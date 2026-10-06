from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import torch

from test_h3_native_graph import native_inputs
from test_minimax_node import _load_minimax_node
from test_h3_segment_loras import rule


def plan(*rules):
    return {"version": 1, "rules": list(rules)}


@pytest.mark.parametrize('mode', ['single', 'dual', 'selflift'])
@pytest.mark.parametrize('drift', [False, True])
@pytest.mark.parametrize('second_loader', [False, True])
def test_graph_applies_each_stage_before_drift_and_sequences_models(monkeypatch, mode, drift, second_loader):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs(mode, **({'upscale_by': [1.0]} if mode == 'dual' else {}))
    base = inputs['model_loader'][0]['model']
    second = copy.copy(base) if second_loader else base
    if second_loader:
        inputs['model_loader_2nd'] = [{'model': second}]
    if drift:
        inputs['tracks_info'][0]['tracks'][0]['segments'][1]['content']['continuity_mode'] = 'context_drift'
    inputs['segment_loras'] = [plan(rule(stage='first'), rule('b', stage='second'))]
    graph = module.EasyMultiTrackProject.execute(**inputs).expand
    prepared = [(key, node) for key, node in graph.items() if node['class_type'] == 'easy h3SegmentLoraModels']
    assert len(prepared) == 2
    for index, (key, node) in enumerate(prepared):
        values = node['inputs']
        assert values['model'] is base and values['second_model'] is (second if mode != 'single' else base)
        assert graph[values['run_state'][0]]['class_type'] == 'easy h3NativePreflight'
        assert graph[values['previous'][0]]['class_type'] == ('easy h3NativeArtifact' if index else 'easy h3NativePreflight')
        assert node['_meta']['easy_media_segment'] == index
        native = next(n for n in graph.values() if n['class_type'] == 'easy h3NativePrepare'
                      and json.loads(n['inputs']['plan_json'])['segment_id'] == ('a' if index == 0 else 'b'))
        assert native['inputs']['model'] == [key, 0] and native['inputs']['second_model'] == [key, 1]
    second_key = prepared[1][0]
    if drift:
        wrapper = next(n for n in graph.values() if n['class_type'] == 'easy MiniMaxH3ContextSwap')
        assert wrapper['inputs']['model'] == [second_key, 0]
        if mode == 'selflift':
            high = next(n for n in graph.values() if n['class_type'] == 'easy h3NativeDriftModel')
            assert high['inputs']['model'] == [second_key, 1]
    if mode == 'dual':
        guiders = [n for n in graph.values() if n['class_type'] == 'BasicGuider']
        assert any(n['inputs']['model'] == [second_key, 1] for n in guiders)


@pytest.mark.parametrize('rules', [[], [rule(enabled=False)], [rule(strength=0)], [rule(stage='second')]])
def test_inactive_rules_leave_original_single_model_graph(monkeypatch, rules):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    inputs['segment_loras'] = [plan(*rules)]
    graph = module.EasyMultiTrackProject.execute(**inputs).expand
    assert not any(n['class_type'] == 'easy h3SegmentLoraModels' for n in graph.values())
    assert not any(n['class_type'] in {'VAEEncode', 'VAEEncodeAudio'} for n in graph.values())


@pytest.fixture
def helpers(monkeypatch):
    namespace = types.ModuleType('lora_project_unit')
    namespace.__path__ = [str(Path(__file__).resolve().parents[1] / 'utils')]
    monkeypatch.setitem(sys.modules, namespace.__name__, namespace)
    for name in ('h3_lora_preflight', 'h3_native_status', 'h3_lora_models'):
        monkeypatch.delitem(sys.modules, f'{namespace.__name__}.{name}', raising=False)
    compiler = importlib.import_module('lora_project_unit.h3_lora_preflight')
    models = importlib.import_module('lora_project_unit.h3_lora_models')
    status = importlib.import_module('lora_project_unit.h3_native_status')
    return compiler, models, status


def test_upstream_advisory_uses_only_selected_effective_stages(helpers):
    compiler, _, _ = helpers
    compiled = compiler.compile_project_loras(native_inputs()['tracks_info'][0],
        plan(rule('a'), rule('b', stage='second')), {'sampling_mode': 'single'}, [1])
    assert compiler.upstream_lora_warnings(compiled, {'first': ['a'], 'second': ['b']}) == [
        {'stage': 'first', 'lora': 'a', 'tasks': [2]}]
    assert compiler.upstream_lora_warnings(compiled, {}) == []


def test_preflight_scope_absolute_resume_unique_file_model_pairs_and_content_aliases(helpers, monkeypatch):
    compiler, models, _ = helpers
    info = native_inputs()['tracks_info'][0]
    read, checked = [], []
    def identity(name):
        read.append(name)
        return {'lora': name, 'sha256': name.removesuffix('-copy')}
    monkeypatch.setattr(models, 'file_identity', identity)
    monkeypatch.setattr(models, 'validate_model_lora', lambda model, file: checked.append((model, file['sha256'])))
    base, second = object(), object()
    result = compiler.preflight_project_loras(info, plan(rule('a', segment_count=1), rule('b', start_segment=2)),
        {'sampling_mode': 'dual'}, [1], base, second)
    assert read == ['a', 'b']  # Parent needs identity, not model preparation.
    assert checked == [(base, 'b'), (second, 'b')]
    assert result['tasks'][1]['number'] == 2 and result['effects']['a'] != result['effects']['b']
    with pytest.raises(ValueError, match='duplicate LoRA'):
        compiler.preflight_project_loras(info, plan(rule('a'), rule('a-copy')), {'sampling_mode': 'single'}, [0, 1], base, second)


def test_last_task_bad_model_rejected_during_whole_run_preflight(helpers, monkeypatch):
    compiler, models, _ = helpers
    info = native_inputs()['tracks_info'][0]
    monkeypatch.setattr(models, 'file_identity', lambda name: {'lora': name, 'sha256': name})
    def validate(model, identity):
        if identity['lora'] == 'bad':
            raise ValueError('shape mismatch')
    monkeypatch.setattr(models, 'validate_model_lora', validate)
    with pytest.raises(ValueError, match='Task 2 first row 2: shape mismatch'):
        compiler.preflight_project_loras(info, plan(rule('a', segment_count=1), rule('bad', start_segment=2)),
            {'sampling_mode': 'single'}, [0, 1], object(), object())


def saved_manifest(info, compiler):
    return {'h3_native': {'version': 2}, 'segments': {str(i): {
        'segment_id': p.segment_id, 'active_generation': 0,
        'generations': {'0': {'native': {'high': {'metadata': p.as_dict()}}, 'native_recipe': {}}}
    } for i, p in enumerate(compiler.compile_native_plan(info))}}


def test_saved_recipe_invalidation_propagates_actual_parents_only_and_is_read_only(helpers):
    compiler, _, status = helpers
    info = native_inputs()['tracks_info'][0]
    tasks = info['tracks'][0]['segments']
    tasks.extend([{'id': 'c', 'start_frame': 481, 'end_frame': 520, 'content': {'continuity_mode': 'shot'}},
                  {'id': 'd', 'start_frame': 520, 'end_frame': 559, 'content': {'continuity_mode': 'shot',
                      'images': [{'source_type': 'previous_frame'}]}}])
    manifest = saved_manifest(info, compiler)
    before = copy.deepcopy(manifest)
    effects = {'a': {'first': [{'sha256': 'changed', 'strength': 1.}]}, 'b': {}, 'c': {}, 'd': {}}
    assert [t['status'] for t in status.native_timeline_status(info, manifest, effects)] == ['edited', 'parent_changed', 'saved', 'saved']
    effects['c'] = effects['a']
    assert [t['status'] for t in status.native_timeline_status(info, manifest, effects)] == ['edited', 'parent_changed', 'edited', 'parent_changed']
    assert compiler.required_lora_tasks(info, [3]) == [2, 3]
    assert manifest == before
    assert status.native_timeline_status(info, manifest, {'a': None})[0]['status'] == 'pending'


def test_distinct_saved_parent_and_child_loras_are_valid_and_disconnection_invalidates(helpers):
    compiler, _, status = helpers
    info = native_inputs()['tracks_info'][0]
    manifest = saved_manifest(info, compiler)
    effects = {sid: {'first': [{'sha256': sid, 'strength': .7}]} for sid in ('a', 'b')}
    for i, sid in enumerate(('a', 'b')):
        manifest['segments'][str(i)]['generations']['0']['native_recipe']['segment_loras'] = effects[sid]
    assert [t['status'] for t in status.native_timeline_status(info, manifest, effects)] == ['saved', 'saved']
    assert [t['status'] for t in status.native_timeline_status(info, manifest, {'a': {}, 'b': {}})] == ['edited', 'edited']


def test_unknown_editor_identity_stays_pending_and_does_not_hash_weights(helpers, monkeypatch):
    compiler, models, _ = helpers
    def identity(name, *, cached_only):
        assert cached_only is True
        return None
    monkeypatch.setattr(models, 'file_identity', identity)
    compiled = compiler.compile_project_loras(native_inputs()['tracks_info'][0], plan(rule()), {'sampling_mode': 'single'})
    assert compiler.preview_lora_effects(compiled) == {'a': None, 'b': None}
