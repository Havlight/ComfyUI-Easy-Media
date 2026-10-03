from __future__ import annotations

import json
import importlib.util
import sys
import types
from pathlib import Path

import pytest
import torch

from test_minimax_node import _h3_project_inputs, _h3_sampling_mode, _load_minimax_node


def native_inputs(mode="single", fallback=False, **options):
    inputs = _h3_project_inputs(project_name="native-test", sampling_mode=_h3_sampling_mode(mode, **options))
    info = inputs["tracks_info"][0]
    info["h3_native"] = {"version": 1, "allow_vae_fallback": fallback}
    first = info["tracks"][0]["segments"][0]
    first.update(id="a", end_frame=243)
    second = {"id": "b", "start_frame": 243, "end_frame": 481,
              "content": {"task_mode": "default", "continuity_mode": "context", "images": [], "user_prompt": "continue"}}
    info["tracks"][0]["segments"].append(second)
    return inputs


@pytest.mark.parametrize("mode,options", [
    ("single", {}), ("dual", {"upscale_by": [1.0]}),
    ("dual", {"upscale_by": [1.25], "upscale_model": ["learned.safetensors"]}),
    ("selflift", {"lowres_scale": [0.6]}),
])
def test_native_graph_preserves_raw_sources_without_reencoding(monkeypatch, mode, options):
    module = _load_minimax_node(monkeypatch)
    result = module.EasyMultiTrackProject.execute(**native_inputs(mode, **options))
    nodes = list(result.expand.values())
    types = [n["class_type"] for n in nodes]
    assert types.count("easy h3NativeArtifact") == 2
    assert "easy h3ProjectArtifact" not in types
    assert "easy h3MotionContextLatentTrim" not in types
    assert "VAEEncode" not in types and "VAEEncodeAudio" not in types
    assert "easy h3ProjectContextLatentLoad" not in types
    assert all("anchor_images" not in node["inputs"] for node in nodes)
    lengths = [node["inputs"]["length"] for node in nodes if node["class_type"] == "easy minimaxH3ToVideo"]
    assert 243 in lengths and 277 in lengths
    prepares = [n for n in nodes if n["class_type"] == "easy h3NativePrepare"]
    assert json.loads(prepares[1]["inputs"]["plan_json"])["parent_segment_id"] == "a"
    assert "previous" in prepares[1]["inputs"]
    for node in nodes:
        if node["class_type"] in {"easy MiniMaxH3MotionContextHard", "easy MiniMaxH3HiResContinuity"}:
            assert node["inputs"]["context_length"] == "39"
        if node["class_type"] == "easy minimaxH3SelfLiftSampler":
            assert node["inputs"]["rho"] == 0
        if node["class_type"] == "easy h3NativeArtifact":
            assert node["_meta"]["easy_media_segment_saved"] is True


def test_native_strict_rejects_pixel_upscale_before_project_changes(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    def no_writes(*args, **kwargs):
        pytest.fail("preflight must not initialize or clear existing project state")
    monkeypatch.setattr(module._project_module, "initialize_h3_project", no_writes)
    monkeypatch.setattr(module._project_module, "clear_h3_project_segments_from", no_writes)
    inputs = native_inputs("dual", upscale_by=[1.25], upscale_model=["None"])
    inputs.update(project_save=["override"], segment_count=[-1])
    with pytest.raises(ValueError, match="PIXEL_UPSCALE"):
        module.EasyMultiTrackProject.execute(**inputs)


def test_native_strict_rejects_previous_generated_frame(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    inputs["tracks_info"][0]["tracks"][0]["segments"][1]["content"]["images"] = [
        {"id": "last", "source_type": "previous_frame", "position": "first"}]
    with pytest.raises(ValueError, match="GENERATED_IMAGE"):
        module.EasyMultiTrackProject.execute(**inputs)


def test_native_context_drift_keeps_native_source_and_dynamic_mask(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    inputs["tracks_info"][0]["tracks"][0]["segments"][1]["content"]["continuity_mode"] = "context_drift"
    result = module.EasyMultiTrackProject.execute(**inputs)
    drift = next(n for n in result.expand.values() if n["class_type"] == "easy MiniMaxH3ContextSwap")
    assert drift["inputs"]["context_length"] == "39"
    assert any(n["class_type"] == "easy h3NativePrepare" and n["inputs"].get("previous") for n in result.expand.values())


def test_native_runtime_nodes_save_full_raw_and_load_only_legal_context(monkeypatch, tmp_path):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    spec = importlib.util.spec_from_file_location("easy_media.nodes.h3_native", Path(__file__).resolve().parents[1] / "nodes/h3_native.py")
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    model = types.SimpleNamespace(model=types.SimpleNamespace(
        model_config=type("MiniMaxH3", (), {})(), latent_format=type("MiniMaxH3AV", (), {})()))
    info = native_inputs()["tracks_info"][0]
    info.update(width=32, height=32)
    segments = info["tracks"][0]["segments"]
    segments[0]["end_frame"] = 56
    segments[1].update(start_frame=56, end_frame=73)
    plans = runtime.compile_native_plan(info)
    recipe = {"sampling_mode": "single", "first_width": 32, "first_height": 32,
              "target_width": 32, "target_height": 32}
    empty, _ = module._empty_av_latent(32, 32, 56)
    prepared = runtime.EasyH3NativePrepare.execute(empty, model, "test", json.dumps(plans[0].as_dict()), json.dumps(recipe))
    canvas, _, _, state = prepared.values
    assert canvas["samples"].unbind()[1].shape[-1] == 94
    canvas["samples"].unbind()[0].fill_(3)
    result = runtime.EasyH3NativeResult.execute(canvas, state)
    high, _, completed_state = result.values
    raw_audio = {"waveform": torch.zeros(1, 2, 18800), "sample_rate": 8000}
    _, delivered = runtime.EasyH3NativeMediaView.execute(completed_state, raw_audio).values
    runtime.EasyH3NativeArtifact.execute("test", 0, info, high, raw_audio, audio=delivered)
    directory = tmp_path / "easy_media/projects/test"
    manifest = json.loads((directory / "project.json").read_text())
    descriptor = manifest["segments"]["0"]["generations"]["0"]["native"]["high"]
    assert descriptor["metadata"]["streams"][0]["shape"][2] == 17
    second_empty, _ = module._empty_av_latent(32, 32, 56)
    second = runtime.EasyH3NativePrepare.execute(second_empty, model, "test", json.dumps(plans[1].as_dict()), json.dumps(recipe))
    context = second.values[1]["samples"].unbind()
    assert context[0].shape[2] == 12 and context[1].shape[-1] == 65
    assert torch.all(context[0] == 3)
    assert second.values[3]["high"]["audio_origin_units"] == 84
    with pytest.raises(ValueError, match="MODEL_ADAPTER"):
        runtime.EasyH3NativePrepare.execute(empty, object(), "test", json.dumps(plans[0].as_dict()), json.dumps(recipe))


def test_native_passthrough_uses_explicit_seed_boundary(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    inputs['tracks_info'][0]['tracks'][0]['segments'][0]['content']['task_mode'] = 'passthrough'
    result = module.EasyMultiTrackProject.execute(**inputs)
    types = [node['class_type'] for node in result.expand.values()]
    assert types.count('easy h3NativeSeed') == 1
    assert types.count('easy h3NativeArtifact') == 2
    assert 'easy h3ProjectArtifact' not in types
    assert 'VAEEncode' not in types


def test_native_audio_lock_is_applied_after_context_in_both_dual_passes(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs('dual', upscale_by=[1.0])
    inputs['tracks_info'][0]['tracks'].append({'id': 'audio', 'type': 'audio', 'audio_locked': True,
        'segments': [{'start_frame': 0, 'end_frame': 481, 'content': {'media_type': 'audio'}}]})
    result = module.EasyMultiTrackProject.execute(**inputs)
    nodes = result.expand
    locks = [node for node in nodes.values() if node['class_type'] == 'easy h3NativeAudioLock']
    assert len(locks) == 4
    assert all(node['class_type'] != 'easy minimaxH3AudioLock' for node in nodes.values())
    sources = [nodes[lock['inputs']['latent'][0]]['class_type'] for lock in locks]
    assert 'easy MiniMaxH3MotionContextHard' in sources
    assert 'easy MiniMaxH3HiResContinuity' in sources


def test_native_locked_video_requests_raw_source_window_and_never_stretches(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    for task in inputs['tracks_info'][0]['tracks'][0]['segments']:
        task['content']['task_mode'] = 'ref'
    track = {'id': 'video', 'type': 'video', 'audio_locked': True,
             'segments': [{'start_frame': 0, 'end_frame': 481, 'content': {'media_type': 'video'}}]}
    inputs['tracks_info'][0]['tracks'].append(track)
    result = module.EasyMultiTrackProject.execute(**inputs)
    windows = [node for node in result.expand.values() if node['class_type'] == 'easy h3NativeLockedVideoInfo']
    assert len(windows) == 2
    assert json.loads(windows[1]['inputs']['plan_json'])['raw_start_frame'] == 204
    track['segments'][0]['end_frame'] = 470
    with pytest.raises(ValueError, match='LOCK_VIDEO_RANGE'):
        module.EasyMultiTrackProject.execute(**inputs)


@pytest.mark.parametrize('mode', ['single', 'dual', 'selflift'])
def test_retired_masked_blocks_generation_in_every_sampling_mode(monkeypatch, mode):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs(mode, **({'upscale_by': [1.0]} if mode == 'dual' else {}))
    inputs['tracks_info'][0]['tracks'][0]['segments'][1]['content']['continuity_mode'] = 'context_masked'
    with pytest.raises(ValueError, match='METHOD_RETIRED'):
        module.EasyMultiTrackProject.execute(**inputs)


def test_native_lock_video_rejects_task_modes_that_ignore_video(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    inputs['tracks_info'][0]['tracks'].append({'id': 'video', 'type': 'video', 'audio_locked': True,
        'segments': [{'start_frame': 0, 'end_frame': 481, 'content': {'media_type': 'video'}}]})
    with pytest.raises(ValueError, match='LOCK_VIDEO_MODE'):
        module.EasyMultiTrackProject.execute(**inputs)


@pytest.mark.parametrize('imported', [False, True])
def test_source_resize_rebuilds_both_stage_clocks_without_reencoding_native_audio(monkeypatch, tmp_path, imported):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(module.folder_paths, 'get_output_directory', lambda: str(tmp_path))
    spec = importlib.util.spec_from_file_location('easy_media.nodes.h3_native', Path(__file__).resolve().parents[1] / 'nodes/h3_native.py')
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    model = types.SimpleNamespace(model=types.SimpleNamespace(
        model_config=type('MiniMaxH3', (), {})(), latent_format=type('MiniMaxH3AV', (), {})()))
    info = native_inputs()['tracks_info'][0]
    info.update(width=32, height=32)
    tasks = info['tracks'][0]['segments']
    tasks[0]['end_frame'] = 56
    tasks[1].update(start_frame=56, end_frame=73)
    plans = runtime.compile_native_plan(info)
    recipe = {'sampling_mode': 'dual', 'first_width': 32, 'first_height': 32,
              'target_width': 32, 'target_height': 32}
    canvas, _ = module._empty_av_latent(32, 32, 56)
    high_meta = runtime.new_native_metadata(plans[0], 'imported_seed' if imported else 'dual_high_final', recipe,
                                            source_kind='imported_seed' if imported else 'native_sampler')
    high = runtime.stamp_native_result(runtime.prepare_native_canvas(canvas, high_meta), high_meta)
    low_meta = runtime.new_native_metadata(plans[0], 'dual_low_prediction', recipe)
    low = runtime.stamp_native_result(runtime.prepare_native_canvas(canvas, low_meta), low_meta)
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'video-reader-is-mocked')
    runtime.EasyH3NativeArtifact.execute('resize-test', 0, info, high,
        {'waveform': torch.zeros(1, 2, 74666), 'sample_rate': 32000}, low_latent=low, video_path=str(source))
    calls = {'video': 0, 'audio': 0}
    class VideoVAE:
        def encode(self, images):
            calls['video'] += 1
            return torch.zeros(1, 24, 12, images.shape[1] // 16, images.shape[2] // 16)
    class AudioVAE:
        audio_sample_rate = 32000
        def encode(self, waveform):
            calls['audio'] += 1
            return torch.zeros(1, 32, 2, 65)
    monkeypatch.setattr(runtime, 'read_delivered_seed', lambda *args: (
        torch.zeros(39, 32, 32, 3), {'waveform': torch.zeros(1, 2, 52000), 'sample_rate': 32000}))
    recipe.update(target_width=64, target_height=64, parent_plan=plans[0].as_dict(), allow_vae_fallback=not imported)
    output = runtime.EasyH3NativePrepare.execute(canvas, model, 'resize-test', json.dumps(plans[1].as_dict()),
        json.dumps(recipe), vae=VideoVAE(), audio_vae=AudioVAE())
    assert calls == {'video': 2, 'audio': 1}
    assert output.values[3]['high']['audio_origin_units'] == output.values[3]['low']['audio_origin_units']
    assert bool(output.values[3]['high']['fallback_history']) is not imported


def test_project_fallback_explicit_false_overrides_legacy_editor_preference(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs(fallback=True)
    inputs['tracks_info'][0]['tracks'][0]['segments'][1]['content']['images'] = [{'source_type': 'previous_frame'}]
    module.EasyMultiTrackProject.execute(**inputs)
    inputs['allow_vae_fallback'] = [False]
    with pytest.raises(ValueError, match='GENERATED_IMAGE'):
        module.EasyMultiTrackProject.execute(**inputs)


def test_all_samplers_depend_on_whole_run_preflight(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs('dual', upscale_by=[1.0])
    graph = module.EasyMultiTrackProject.execute(**inputs).expand
    gate = next(key for key, value in graph.items() if value['class_type'] == 'easy h3NativePreflight')
    def dependencies(key, seen=None):
        seen = seen or set()
        if key in seen:
            return seen
        seen.add(key)
        for value in graph[key]['inputs'].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in graph:
                dependencies(value[0], seen)
        return seen
    samplers = [key for key, value in graph.items() if value['class_type'] in {'SamplerCustomAdvanced', 'easy h3SamplerCustomAdvanced'}]
    assert len(samplers) == 4
    assert all(gate in dependencies(key) for key in samplers)

@pytest.mark.parametrize('bad', [[1.0, float('nan'), 0.0], [0.0, 1.0], [1.0, -0.1], [1.0]])
def test_runtime_gate_rejects_invalid_second_schedule_before_task_work(monkeypatch, bad):
    module = _load_minimax_node(monkeypatch)
    spec = importlib.util.spec_from_file_location('easy_media.nodes.h3_native', Path(__file__).resolve().parents[1] / 'nodes/h3_native.py')
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    basic = types.ModuleType('easy_media.nodes.basic')
    basic.MultiTrackTaskOutput = object()
    monkeypatch.setitem(sys.modules, basic.__name__, basic)
    model = types.SimpleNamespace(model=types.SimpleNamespace(
        model_config=type('MiniMaxH3', (), {})(), latent_format=type('MiniMaxH3AV', (), {})()))
    config = {'selected': [0, 1], 'run_second_pass': True, 'has_context_second_pass': True,
              'recipe': {'sampling_mode': 'dual', 'target_width': 32, 'target_height': 32,
                         'first_width': 32, 'first_height': 32, 'upscale_model': 'None', 'allow_vae_fallback': False}}
    with pytest.raises(ValueError, match='SCHEDULE'):
        runtime.EasyH3NativePreflight.execute(native_inputs()['tracks_info'][0], 'missing-project', json.dumps(config),
            model, object(), object(), sigmas=torch.tensor([1.0, 0.0]), second_sigmas=torch.tensor(bad))
