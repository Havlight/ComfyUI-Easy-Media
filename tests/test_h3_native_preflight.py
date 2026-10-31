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


def test_edited_parent_content_still_continues_from_its_saved_latent(tmp_path):
    # Prompt-level edits do not change the saved latent a continuation reads.
    plans = [_plan(), _plan('b', 243, 51, 'a')]
    _commit(tmp_path, _latent(), plans)
    manifest = artifacts.read_native_manifest(tmp_path)
    manifest['segments']['0']['generations']['0']['native_recipe'] = {'task_content': {'user_prompt': 'old'}}
    state = preflight.preflight_native_sources(tmp_path, manifest, plans, [1], recipe(), set(),
        [{'user_prompt': 'changed'}, {}], set())
    assert 'a' in state['existing']


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
        {'id': 'video', 'type': 'video', 'segments': [{'id': 'source', 'start_frame': 0, 'end_frame': 90,
            'content': {'media_type': 'video', 'file_path': 'source.mp4', 'shared_reference': False}}]}]}
    materialized = copy.deepcopy(info)
    materialized['tracks'][0]['segments'][0]['content']['images'][0]['media_index'] = 3
    materialized['tracks'][1]['segments'][0]['content']['media_index'] = 1
    materialized['tracks'][1]['segments'][0]['color'] = 'changed'
    assert status.native_task_fingerprint(info, 0) == status.native_task_fingerprint(materialized, 0)
    materialized['tracks'][1]['segments'][0]['content']['file_path'] = 'replacement.mp4'
    assert status.native_task_fingerprint(info, 0) != status.native_task_fingerprint(materialized, 0)


def test_source_fingerprint_only_tracks_media_that_reaches_the_task():
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    clip = lambda sid, start, end, **content: {'id': sid, 'start_frame': start, 'end_frame': end,
                                               'content': {'media_type': 'audio', 'file_path': f'{sid}.wav', **content}}
    info = {'tracks': [
        {'type': 'task', 'segments': [{'id': 'a', 'start_frame': 0, 'end_frame': 90, 'content': {}},
                                      {'id': 'b', 'start_frame': 90, 'end_frame': 180, 'content': {}}]},
        {'id': 'voice', 'type': 'audio', 'segments': [clip('near', 0, 60), clip('far', 400, 500)]},
        {'id': 'later', 'type': 'audio', 'segments': [clip('late', 300, 350)]},
        {'id': 'quiet', 'type': 'audio', 'muted': True, 'segments': [clip('hum', 0, 90)]}]}
    before = status.native_task_fingerprint(info, 0)

    edited = copy.deepcopy(info)
    edited['tracks'][1]['segments'][1]['content']['file_path'] = 'other.wav'
    edited['tracks'][2]['segments'][0]['content']['volume_db'] = -6
    edited['tracks'][3]['segments'][0]['content']['file_path'] = 'other.wav'
    assert status.native_task_fingerprint(edited, 0) == before

    unmuted = copy.deepcopy(info)
    unmuted['tracks'][3]['muted'] = False
    assert status.native_task_fingerprint(unmuted, 0) != before
    moved = copy.deepcopy(info)
    moved['tracks'][1]['segments'][0]['content']['file_path'] = 'other.wav'
    assert status.native_task_fingerprint(moved, 0) != before
    # The shared clip reaches every task wherever it sits.
    shared = copy.deepcopy(info)
    shared['tracks'][1]['segments'][1]['content']['shared_reference'] = True
    assert status.native_task_fingerprint(shared, 0) != before
    shared_edit = copy.deepcopy(shared)
    shared_edit['tracks'][1]['segments'][1]['content']['file_path'] = 'other.wav'
    assert status.native_task_fingerprint(shared_edit, 0) != status.native_task_fingerprint(shared, 0)


def test_source_fingerprint_counts_only_the_part_of_a_clip_inside_the_window():
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    info = {'tracks': [
        {'type': 'task', 'segments': [{'id': 'a', 'start_frame': 0, 'end_frame': 90, 'content': {}},
                                      {'id': 'b', 'start_frame': 90, 'end_frame': 180, 'content': {}}]},
        {'id': 'song', 'type': 'audio', 'audio_locked': True, 'segments': [
            {'id': 'track', 'start_frame': 0, 'end_frame': 400, 'content': {'media_type': 'audio', 'file_path': 'song.wav'}}]}]}
    before = status.native_task_fingerprint(info, 0)

    def edited(**fields):
        changed = copy.deepcopy(info)
        changed['tracks'][1]['segments'][0].update(fields)
        return changed

    # Trimming the song's end far past the task leaves the task's part, and the task, as it was.
    trimmed = edited(end_frame=300)
    assert status.native_task_fingerprint(trimmed, 0) == before
    assert status.native_task_fingerprint(trimmed, 0, 1) != status.native_task_fingerprint(info, 0, 1)
    # Cutting into the task's window, or sliding the source under it, changes what it heard.
    assert status.native_task_fingerprint(edited(end_frame=60), 0) != before
    assert status.native_task_fingerprint(edited(start_frame=0, origin_start_frame=-24), 0) != before


def test_timeline_status_compares_each_version_with_its_own_fingerprint(tmp_path):
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    plans = [_plan()]
    _commit(tmp_path, _latent(), plans)
    info = {'format': 'MiniMax', 'frame_rate': 24, 'h3_native': {'version': 2}, 'tracks': [
        {'type': 'task', 'segments': [{'id': 'a', 'start_frame': plans[0].start_frame, 'end_frame': plans[0].end_frame,
                                       'content': {'continuity_mode': plans[0].continuity_mode}}]},
        {'id': 'song', 'type': 'audio', 'audio_locked': True, 'segments': [
            {'id': 'track', 'start_frame': 0, 'end_frame': 600, 'content': {'media_type': 'audio', 'file_path': 'song.wav'}}]}]}
    trimmed = copy.deepcopy(info)
    trimmed['tracks'][1]['segments'][0]['end_frame'] = 500
    manifest = artifacts.read_native_manifest(tmp_path)
    generation = manifest['segments']['0']['generations']['0']

    def status_after_trim(version):
        recipe = {'task_fingerprint': status.native_task_fingerprint(info, 0, version or 1)}
        generation['native_recipe'] = {**recipe, **({'task_fingerprint_version': version} if version else {})}
        return status.native_timeline_status(trimmed, manifest)[0]['status']

    # Versions saved before version 2 keep their old comparison, so their status does not move.
    assert status_after_trim(None) == 'edited'
    assert status_after_trim(2) == 'saved'


def test_source_fingerprint_follows_the_task_reference_lists():
    import copy
    status = importlib.import_module('native_artifact_unit.utils.h3_native_status')
    clip = lambda sid, start, end: {'id': sid, 'start_frame': start, 'end_frame': end,
                                    'content': {'media_type': 'audio', 'file_path': f'{sid}.wav'}}
    info = {'frame_rate': 24, 'tracks': [
        {'type': 'task', 'segments': [{'id': 'a', 'start_frame': 0, 'end_frame': 90, 'content': {'task_mode': 'ref'}},
                                      {'id': 'b', 'start_frame': 90, 'end_frame': 180, 'content': {}}]},
        {'id': 'voice', 'type': 'audio', 'segments': [clip('near', 0, 60)]},
        {'id': 'later', 'type': 'audio', 'segments': [clip('late', 300, 350)]}]}

    def with_audios(*audios):
        changed = copy.deepcopy(info)
        changed['tracks'][0]['segments'][0]['content']['audios'] = list(audios)
        return status.native_task_fingerprint(changed, 0), changed

    base = status.native_task_fingerprint(info, 0)
    # Placing a reference where the plan already puts it changes nothing.
    assert with_audios({'id': 'p', 'source_type': 'track', 'track_id': 'voice'})[0] == base
    assert with_audios({'id': 'u', 'source_type': 'input', 'file_path': 'line.wav'})[0] != base
    assert with_audios({'id': 'p', 'source_type': 'track', 'track_id': 'voice', 'muted': True})[0] != base
    # Unfitted audio reaches 15 s past the task start, so the late clip now counts.
    unfitted, unfitted_info = with_audios({'id': 'p', 'source_type': 'track', 'track_id': 'later', 'fit': False})
    assert unfitted != base
    unfitted_info['tracks'][2]['segments'][0]['content']['file_path'] = 'other.wav'
    assert status.native_task_fingerprint(unfitted_info, 0) != unfitted
