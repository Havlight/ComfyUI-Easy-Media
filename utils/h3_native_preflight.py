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
from .h3_native_artifacts import file_checksum, load_native_latent, native_child_path, refresh_native_dependencies
from .h3_native_sources import read_delivered_seed
from .h3_native_timing import NativePlanError, NativeTaskPlan, audio_clock


def native_stage_layout(recipe: dict[str, Any]) -> dict[str, tuple[str, tuple[int, int]]]:
    mode = recipe['sampling_mode']
    size = (recipe['target_height'] // 16, recipe['target_width'] // 16)
    first = (recipe['first_height'] // 16, recipe['first_width'] // 16)
    if recipe.get('first_pass_only'):
        return {'high': ('dual_low_prediction', first), 'low': ('dual_low_prediction', first)}
    stages = {'high': ({'single': 'single_final', 'dual': 'dual_high_final',
                       'selflift': 'selflift_high_final'}[mode], size)}
    if mode == 'dual':
        stages['low'] = ('dual_low_prediction', first)
    elif mode == 'selflift':
        stages['low'] = ('selflift_low_prediction', tuple(max(2, round(n * recipe['lowres_scale'] / 2) * 2) for n in size))
    return stages


def generation_snapshot(manifest: dict[str, Any], sid: str, index: int) -> dict[str, Any]:
    matches = [(i, s) for i, s in manifest.get('segments', {}).items() if s.get('segment_id') == sid]
    if not matches and not manifest.get('h3_native'):
        matches = [(str(index), manifest.get('segments', {}).get(str(index), {}))]
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
    refresh_native_dependencies(manifest)
    state: dict[str, Any] = {'existing': {}, 'produced': {},
                             'planned_ids': [plans[i].segment_id for i in selected]}
    contracts: dict[str, dict[str, tuple[dict[str, Any], tuple[int, int]]]] = {}
    errors: list[str] = []
    layout = native_stage_layout(recipe) if recipe['sampling_mode'] != 'passthrough' else {}

    def existing(sid: str, index: int) -> dict[str, Any]:
        if sid not in state['existing']:
            state['existing'][sid] = generation_snapshot(manifest, sid, index)
        return state['existing'][sid]

    for index in selected:
        plan = plans[index]
        try:
            if plan.continuity_mode == 'context_masked' and index not in passthrough:
                raise NativePlanError('METHOD_RETIRED', 'Masked is retired. Choose Context or Drift before generating.', plan.segment_id)
            if index in previous_frames:
                if index == 0 or recipe['target_width'] == recipe['target_height'] == 32:
                    raise NativePlanError('PREVIOUS_FRAME', 'A previous-frame reference requires a preceding video task.', plan.segment_id)
                if len([im for im in contents[index].get('images', []) if not im.get('muted')]) > 9:
                    raise NativePlanError('IMAGE_SLOTS', 'At most nine image references are supported.', plan.segment_id)
                parent = plans[index - 1]
                if parent.segment_id not in contracts:
                    source = existing(parent.segment_id, index - 1)
                    record = source['record']
                    if not record.get('video') or record.get('sampling_pass') == 'first':
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
            parents: dict[str, dict[str, Any]] = {}
            high_clock = None
            if plan.parent_segment_id:
                parent = plans[index - 1]
                planned = contracts.get(parent.segment_id)
                source = None if planned is not None else existing(parent.segment_id, index - 1)
                record = source['record'] if source else {}
                source_recipe = record.get('native_recipe', {})
                if source_recipe.get('task_content') is not None and source_recipe['task_content'] != contents[index - 1]:
                    raise NativePlanError('PARENT_EDITED', 'The predecessor content changed; regenerate it before continuing.', plan.segment_id)
                imported = False
                checked_video = False
                for label, (stage, size) in layout.items():
                    candidate = planned.get(label) if planned is not None else None
                    descriptor = record.get('native', {}).get(label)
                    if descriptor is not None:
                        saved = load_native_latent(directory, descriptor)
                        meta = validate_native_latent(saved)
                        candidate = (meta, tuple(saved['samples'].unbind()[0].shape[-2:]))
                        del saved
                    if candidate is not None:
                        meta, actual_size = candidate
                        validate_parent_range(meta, parent)
                        if label == 'high':
                            imported = meta['source_kind'] == 'imported_seed'
                        try:
                            _, _, clock = validate_native_context_contract(meta, plan, stage, size, actual_size)
                            if high_clock is not None and clock['audio_origin_units'] != high_clock['audio_origin_units']:
                                raise NativePlanError('STAGE_CLOCK', 'High and low sources have different audio clocks.')
                            parents[label] = meta
                            high_clock = high_clock or clock
                            continue
                        except NativePlanError as error:
                            if error.code not in {'STAGE_MISMATCH', 'SIZE_MISMATCH', 'STAGE_CLOCK'}:
                                raise
                    if not imported and not recipe['allow_vae_fallback']:
                        raise NativePlanError('VAE_FALLBACK_REQUIRED', f'The predecessor has no compatible {label} native stage; regenerate it or allow VAE fallback.', plan.segment_id)
                    if parent.end_frame - parent.start_frame < 39 or (source and (not record.get('video') or record.get('sampling_pass') == 'first')):
                        raise NativePlanError('SOURCE_ADAPTER', 'A source rebuild needs a completed video containing at least 39 delivered frames.', plan.segment_id)
                    if source and not checked_video:
                        path = native_child_path(directory, record['video'])
                        read_delivered_seed(path, parent.end_frame - parent.start_frame)
                        source['video_sha256'] = file_checksum(path)
                        checked_video = True
                    meta = seed_contract(parent, stage, recipe, imported, high_clock if label == 'low' else None)
                    parents[label] = meta
                    high_clock = high_clock or audio_clock(plan, meta)
            contracts[plan.segment_id] = {label: (new_native_metadata(plan, stage, recipe, parents.get(label)), size)
                                          for label, (stage, size) in layout.items()}
        except (NativePlanError, OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
            errors.append(f'Task {index + 1}: {error}')
    if errors:
        raise NativePlanError('PREFLIGHT', '\n' + '\n'.join(errors))
    return state
