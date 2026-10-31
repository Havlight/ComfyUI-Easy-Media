"""Read-only comparison between saved generations and the current timeline."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .h3_drift_taper import DEFAULT_DRIFT_TAPER, drift_taper
from .h3_native_timing import CONTEXT_FRAMES, NativePlanError, compile_native_plan, native_task_segments
from .h3_references import plan_task_references, shared_reference_clip


def _content_fields(value: Any) -> Any:
    """Exclude presentation and materialization fields from dependency identity."""
    if isinstance(value, list):
        return [_content_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _content_fields(item) for key, item in value.items()
                if key not in {'id', 'name', 'color', 'thumbnail', 'selected', 'locked', 'media_index',
                               'shared_media_index', 'shared_reference_copy', 'volume', 'has_audio'}}
    return value


def task_content_signature(content: dict[str, Any]) -> dict[str, Any]:
    """Ignore editor-only labels and selected prompt variants' inactive text."""
    variant = str(content.get('user_prompt_variant', 'a')).lower()
    prompt = content.get('user_prompt_b', '') if variant == 'b' else content.get('user_prompt', content.get('text', ''))
    # Only a non-default Drift taper changes the signature, so existing versions stay current.
    try:
        taper = drift_taper(content)
    except NativePlanError:
        taper = repr(tuple(content.get(key) for key in ('drift_taper_steps', 'drift_taper_floor', 'drift_taper_low_only')))
    return {key: value for key, value in {
        **({'drift_taper': list(taper) if isinstance(taper, tuple) else taper}
           if taper is not None and taper != DEFAULT_DRIFT_TAPER else {}),
        'task_mode': content.get('task_mode', 'default'),
        'continuity_mode': content.get('continuity_mode', 'shot'),
        'prompt': str(prompt or '').replace('@', ''),
        'ref_image_size': content.get('ref_image_size', 'match'),
        'images': [_content_fields(image)
                   for image in content.get('images', []) if not image.get('muted')],
    }.items()}


# Saved versions record which fingerprint they used; those without one predate version 2.
TASK_FINGERPRINT_VERSION = 2


def _window_part(segment: dict[str, Any], window: tuple[int, int]) -> dict[str, Any]:
    """The part of a clip inside the window, and where that part starts in its source."""
    start, end = int(segment.get('start_frame', 0)), int(segment.get('end_frame', 0))
    origin = int(segment.get('origin_start_frame', start))
    part_start, part_end = max(start, window[0]), min(end, window[1])
    rest = {key: value for key, value in segment.items() if key not in {'start_frame', 'end_frame', 'origin_start_frame'}}
    return {**rest, 'window_start': part_start, 'window_end': part_end, 'source_start': part_start - origin}


def native_task_fingerprint(info: dict[str, Any], index: int, version: int = TASK_FINGERPRINT_VERSION) -> str:
    """Hash a task's content, its reference plan and the media tracks that can change it.

    A track counts when the task's reference plan uses it or when it is audio-locked
    and overlaps the task. Only its clips that reach the task's window, including the
    39-frame context before it and any unfitted reference after it, and its shared clip
    are hashed, so edits elsewhere on the timeline leave the task saved. The plan covers
    the task's own uploads and the order, muting and fitting of every reference.

    Version 2 hashes only the part of each clip inside the window, so trimming or
    extending a clip beyond it (a song's end, a dance cut at a later task) changes
    nothing; version 1 hashes whole clips and is kept for versions saved with it.
    """
    tasks = native_task_segments(info)
    task = tasks[index]
    start, end = int(task['start_frame']), int(task['end_frame'])
    next_start = int(tasks[index + 1]['start_frame']) if index + 1 < len(tasks) else None
    media = [track for track in info.get('tracks', []) if track.get('type') in {'audio', 'video'}]
    plan = plan_task_references(media, task, next_start,
                                audio_only=(info.get('width'), info.get('height')) == (32, 32),
                                frame_rate=float(info.get('frame_rate', 24)))
    references = (*plan.videos, *plan.audios)
    window = (start - CONTEXT_FRAMES, max(end, next_start or end, *(
        ref.start_frame + ref.frames for ref in references if ref.kind == 'track')))
    referenced = {ref.track_id for ref in references if ref.kind != 'upload'}
    sources = []
    for track in media:
        shared = shared_reference_clip(track)
        clips = [segment for segment in track.get('segments', []) if segment is shared or (
            int(segment.get('start_frame', 0)) < window[1] and int(segment.get('end_frame', 0)) > window[0])]
        if version >= 2:
            # A shared clip is referenced whole wherever it sits.
            clips = [segment if segment is shared else _window_part(segment, window) for segment in clips]
        if str(track.get('id', '')) in referenced or (track.get('audio_locked') is True and clips):
            sources.append(_content_fields({**{k: v for k, v in track.items()
                                               if k in {'type', 'muted', 'solo', 'audio_locked', 'soundtrack', 'visible', 'volume_db'}},
                                            'segments': clips}))
    value = {'content': task_content_signature(task.get('content', {})), 'sources': sources,
             'references': [{key: item for key, item in ref.as_dict().items() if key != 'key'} for ref in references],
             'muted': info.get('muted', False),
             'solo': any(track.get('solo') is True for track in media),
             'volume_db': float(info.get('volume_db', 0))}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def native_timeline_status(info: dict[str, Any], manifest: dict[str, Any],
                           lora_effects: dict[str, dict[str, Any] | None] | None = None) -> list[dict[str, Any]]:
    from .h3_lora_preflight import task_dependencies

    plans = compile_native_plan(info)
    tasks = native_task_segments(info)
    dependencies = task_dependencies(info)
    result: list[dict[str, Any]] = []
    states: dict[str, str] = {}
    for index, plan in enumerate(plans):
        segment = next((s for s in manifest.get('segments', {}).values() if s.get('segment_id') == plan.segment_id), {})
        record = segment.get('generations', {}).get(str(segment.get('active_generation')), {})
        meta = record.get('native', {}).get('high', {}).get('metadata', {})
        recipe = record.get('native_recipe', meta.get('recipe', {}))
        status = 'saved'
        if not record:
            status = 'missing'
        elif not meta:
            status = 'legacy'
        elif record.get('native_stale'):
            status = 'parent_changed'
        elif any(meta.get(key) != getattr(plan, key) for key in ('start_frame', 'end_frame', 'continuity_mode', 'parent_segment_id')):
            status = 'edited'
        elif recipe.get('task_fingerprint') and recipe['task_fingerprint'] != native_task_fingerprint(
                info, index, int(recipe.get('task_fingerprint_version', 1))):
            status = 'edited'
        elif recipe.get('task_content') is not None and task_content_signature(recipe['task_content']) != task_content_signature(tasks[index].get('content', {})):
            status = 'edited'
        if status == 'saved' and lora_effects is not None:
            effect = lora_effects.get(plan.segment_id)
            if effect is None:
                status = 'pending'
            elif effect != recipe.get('segment_loras', {}):
                status = 'edited'
        if status in {'saved', 'pending'}:
            parent_states = {states.get(plans[parent].segment_id) for parent in dependencies[index]}
            if parent_states - {'saved', 'pending'}:
                status = 'parent_changed'
            elif 'pending' in parent_states:
                status = 'pending'
        states[plan.segment_id] = status
        result.append({'segment_id': plan.segment_id, 'index': index, 'status': status})
    return result


def native_project_versions(manifest: dict[str, Any]) -> dict[str, Any]:
    """Each task's saved versions in timeline order: which is active, what it continued from,
    whether it used locked audio, fell back or went stale. Tasks no longer on the timeline
    keep their versions under ``detached``."""
    owners: dict[str, str] = {}
    groups = {'segments': manifest.get('segments', {}), 'detached': manifest.get('detached_segments', {})}
    for segments in groups.values():
        for segment in segments.values():
            for key, generation in segment.get('generations', {}).items():
                for stage in generation.get('native', {}).values():
                    artifact = stage.get('metadata', {}).get('artifact_id')
                    if artifact:
                        owners[artifact] = f"{segment.get('segment_id', '')}@{key}"

    def describe(segment: dict[str, Any]) -> dict[str, Any]:
        versions = []
        for key, generation in sorted(segment.get('generations', {}).items(), key=lambda item: int(item[0])):
            metadata = generation.get('native', {}).get('high', {}).get('metadata', {})
            parent = metadata.get('parent_artifact_id')
            versions.append({
                'version': int(key), 'video': generation.get('video') or generation.get('audio'),
                'sampling_pass': generation.get('sampling_pass'), 'locked_audio': generation.get('audio_locked') is True,
                'continued_from': owners.get(parent, 'missing') if parent else None,
                'fallback': [item.get('reason') for item in generation.get('fallback_history', [])],
                'stale': generation.get('native_stale') is True,
            })
        return {'task': segment.get('segment_id'), 'active': segment.get('active_generation'), 'versions': versions}

    ordered = sorted(groups['segments'].items(), key=lambda item: int(item[0]))
    return {'width': manifest.get('width'), 'height': manifest.get('height'),
            'tasks': [{'number': int(index) + 1, **describe(segment)} for index, segment in ordered],
            'detached': [describe(segment) for segment in groups['detached'].values()]}
