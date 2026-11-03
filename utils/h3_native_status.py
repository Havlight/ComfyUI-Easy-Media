"""Read-only comparison between saved generations and the current timeline."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .h3_anchor import anchor_signature
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
    anchor = anchor_signature(content)
    return {key: value for key, value in {
        **({'drift_taper': list(taper) if isinstance(taper, tuple) else taper}
           if taper is not None and taper != DEFAULT_DRIFT_TAPER else {}),
        # Every Anchor task carries its settings and method version, so a new method stales them.
        **({'anchor': anchor} if anchor is not None else {}),
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


def _active_record(manifest: dict[str, Any], sid: str) -> dict[str, Any]:
    segment = next((s for s in manifest.get('segments', {}).values() if s.get('segment_id') == sid), {})
    return segment.get('generations', {}).get(str(segment.get('active_generation')), {})


def _record_edited(info: dict[str, Any], plans: list[Any], tasks: list[dict[str, Any]], index: int,
                   meta: dict[str, Any], recipe: dict[str, Any]) -> bool:
    """Whether the task's timing, continuity, content or sources changed since the version was saved."""
    plan = plans[index]
    return (any(meta.get(key) != getattr(plan, key)
                for key in ('start_frame', 'end_frame', 'continuity_mode', 'parent_segment_id', 'anchor_segment_id'))
            or bool(recipe.get('task_fingerprint') and recipe['task_fingerprint'] != native_task_fingerprint(
                info, index, int(recipe.get('task_fingerprint_version', 1))))
            or (recipe.get('task_content') is not None
                and task_content_signature(recipe['task_content']) != task_content_signature(tasks[index].get('content', {}))))


def _upscaled(record: dict[str, Any], recipe: dict[str, Any]) -> bool:
    """Made from a saved first pass: its first stage was not sampled with the rules of its run."""
    return record.get('first_pass_generation', recipe.get('first_pass_generation')) is not None


def _first_stage_changed(recipe: dict[str, Any], effect: dict[str, Any]) -> bool:
    return (recipe.get('segment_loras') or {}).get('first', []) != effect.get('first', [])


def _second_stages(effect: dict[str, Any]) -> dict[str, Any]:
    return {stage: rules for stage, rules in effect.items() if stage != 'first'}


def native_timeline_status(info: dict[str, Any], manifest: dict[str, Any],
                           lora_effects: dict[str, dict[str, Any] | None] | None = None,
                           reuse_first_pass: bool = False) -> list[dict[str, Any]]:
    """Each task's saved result against the timeline. `first_loras_changed` marks a version whose
    first-stage LoRAs differ from the rules now: upscaling its saved first pass keeps them.

    `reuse_first_pass` when the run upscales saved first passes (Dual with use_saved_1st_pass):
    a version upscaled from one then answers for its second-pass LoRAs only, since the run
    would upscale the same first pass again. Any other run samples a new first pass.
    """
    from .h3_lora_preflight import task_dependencies

    plans = compile_native_plan(info)
    tasks = native_task_segments(info)
    dependencies = task_dependencies(info)
    result: list[dict[str, Any]] = []
    states: dict[str, str] = {}
    for index, plan in enumerate(plans):
        record = _active_record(manifest, plan.segment_id)
        meta = record.get('native', {}).get('high', {}).get('metadata', {})
        recipe = record.get('native_recipe', meta.get('recipe', {}))
        status = 'saved'
        effect = lora_effects.get(plan.segment_id) if lora_effects is not None else None
        if not record:
            status = 'missing'
        elif not meta:
            status = 'legacy'
        elif record.get('native_stale'):
            status = 'parent_changed'
        elif _record_edited(info, plans, tasks, index, meta, recipe):
            status = 'edited'
        if status == 'saved' and lora_effects is not None:
            saved = recipe.get('segment_loras', {})
            if effect is None:
                status = 'pending'
            # Upscaling the same saved first pass again only redoes the second pass.
            elif ((_second_stages(effect) != _second_stages(saved)) if reuse_first_pass and _upscaled(record, recipe)
                  else effect != saved):
                status = 'edited'
        if status in {'saved', 'pending'}:
            parent_states = {states.get(plans[parent].segment_id) for parent in dependencies[index]}
            if parent_states - {'saved', 'pending'}:
                status = 'parent_changed'
            elif 'pending' in parent_states:
                status = 'pending'
        states[plan.segment_id] = status
        changed = bool(meta and effect is not None and _first_stage_changed(recipe, effect))
        result.append({'segment_id': plan.segment_id, 'index': index, 'status': status,
                       **({'first_loras_changed': True} if changed else {})})
    return result


def saved_first_pass_edits(info: dict[str, Any], manifest: dict[str, Any], selected: list[int]) -> list[str]:
    """Tasks whose saved first pass no longer matches their timeline content.

    Only the first pass is reused as it is, so second-pass settings and LoRAs may change freely,
    and so may first-stage LoRAs: they apply to the next first pass, not the saved one.
    """
    plans = compile_native_plan(info)
    tasks = native_task_segments(info)
    problems = []
    for index in selected:
        sid = plans[index].segment_id
        record = _active_record(manifest, sid)
        meta = record.get('native', {}).get('high', {}).get('metadata', {})
        if not meta:
            # The source preflight explains a missing or legacy version.
            continue
        recipe = record.get('native_recipe', meta.get('recipe', {}))
        # A version can be stale through its second pass alone; its first pass is checked by itself.
        if _record_edited(info, plans, tasks, index, meta, recipe):
            problems.append(f'Task {index + 1}: its content changed after the first pass. Run it with single again.')
    return problems


def saved_first_pass_lora_changes(info: dict[str, Any], manifest: dict[str, Any], selected: list[int],
                                  lora_effects: dict[str, dict[str, Any] | None]) -> list[int]:
    """1-based tasks whose first-stage LoRA rules changed after their saved first pass, which
    upscaling keeps as it is."""
    plans = compile_native_plan(info)
    changed = []
    for index in selected:
        record = _active_record(manifest, plans[index].segment_id)
        meta = record.get('native', {}).get('high', {}).get('metadata', {})
        effect = lora_effects.get(plans[index].segment_id)
        if meta and effect is not None and _first_stage_changed(record.get('native_recipe', meta.get('recipe', {})), effect):
            changed.append(index + 1)
    return changed


def native_project_versions(manifest: dict[str, Any]) -> dict[str, Any]:
    """Each task's saved versions in timeline order: which is active, how each was made (pass,
    size, seed, the version it upscaled), what it continued from, whether it used locked
    audio, fell back or went stale. Tasks no longer on the timeline keep their versions
    under ``detached``."""
    owners: dict[str, str] = {}
    groups = {'segments': manifest.get('segments', {}), 'detached': manifest.get('detached_segments', {})}
    for segments in groups.values():
        for segment in segments.values():
            for key, generation in segment.get('generations', {}).items():
                for stage in generation.get('native', {}).values():
                    artifact = stage.get('metadata', {}).get('artifact_id')
                    if artifact:
                        # An upscaled version carries its first pass on; name the version that made it.
                        owners.setdefault(artifact, f"{segment.get('segment_id', '')}@{key}")

    def describe(segment: dict[str, Any]) -> dict[str, Any]:
        versions = []
        for key, generation in sorted(segment.get('generations', {}).items(), key=lambda item: int(item[0])):
            metadata = generation.get('native', {}).get('high', {}).get('metadata', {})
            parent = metadata.get('parent_artifact_id')
            recipe = generation.get('native_recipe', metadata.get('recipe', {}))
            dual = generation.get('sampling_pass') == 'second'
            versions.append({
                'version': int(key), 'video': generation.get('video') or generation.get('audio'),
                'sampling_pass': generation.get('sampling_pass'),
                **({'size': f"{recipe['target_width']}x{recipe['target_height']}"} if 'target_width' in recipe else {}),
                **({'first_pass_size': f"{recipe['first_width']}x{recipe['first_height']}"}
                   if dual and 'first_width' in recipe else {}),
                **({'seed': generation['seed']} if 'seed' in generation else {}),
                # override drops the version a first pass was upscaled from.
                **({'upscaled_from': int(generation['first_pass_generation'])}
                   if str(generation.get('first_pass_generation')) in segment.get('generations', {}) else {}),
                **({'keep_1st_pass_audio': generation['keep_1st_pass_audio']} if 'keep_1st_pass_audio' in generation else {}),
                'locked_audio': generation.get('audio_locked') is True,
                'continued_from': owners.get(parent, 'missing') if parent else None,
                'fallback': [item.get('reason') for item in generation.get('fallback_history', [])],
                'stale': generation.get('native_stale') is True,
            })
        return {'task': segment.get('segment_id'), 'active': segment.get('active_generation'), 'versions': versions}

    ordered = sorted(groups['segments'].items(), key=lambda item: int(item[0]))
    return {'width': manifest.get('width'), 'height': manifest.get('height'),
            'tasks': [{'number': int(index) + 1, **describe(segment)} for index, segment in ordered],
            'detached': [describe(segment) for segment in groups['detached'].values()]}
