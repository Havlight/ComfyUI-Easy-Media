"""Read-only comparison between saved generations and the current timeline."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .h3_native_timing import compile_native_plan, native_task_segments


def _content_fields(value: Any) -> Any:
    """Exclude presentation and materialization fields from dependency identity."""
    if isinstance(value, list):
        return [_content_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _content_fields(item) for key, item in value.items()
                if key not in {'id', 'name', 'color', 'thumbnail', 'selected', 'locked',
                               'media_index', 'shared_media_index', 'shared_reference_copy', 'volume'}}
    return value


def task_content_signature(content: dict[str, Any]) -> dict[str, Any]:
    """Ignore editor-only labels and selected prompt variants' inactive text."""
    variant = str(content.get('user_prompt_variant', 'a')).lower()
    prompt = content.get('user_prompt_b', '') if variant == 'b' else content.get('user_prompt', content.get('text', ''))
    return {key: value for key, value in {
        'task_mode': content.get('task_mode', 'default'),
        'continuity_mode': {'context_swap': 'context_drift', 'context_test': 'context'}.get(content.get('continuity_mode'), content.get('continuity_mode', 'shot')),
        'prompt': str(prompt or '').replace('@', ''),
        'ref_image_size': content.get('ref_image_size', 'match'),
        'images': [_content_fields(image)
                   for image in content.get('images', []) if not image.get('muted')],
    }.items()}


def native_task_fingerprint(info: dict[str, Any], index: int) -> str:
    tasks = native_task_segments(info)
    content = task_content_signature(tasks[index].get('content', {}))
    # Source edits can affect shared references and locks outside a task's visible
    # range. Conservatively invalidate consumers rather than reuse obsolete media.
    sources = [_content_fields({k: v for k, v in track.items() if k in {'type', 'muted', 'solo', 'audio_locked', 'visible', 'volume_db', 'segments'}})
               for track in info.get('tracks', []) if track.get('type') != 'task']
    value = {'content': content, 'sources': sources, 'muted': info.get('muted', False),
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
        if not segment and not manifest.get('h3_native'):
            segment = manifest.get('segments', {}).get(str(index), {})
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
        elif recipe.get('task_fingerprint') and recipe['task_fingerprint'] != native_task_fingerprint(info, index):
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
        if plan.continuity_mode == 'context_masked':
            status = 'retired'
        result.append({'segment_id': plan.segment_id, 'index': index, 'status': status})
    return result
