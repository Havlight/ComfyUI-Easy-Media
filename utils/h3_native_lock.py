"""Native video lock validation and external audio lock on the inherited clock."""
from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F

from ..modules.motion_context.core import _noise_mask_streams, _official_nested_tensor, _streams_from_latent
from .h3_native_timing import NativePlanError, round_ratio, sample_at_frame


def validate_native_locked_video(video: Any, expected_frames: int) -> None:
    """Check the actual raw window without materializing file-backed RGB tensors."""
    import av
    from comfy_api.latest import InputImpl

    range_error = 'The locked video must cover the native raw window at 24 fps without padding or stretching.'
    # The generic VIDEO.get_stream_source() can encode a whole in-memory video.
    # Only use streaming for the concrete file adapter; preserve other adapters.
    if not isinstance(video, InputImpl.VideoFromFile):
        components = video.get_components()
        if components.images.shape[0] != expected_frames or float(components.frame_rate) != 24:
            raise NativePlanError('LOCK_VIDEO_RANGE', range_error)
        return

    try:
        start_time, duration = video.get_active_trim_window()
        with av.open(video.get_stream_source(), mode='r') as container:
            if not container.streams.video:
                raise NativePlanError('LOCK_VIDEO_SOURCE', 'The locked video has no readable video stream.')
            stream = container.streams.video[0]
            # Match VideoFromFile.get_components() FPS and trim boundaries.
            if float(stream.average_rate or 1) != 24:
                raise NativePlanError('LOCK_VIDEO_RANGE', range_error)
            stream.thread_count = 1
            start_pts = int(start_time / stream.time_base)
            end_pts = int((start_time + duration) / stream.time_base) if duration else None
            if start_pts:
                container.seek(start_pts, stream=stream)
            count = 0
            for frame in container.decode(stream):
                if frame.pts is None:
                    raise NativePlanError('LOCK_VIDEO_DECODE', 'The locked video has a frame without a timestamp.')
                if frame.pts < start_pts:
                    continue
                if end_pts is not None and frame.pts >= end_pts:
                    break
                count += 1
                if count > expected_frames:
                    raise NativePlanError('LOCK_VIDEO_RANGE', range_error)
            # Header frame counts and duration estimates can hide a short decode.
            # Iterate actual frames, retaining only the decoder's bounded buffers.
            if count != expected_frames:
                raise NativePlanError('LOCK_VIDEO_RANGE', range_error)
    except NativePlanError:
        raise
    except (av.error.FFmpegError, OSError, ValueError, RuntimeError) as error:
        raise NativePlanError('LOCK_VIDEO_DECODE', f'The locked video could not be decoded: {error}') from error


def native_locked_audio_view(metadata: dict[str, Any], audio: dict[str, Any] | None) -> dict[str, Any] | None:
    """Crop the already mixed external timeline at cumulative sample endpoints."""
    if audio is None:
        return None
    rate = int(audio['sample_rate'])
    start = sample_at_frame(metadata['start_frame'], rate)
    end = sample_at_frame(metadata['end_frame'], rate)
    waveform = audio['waveform'][..., start:end]
    # Match the declared external lock's existing silence policy beyond source.
    waveform = F.pad(waveform, (0, end - start - waveform.shape[-1]))
    return {**audio, 'waveform': waveform}


def lock_native_audio(latent: dict[str, Any], metadata: dict[str, Any], audio: dict[str, Any] | None,
                      audio_vae: Any, intervals: list[list[int]]) -> dict[str, Any]:
    # A locked video can have no audio stream. Match the existing audio-lock
    # selector: retain generated audio without encoding an invented source.
    if audio is None:
        return latent
    video, base_audio = _streams_from_latent(latent)
    ticks = base_audio.shape[-1]
    if ticks != metadata['audio_ticks']:
        raise NativePlanError('LOCK_CLOCK', 'Audio lock and sampler canvas use different clocks.')
    rate = int(getattr(audio_vae, 'audio_sample_rate', 32000))
    if rate % 40:
        raise NativePlanError('LOCK_ADAPTER', 'Audio VAE rate must contain an integer number of samples per tick.')
    waveform = audio['waveform'][:1]
    if audio['sample_rate'] != rate:
        import torchaudio.functional

        waveform = torchaudio.functional.resample(waveform, int(audio['sample_rate']), rate)
    origin = round_ratio(metadata['audio_origin_units'] * rate, 120)
    length = ticks * (rate // 40)
    window = waveform[..., max(0, origin):max(0, origin + length)]
    window = F.pad(window, (max(0, -origin), max(0, origin + length - waveform.shape[-1])))
    if window.shape[-1] != length:
        raise NativePlanError('LOCK_RANGE', 'External audio cannot cover the declared lock window.')
    encoded = audio_vae.encode(window.movedim(1, -1).contiguous())
    if encoded.shape != base_audio.shape:
        raise NativePlanError('LOCK_GRID', 'The audio VAE returned an incompatible lock grid; encoded audio is never stretched.')
    # A tick intersects the locked source interval. Encoding the whole raw
    # window retains waveform context around boundary ticks, without shifting
    # the source by the continuation prefix.
    starts = torch.arange(ticks, device=base_audio.device) * 3 + metadata['audio_origin_units']
    locked = torch.zeros(ticks, dtype=torch.bool, device=base_audio.device)
    for start, end in intervals:
        locked |= (starts < end * 5) & (starts + 3 > start * 5)
    selected = locked.reshape(1, 1, 1, -1)
    samples = torch.where(selected, encoded.to(base_audio), base_audio)
    video_mask, audio_mask = _noise_mask_streams(latent)
    video_mask = torch.ones_like(video) if video_mask is None else video_mask
    audio_mask = torch.ones_like(base_audio) if audio_mask is None else audio_mask
    audio_mask = torch.where(selected, torch.zeros_like(audio_mask), audio_mask)
    return {**latent, 'samples': _official_nested_tensor((video, samples)),
            'noise_mask': _official_nested_tensor((video_mask, audio_mask))}
