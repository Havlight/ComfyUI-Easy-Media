from __future__ import annotations

import importlib

import pytest

from test_h3_native_artifacts import _plan, _latent, _commit, artifacts, native, timing, real_nested_tensor

preflight = importlib.import_module('native_artifact_unit.utils.h3_native_preflight')


def recipe(mode='single', **kwargs):
    return {'sampling_mode': mode, 'first_width': 32, 'first_height': 32,
            'target_width': 32, 'target_height': 32, 'lowres_scale': 0.6,
            'allow_vae_fallback': False, **kwargs}


def check(directory, plans, selected, config=None, **kwargs):
    return preflight.preflight_native_sources(directory, artifacts.read_native_manifest(directory),
        plans, selected, config or recipe(), kwargs.get('previous_frames', set()),
        kwargs.get('contents', [{} for _ in plans]), kwargs.get('passthrough', set()))


@pytest.mark.parametrize('mode', ['single', 'dual', 'selflift'])
def test_whole_new_chain_needs_no_existing_files(tmp_path, mode):
    plans = [_plan(), _plan('b', 243, 17, 'a'), _plan('c', 260, 51, 'b')]
    state = check(tmp_path, plans, [0, 1, 2], recipe(mode))
    assert state['existing'] == {} and state['planned_ids'] == ['a', 'b', 'c']
    assert list(tmp_path.iterdir()) == []


def test_resume_pins_existing_version_when_active_changes(tmp_path):
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    saved = _commit(tmp_path, _latent(), plans)
    state = check(tmp_path, plans, [1])
    _commit(tmp_path, _latent(), plans)
    pinned = preflight.run_source(state, 'a')
    assert pinned['generation'] == '0'
    assert pinned['record']['native']['high'] == saved['native']['high']
    assert artifacts.read_native_manifest(tmp_path)['segments']['0']['active_generation'] == 1


def test_planned_parent_cannot_use_an_unrelated_active_generation(tmp_path):
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    _commit(tmp_path, _latent(), plans)
    state = check(tmp_path, plans, [0, 1])
    with pytest.raises(timing.NativePlanError, match='RUN_SOURCE'):
        preflight.run_source(state, 'a')


def test_missing_dual_low_history_fails_before_any_generation(tmp_path):
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    _commit(tmp_path, _latent(), plans)
    before = (tmp_path / 'project.json').read_bytes()
    with pytest.raises(timing.NativePlanError, match='compatible low'):
        check(tmp_path, plans, [1], recipe('dual'))
    assert (tmp_path / 'project.json').read_bytes() == before


def test_fallback_does_not_hide_corruption_or_changed_parent_range(tmp_path):
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    saved = _commit(tmp_path, _latent(), plans)
    path = tmp_path / saved['native']['high']['file']
    original = path.read_bytes()
    path.write_bytes(original[:-1] + b'\xff')
    with pytest.raises(timing.NativePlanError, match='CHECKSUM'):
        check(tmp_path, plans, [1], recipe(allow_vae_fallback=True))
    path.write_bytes(original)
    moved = [_plan(duration=260), _plan('b', 260, 51, 'a')]
    with pytest.raises(timing.NativePlanError, match='PARENT_RANGE'):
        check(tmp_path, moved, [1], recipe(allow_vae_fallback=True))


def test_later_retired_task_stops_whole_run(tmp_path):
    from dataclasses import replace
    plans = [_plan(), replace(_plan('b', 243, 51, 'a'), continuity_mode='context_masked')]
    with pytest.raises(timing.NativePlanError, match='Task 2.*METHOD_RETIRED'):
        check(tmp_path, plans, [0, 1])
    assert not (tmp_path / 'project.json').exists()


def test_edited_parent_content_needs_regeneration(tmp_path):
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    _commit(tmp_path, _latent(), plans)
    manifest = artifacts.read_native_manifest(tmp_path)
    manifest['segments']['0']['generations']['0']['native_recipe'] = {'task_content': {'user_prompt': 'old'}}
    with pytest.raises(timing.NativePlanError, match='PARENT_EDITED'):
        preflight.preflight_native_sources(tmp_path, manifest, plans, [1], recipe(), set(),
            [{'user_prompt': 'changed'}, {}], set())


def test_timeline_status_marks_edited_parent_and_descendants_without_changing_manifest(tmp_path):
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    first = _latent()
    _commit(tmp_path, first, plans)
    _commit(tmp_path, _latent(plans[1], first['h3_native']), plans, index=1)
    info = {'format': 'MiniMax', 'frame_rate': 24, 'h3_native': {'version': 2},
            'tracks': [{'type': 'task', 'segments': [{'id': p.segment_id, 'start_frame': p.start_frame, 'end_frame': p.end_frame,
                'content': {'continuity_mode': p.continuity_mode}} for p in plans]}]}
    manifest = artifacts.read_native_manifest(tmp_path)
    before = copy.deepcopy(manifest)
    assert [s['status'] for s in status.native_timeline_status(info, manifest)] == ['saved', 'saved']
    tasks = info['tracks'][0]['segments']
    tasks[0]['end_frame'] += 17
    tasks[1]['start_frame'] += 17
    tasks[1]['end_frame'] += 17
    assert all(s['status'] != 'saved' for s in status.native_timeline_status(info, manifest))
    assert manifest == before


def test_source_fingerprint_ignores_materialization_but_tracks_source_edits():
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    info = {'tracks': [{'type': 'task', 'segments': [{'id': 'a', 'start_frame': 0, 'end_frame': 90,
        'content': {'images': [{'id': 'image', 'file_path': 'portrait.png'}]}}]},
        {'type': 'video', 'segments': [{'id': 'source', 'start_frame': 0, 'end_frame': 90,
            'content': {'file_path': 'source.mp4', 'shared_reference': False}}]}]}
    materialized = copy.deepcopy(info)
    materialized['tracks'][0]['segments'][0]['content']['images'][0]['media_index'] = 3
    materialized['tracks'][1]['segments'][0]['content']['media_index'] = 1
    materialized['tracks'][1]['segments'][0]['color'] = 'changed'
    assert status.native_task_fingerprint(info, 0) == status.native_task_fingerprint(materialized, 0)
    materialized['tracks'][1]['segments'][0]['content']['file_path'] = 'replacement.mp4'
    assert status.native_task_fingerprint(info, 0) != status.native_task_fingerprint(materialized, 0)
