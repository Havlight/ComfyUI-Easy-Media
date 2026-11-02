"""Whole-run source planning, immutable version selection and stage contracts.

This module never samples, encodes or changes the project manifest. A run state
holds snapshots of existing versions and versions published by this run only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from .h3_native import new_native_metadata, validate_native_context_contract, validate_native_latent
from .h3_native_artifacts import (
    file_checksum, load_native_latent, native_child_path, native_parent_ids, refresh_native_dependencies,
)
from .h3_native_sources import read_delivered_seed
from .h3_native_timing import NativePlanError, NativeTaskPlan, audio_clock


def native_stage_layout(recipe: dict[str, Any]) -> dict[str, tuple[str, tuple[int, int]]]:
    mode = recipe['sampling_mode']
    size = (recipe['target_height'] // 16, recipe['target_width'] // 16)
    first = (recipe['first_height'] // 16, recipe['first_width'] // 16)
    stages = {'high': ({'single': 'single_final', 'dual': 'dual_high_final',
                       'selflift': 'selflift_high_final'}[mode], size)}
    if mode == 'dual':
        stages['low'] = ('dual_low_prediction', first)
    elif mode == 'selflift':
        stages['low'] = ('selflift_low_prediction', tuple(max(2, round(n * recipe['lowres_scale'] / 2) * 2) for n in size))
    return stages


def parent_stage_labels(recipe: dict[str, Any], label: str) -> tuple[str, ...]:
    """Where a predecessor's source for one of this run's stages may come from.

    A single run is a Dual first pass at the editor size, so it continues from a Dual
    predecessor's first pass when that predecessor's final stage has another size.
    """
    return ('high', 'low') if recipe['sampling_mode'] == 'single' and label == 'high' else (label,)


# Where a version keeps its first pass, by the pass that produced the version
# ('first': checkpoints of the former first-pass-only preview).
FIRST_PASS_LABELS = {'single': 'high', 'second': 'low', 'first': 'high'}


def saved_first_pass(directory: Path, manifest: dict[str, Any], plan: NativeTaskPlan,
                     recipe: dict[str, Any], valid_ids: set[str]) -> dict[str, Any]:
    """The active version's first pass, which a Dual run upscales instead of sampling again.

    ``valid_ids`` are the artifacts of the valid active versions. Only the first pass must
    still continue from them: a version whose second pass went stale keeps a usable first pass.
    """
    segment = next((s for s in manifest.get('segments', {}).values() if s.get('segment_id') == plan.segment_id), {})
    generation = str(segment.get('active_generation'))
    record = segment.get('generations', {}).get(generation)
    if not isinstance(record, dict):
        raise NativePlanError('SAVED_FIRST_PASS', 'There is no saved first pass to upscale. Run this task '
                              'with single first, or turn off use_saved_1st_pass.', plan.segment_id)
    label = FIRST_PASS_LABELS.get(record.get('sampling_pass'))
    descriptor = record.get('native', {}).get(label) if label else None
    if descriptor is None:
        raise NativePlanError('SAVED_FIRST_PASS_MODE', 'Its active version (sampling pass '
                              f"{record.get('sampling_pass', 'unknown')}) has no first pass to upscale. Select a "
                              'single or dual version in MultiTrack Project Video Combine, or run this task '
                              'with single.', plan.segment_id)
    if not set(native_parent_ids(descriptor['metadata'])) <= valid_ids:
        raise NativePlanError('SAVED_FIRST_PASS_STALE', 'Its first pass continued from a version of the '
                              'previous task that is no longer selected. Select that version again, or run '
                              'this task with single again.', plan.segment_id)
    latent = load_native_latent(directory, descriptor)
    meta = validate_native_latent(latent)
    size = tuple(latent['samples'].unbind()[0].shape[-2:])
    del latent
    if meta['stage'] not in {'single_final', 'dual_low_prediction'}:
        raise NativePlanError('SAVED_FIRST_PASS_MODE', f"Its active version holds a {meta['stage']} stage, not a "
                              'first pass. Run this task with single.', plan.segment_id)
    if size != (recipe['first_height'] // 16, recipe['first_width'] // 16):
        raise NativePlanError('SAVED_FIRST_PASS_SIZE', f'The saved first pass is {size[1] * 16}x{size[0] * 16}, '
                              f"but the editor is {recipe['first_width']}x{recipe['first_height']}. Run this task "
                              'with single again, or set the editor size back.', plan.segment_id)
    if any(meta[key] != getattr(plan, key) for key in ('start_frame', 'end_frame', 'continuity_mode', 'parent_segment_id')):
        raise NativePlanError('SAVED_FIRST_PASS_EDITED', 'Its timing or continuity changed after the first pass. '
                              'Run this task with single again.', plan.segment_id)
    # The LoRAs the first pass was sampled with, which the upscaled version records as its own.
    source = record.get('native_recipe') or meta['recipe']
    loras = {key: (source.get(key) or {}).get('first') for key in ('segment_loras', 'segment_lora_files')}
    return {'generation': generation, 'descriptor': deepcopy(descriptor), 'meta': meta,
            'seed': int(record.get('seed', meta['recipe'].get('seed', 0))),
            'loras': {key: value for key, value in loras.items() if value}}


def generation_snapshot(manifest: dict[str, Any], sid: str) -> dict[str, Any]:
    matches = [(i, s) for i, s in manifest.get('segments', {}).items() if s.get('segment_id') == sid]
    if not matches:
        raise NativePlanError('SOURCE_MISSING', 'Generate or restore the required predecessor first.', sid)
    saved_index, segment = matches[0]
    version = str(segment.get('active_generation'))
    record = segment.get('generations', {}).get(version)
    if not isinstance(record, dict) or record.get('native_stale'):
        raise NativePlanError('STALE_SOURCE', 'The predecessor is missing or depends on a replaced version; regenerate it.', sid)
    return {'segment_index': int(saved_index), 'generation': version, 'record': deepcopy(record)}


def run_source(state: dict[str, Any], sid: str) -> dict[str, Any]:
    source = (state['produced'] if sid in state['planned_ids'] else state['existing']).get(sid)
    if source is None:
        raise NativePlanError('RUN_SOURCE', 'The declared predecessor has not been published by this run.', sid)
    return source


def validate_parent_range(meta: dict[str, Any], parent: NativeTaskPlan) -> None:
    if any(meta[key] != getattr(parent, key) for key in ('start_frame', 'end_frame')):
        raise NativePlanError('PARENT_RANGE', 'The predecessor timeline changed; regenerate it before continuing.', parent.segment_id)


def seed_contract(parent: NativeTaskPlan, stage: str, recipe: dict[str, Any], imported: bool,
                  high_clock: dict[str, int] | None = None) -> dict[str, Any]:
    seed = replace(parent, continuity_mode='shot', parent_segment_id=None, context_frames=0,
                   raw_start_frame=parent.end_frame - 39, raw_frames=39, passthrough=True)
    meta = new_native_metadata(seed, 'imported_seed' if imported else stage, recipe,
                               source_kind='imported_seed' if imported else 'rebuilt_generated')
    if high_clock:
        meta['audio_origin_units'] = high_clock['audio_origin_units']
    return meta


def preflight_native_sources(directory: Path, manifest: dict[str, Any], plans: list[NativeTaskPlan],
                             selected: list[int], recipe: dict[str, Any],
                             previous_frames: set[int], contents: list[dict[str, Any]],
                             passthrough: set[int]) -> dict[str, Any]:
    valid_ids = refresh_native_dependencies(manifest)
    state: dict[str, Any] = {'existing': {}, 'produced': {}, 'first_pass': {},
                             'planned_ids': [plans[i].segment_id for i in selected]}
    contracts: dict[str, dict[str, tuple[dict[str, Any], tuple[int, int]]]] = {}
    errors: list[str] = []
    layout = native_stage_layout(recipe) if recipe['sampling_mode'] != 'passthrough' else {}
    use_saved_first_pass = bool(recipe.get('use_saved_1st_pass'))

    def existing(sid: str) -> dict[str, Any]:
        if sid not in state['existing']:
            state['existing'][sid] = generation_snapshot(manifest, sid)
        return state['existing'][sid]

    for index in selected:
        plan = plans[index]
        try:
            if index in previous_frames:
                if index == 0 or recipe['target_width'] == recipe['target_height'] == 32:
                    raise NativePlanError('PREVIOUS_FRAME', 'A previous-frame reference requires a preceding video task.', plan.segment_id)
                if len([im for im in contents[index].get('images', []) if not im.get('muted')]) > 9:
                    raise NativePlanError('IMAGE_SLOTS', 'At most nine image references are supported.', plan.segment_id)
                parent = plans[index - 1]
                if parent.segment_id not in contracts:
                    source = existing(parent.segment_id)
                    record = source['record']
                    if not record.get('video'):
                        raise NativePlanError('PREVIOUS_FRAME', 'The predecessor needs a completed video.', plan.segment_id)
                    from .h3_previous_frame import read_video_frames, video_frame_count
                    path = native_child_path(directory, record['video'])
                    if video_frame_count(path) != parent.end_frame - parent.start_frame:
                        raise NativePlanError('PARENT_RANGE', 'The previous-frame video has different timing; regenerate it.', plan.segment_id)
                    read_video_frames(path, video_frame_count(path) - 1, 1, fps=None)
                    source['video_sha256'] = file_checksum(path)
            if index in passthrough:
                if plan.end_frame - plan.start_frame < 39:
                    raise NativePlanError('SEED_SHORT', 'An imported seed needs at least 39 frames.', plan.segment_id)
                size = (recipe['target_height'] // 16, recipe['target_width'] // 16)
                contracts[plan.segment_id] = {'high': (seed_contract(plan, 'imported_seed', recipe, True), size)}
                # Seed creates the mode-specific low source at initial ingress.
                if 'low' in layout:
                    contracts[plan.segment_id]['low'] = (seed_contract(plan, 'imported_seed', recipe, True), layout['low'][1])
                continue
            first_pass = None
            if use_saved_first_pass:
                first_pass = saved_first_pass(directory, manifest, plan, recipe, valid_ids)
                # The previous task's new version carries only its own saved first pass on.
                parent_ids = set(native_parent_ids(first_pass['meta']))
                if parent_ids and index - 1 in passthrough:
                    raise NativePlanError('SAVED_FIRST_PASS_CHAIN', f'Task {index} is passthrough, which every run '
                                          'imports again, so the first pass that continues from it would be stale '
                                          f'afterwards. Start the run at task {index + 1}.', plan.segment_id)
                if parent_ids and index - 1 in selected:
                    carried = state['first_pass'].get(plans[index - 1].segment_id)
                    allowed = {carried['meta']['artifact_id']} if carried else set()
                    if not parent_ids <= allowed:
                        raise NativePlanError('SAVED_FIRST_PASS_CHAIN', 'Its saved first pass does not continue from '
                                              f'task {index}, whose saved first pass this run upscales, so it would be '
                                              f'stale afterwards. Upscale task {index} first, then run this task '
                                              'with single again.', plan.segment_id)
                state['first_pass'][plan.segment_id] = first_pass
            parents: dict[str, dict[str, Any]] = {}
            high_clock = None
            if plan.parent_segment_id:
                parent = plans[index - 1]
                planned = contracts.get(parent.segment_id)
                source = None if planned is not None else existing(parent.segment_id)
                record = source['record'] if source else {}
                # Content edits leave the saved latent usable; the run logs them.
                imported = False
                checked_video = False
                for label, (stage, size) in layout.items():
                    if first_pass is not None and label == 'low':
                        continue
                    for source_label in parent_stage_labels(recipe, label):
                        candidate = planned.get(source_label) if planned is not None else None
                        descriptor = record.get('native', {}).get(source_label)
                        if descriptor is not None:
                            saved = load_native_latent(directory, descriptor)
                            meta = validate_native_latent(saved)
                            candidate = (meta, tuple(saved['samples'].unbind()[0].shape[-2:]))
                            del saved
                        if candidate is None:
                            continue
                        meta, actual_size = candidate
                        validate_parent_range(meta, parent)
                        if source_label == label == 'high':
                            imported = meta['source_kind'] == 'imported_seed'
                        try:
                            _, _, clock = validate_native_context_contract(meta, plan, stage, size, actual_size)
                            if high_clock is not None and clock['audio_origin_units'] != high_clock['audio_origin_units']:
                                raise NativePlanError('STAGE_CLOCK', 'High and low sources have different audio clocks.')
                        except NativePlanError as error:
                            if error.code not in {'STAGE_MISMATCH', 'SIZE_MISMATCH', 'STAGE_CLOCK'}:
                                raise
                            continue
                        parents[label] = meta
                        high_clock = high_clock or clock
                        break
                    if label in parents:
                        continue
                    if first_pass is not None and not imported:
                        raise NativePlanError('SAVED_FIRST_PASS_PARENT', f'It continues from task {index}, whose active '
                                              'version has no second pass at this size. Upscale task '
                                              f'{index} first, or include it in this run.', plan.segment_id)
                    if not imported and not recipe['allow_vae_fallback']:
                        raise NativePlanError('VAE_FALLBACK_REQUIRED', f'The predecessor has no compatible {label} native stage; regenerate it or allow VAE fallback.', plan.segment_id)
                    if parent.end_frame - parent.start_frame < 39 or (source and not record.get('video')):
                        raise NativePlanError('SOURCE_ADAPTER', 'A source rebuild needs a completed video containing at least 39 delivered frames.', plan.segment_id)
                    if source and not checked_video:
                        path = native_child_path(directory, record['video'])
                        read_delivered_seed(path, parent.end_frame - parent.start_frame)
                        source['video_sha256'] = file_checksum(path)
                        checked_video = True
                    meta = seed_contract(parent, stage, recipe, imported, high_clock if label == 'low' else None)
                    parents[label] = meta
                    high_clock = high_clock or audio_clock(plan, meta)
            contracts[plan.segment_id] = {
                label: (first_pass['meta'], size) if first_pass is not None and label == 'low'
                else (new_native_metadata(plan, stage, recipe, parents.get(label)), size)
                for label, (stage, size) in layout.items()}
        except (NativePlanError, OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
            errors.append(f'Task {index + 1}: {error}')
    if errors:
        raise NativePlanError('PREFLIGHT', '\n' + '\n'.join(errors))
    return state
