"""Output-only sample mapping and next-segment ownership of generated overlap."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .h3_native_timing import NativePlanError, round_ratio, sample_at_frame


def raw_audio_window(waveform: np.ndarray, metadata: dict[str, Any], rate: int,
                     start_frame: int, end_frame: int) -> np.ndarray:
    origin = round_ratio(metadata['audio_origin_units'] * rate, 120)
    start, end = sample_at_frame(start_frame, rate) - origin, sample_at_frame(end_frame, rate) - origin
    if start < 0 or end > len(waveform):
        raise NativePlanError('ASSEMBLY_RANGE', 'Saved raw audio does not cover the requested output view.')
    return waveform[start:end].copy()


def incoming_overlap(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    return bool(parent.get('source_kind') == child.get('source_kind') == 'native_sampler'
                and child.get('parent_artifact_id') == parent.get('artifact_id')
                and parent['end_frame'] == child['start_frame'] and child['context_frames'])


def build_native_audio_views(segments: list[dict[str, Any]], directory: Path) -> None:
    """Attach bounded PCM views to the existing compositor. No latent is modified.

    Reordered, trimmed, disabled and mismatched-version clips still work; overlap
    ownership applies only where the original adjacent seam survives intact.
    """
    for index, segment in enumerate(segments):
        metadata = segment.get('native_metadata')
        if not metadata or metadata['source_kind'] != 'native_sampler' or segment.get('audio_locked'):
            continue
        waveform, rate = sf.read(segment['raw_audio_source'], dtype='float32', always_2d=True)
        count = segment['end_frame'] - segment['start_frame']
        start = metadata['start_frame'] + segment.get('source_start_frame', 0)
        end = start + count
        view = raw_audio_window(waveform, metadata, rate, start, end)
        if index + 1 < len(segments):
            following = segments[index + 1]
            child = following.get('native_metadata')
            if (child and not following.get('source_start_frame', 0)
                    and end == metadata['end_frame'] and incoming_overlap(metadata, child)):
                child_waveform, child_rate = sf.read(following['raw_audio_source'], dtype='float32', always_2d=True)
                if child_rate != rate:
                    raise NativePlanError('ASSEMBLY_RATE', 'Adjacent native audio sources use different sample rates.')
                child_origin = round_ratio(child['audio_origin_units'] * rate, 120)
                overlap_start = max(sample_at_frame(start, rate), sample_at_frame(child['raw_start_frame'], rate), child_origin)
                # Drift/Masked release belongs to the incoming audio prefix.
                overlap_end = sample_at_frame(end, rate)
                replacement = child_waveform[overlap_start - child_origin:overlap_end - child_origin]
                if len(replacement) != overlap_end - overlap_start:
                    raise NativePlanError('ASSEMBLY_RANGE', 'Incoming raw audio does not cover its seam.')
                view[-len(replacement):] = replacement
        target = sample_at_frame(segment['end_frame'], rate) - sample_at_frame(segment['start_frame'], rate)
        difference = target - len(view)
        if abs(difference) > 1:
            raise NativePlanError('ASSEMBLY_CLOCK', 'Output and source clocks differ by more than one sample.')
        if difference > 0:
            view = np.concatenate((view, view[-1:]), axis=0)
        elif difference < 0:
            view = view[:target]
        path = directory / f'view-{index}.wav'
        sf.write(path, view, rate, subtype='FLOAT')
        segment.update(audio_source=str(path), audio_exact_rate=rate,
                       audio_source_start_sample=0, audio_source_end_sample=target)
