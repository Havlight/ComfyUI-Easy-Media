"""External audio lock on the inherited native audio clock, after context copy."""
from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F

from ..modules.motion_context.core import _noise_mask_streams, _official_nested_tensor, _streams_from_latent
from .h3_native_timing import NativePlanError, round_ratio


def lock_native_audio(latent: dict[str, Any], metadata: dict[str, Any], audio: dict[str, Any],
                      audio_vae: Any, intervals: list[list[int]]) -> dict[str, Any]:
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
