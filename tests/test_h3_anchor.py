from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import comfy.utils  # noqa: E402
from comfy.nested_tensor import NestedTensor  # noqa: E402
from modules.motion_context import anchor as anchor_core  # noqa: E402
from modules.motion_context import core  # noqa: E402
from utils.h3_anchor import (  # noqa: E402
    ANCHOR_METHOD, DEFAULT_ANCHOR_SETTINGS, anchor_settings, anchor_signature, context_noise_ramp, late_start_step,
)
from utils.h3_lora_preflight import task_dependencies  # noqa: E402
from utils.h3_native_status import task_content_signature  # noqa: E402
from utils.h3_native_timing import NativePlanError, compile_native_plan  # noqa: E402

from test_h3_native_graph import _task_content, native_inputs  # noqa: E402
from test_minimax_node import _load_minimax_node  # noqa: E402


def anchor_content(**extra):
    return {"task_mode": "default", "continuity_mode": "context_anchor", **extra}


def test_settings_default_to_the_geiru_authors_best_setup():
    assert anchor_settings(anchor_content()) == DEFAULT_ANCHOR_SETTINGS
    assert DEFAULT_ANCHOR_SETTINGS == ("keystone_front_capstone_back", 0.5, 0.8, False)
    assert anchor_settings(anchor_content(anchor_noise=0)).noise == 0.0
    assert anchor_settings({"continuity_mode": "context_drift"}) is None
    assert anchor_settings(anchor_content(task_mode="passthrough")) is None
    for field, value in (("anchor_placement", "middle"), ("anchor_late", 0.3), ("anchor_late", True), ("anchor_restart", "yes"),
                         ("anchor_noise", 0.5), ("anchor_noise", True), ("anchor_noise", "0.8")):
        with pytest.raises(NativePlanError, match="ANCHOR_VALUE"):
            anchor_settings(anchor_content(**{field: value}))
    # Late Lock's swap_step: 4 of 8 steps at one half, 5 of our 10-step first pass.
    assert late_start_step(0.5, 8) == 4 and late_start_step(0.5, 10) == 5 and late_start_step(0.0, 10) == 0


def test_every_anchor_task_carries_its_settings_and_method_in_the_saved_result_signature():
    plain = task_content_signature(anchor_content())
    # Results saved by the earlier method have no method version, so they are stale.
    assert ANCHOR_METHOD == 2 and plain["anchor"] == [2, "keystone_front_capstone_back", 0.5, 0.8, False]
    assert task_content_signature(anchor_content(anchor_late=0.5)) == plain
    assert task_content_signature(anchor_content(anchor_placement="keystone_front"))["anchor"] == [2, "keystone_front", 0.5, 0.8, False]
    assert task_content_signature(anchor_content(anchor_noise=0))["anchor"][3] == 0.0
    assert anchor_signature({"continuity_mode": "context"}) is None
    assert "anchor" not in task_content_signature({"task_mode": "default", "continuity_mode": "context_drift"})
    # Without a back latent the late lock times nothing.
    assert (anchor_signature(anchor_content(anchor_placement="keystone_front", anchor_late=0.0))
            == anchor_signature(anchor_content(anchor_placement="keystone_front")))


def _timeline(*tasks, markers=()):
    segments, start = [], 0
    for index, (continuity, extra) in enumerate(tasks):
        length = 124 if index == 0 or continuity == "shot" else 119
        segments.append({"id": f"s{index + 1}", "start_frame": start, "end_frame": start + length,
                         "content": {"task_mode": "default", "continuity_mode": continuity, **extra}})
        start += length
    return {"h3_native": {"version": 2}, "frame_rate": 24, "format": "MiniMax", "task_markers": list(markers),
            "tracks": [{"id": "tasks", "type": "task", "segments": segments}]}


def test_anchored_tasks_use_the_chain_start_until_a_task_restarts_it():
    info = _timeline(("shot", {}), ("context_anchor", {}), ("context", {}), ("context_anchor", {}),
                     ("context_anchor", {"anchor_restart": True}), ("context_anchor", {}), ("shot", {}),
                     ("context_anchor", {}))
    plans = compile_native_plan(info)
    assert [plan.anchor_segment_id for plan in plans] == [None, "s1", None, "s1", None, "s5", None, "s7"]
    # An anchored task depends on its anchor as on its parent.
    dependencies = task_dependencies(info)
    assert dependencies[3] == {2, 0} and dependencies[5] == {4} and dependencies[4] == {3}


def test_only_a_tasks_first_part_restarts_the_anchor():
    info = _timeline(("shot", {}), ("context", {}), ("context_anchor", {"anchor_restart": True}))
    info["tracks"][0]["segments"][2]["end_frame"] += 119
    info["task_markers"] = [{"id": "m", "frame": info["tracks"][0]["segments"][2]["start_frame"] + 119}]
    plans = compile_native_plan(info)
    assert [plan.segment_id for plan in plans][-2:] == ["s3", "s3:marker:m"]
    assert plans[-2].anchor_segment_id is None and plans[-1].anchor_segment_id == "s3"


def _av(video_steps, value=0.0, audio_ticks=None, size=(2, 2)):
    video = torch.full((1, 24, video_steps, *size), float(value))
    ticks = audio_ticks or round(core._pixel_frames(video_steps) * core.FRAME_RESCALE)
    return {"samples": NestedTensor((video, torch.full((1, 32, 2, ticks), float(value))))}


def _streams(latent, key="samples"):
    return list(latent[key].unbind())


def _numbered_context():
    """A 39-frame overlap (two 17-frame blocks and 5 frames, 12 latents) whose latent k holds k + 1."""
    context = _av(12, 3.0)
    context["samples"].unbind()[0][:] = torch.arange(1, 13, dtype=torch.float32).view(1, 1, 12, 1, 1)
    return context


def test_hold_keeps_the_first_22_overlap_frames_and_lets_the_task_draw_the_last_17():
    target = _av(22)
    held, held_steps, originals = anchor_core.anchor_hold_latent(target, _numbered_context(), 39, 22)
    video, audio = _streams(held)
    video_mask, audio_mask = _streams(held, "noise_mask")
    # 22 frames are a 17-frame block and 5 frames: latents 0-6, the overlap's first seven.
    assert held_steps == 7 and originals == {}
    assert video[0, 0, :7, 0, 0].tolist() == [1, 2, 3, 4, 5, 6, 7] and torch.all(video[:, :, 7:] == 0)
    assert torch.all(video_mask[:, :, :7] == 0) and torch.all(video_mask[:, :, 7:] == 1)
    # The audio under the 22 held frames (22 * 5 / 3 ticks, rounded) is held too.
    assert torch.all(audio_mask[..., :37] == 0) and torch.all(audio_mask[..., 37:] == 1)
    assert torch.all(audio[..., :37] == 3) and torch.all(audio[..., 37:] == 0)
    _, frozen = _streams(anchor_core.anchor_hold_latent(target, _numbered_context(), 39, 22, freeze_audio=True)[0], "noise_mask")
    assert torch.all(frozen == 0)
    with pytest.raises(ValueError, match="do not end on a latent"):
        anchor_core.anchor_hold_latent(target, _numbered_context(), 39, 20)


def test_context_noise_ramps_the_held_latents_after_the_first_down_to_the_join():
    # Geiru Context Noise at 0.8 on seven held latents: 0.8, 0.64, 0.48, 0.32, 0.16, then the join stays locked.
    assert context_noise_ramp(7, 0.8) == [(1, 0.8), (2, 0.64), (3, 0.48), (4, 0.32), (5, 0.16)]
    assert context_noise_ramp(7, 0.0) == [] and context_noise_ramp(1, 0.8) == []
    held, _, originals = anchor_core.anchor_hold_latent(_av(22), _numbered_context(), 39, 22, noise=0.8)
    video_mask = _streams(held, "noise_mask")[0]
    assert [round(float(value), 2) for value in video_mask[0, 0, :8, 0, 0]] == [0, 0.8, 0.64, 0.48, 0.32, 0.16, 0, 1]
    # The noised latents are the real held ones, kept to be put back after sampling.
    assert sorted(originals) == [1, 2, 3, 4, 5]
    assert all(torch.all(originals[index] == index + 1) for index in originals)
    assert _streams(held)[0][0, 0, :7, 0, 0].tolist() == [1, 2, 3, 4, 5, 6, 7]


def test_keystone_and_capstone_come_from_the_anchors_single_frame_latents():
    source = _av(37)
    source["samples"].unbind()[0][:, :, :, 0, 0] = torch.arange(37, dtype=torch.float32)
    picked = anchor_core.anchor_source_latents(source, 0)
    assert picked[0, 0, :, 0, 0].tolist() == [0, 35]
    # A task that restarted mid-chain gives the first single frame it drew after its 22 held frames: frame 34, latent 10.
    assert anchor_core.anchor_source_latents(source, 22)[0, 0, :, 0, 0].tolist() == [10, 35]


def test_keystone_replaces_the_held_first_latent_and_keeps_it_for_the_restore():
    held, _, _ = anchor_core.anchor_hold_latent(_av(22), _av(12, 3.0), 39, 22)
    anchor = torch.stack([torch.full((1, 24, 2, 2), 7.0), torch.full((1, 24, 2, 2), 9.0)], dim=2)
    grafted, originals = anchor_core.graft_keystone(held, anchor, "keystone")
    video = _streams(grafted)[0]
    assert torch.all(video[:, :, 0] == 7) and torch.all(video[:, :, 1:7] == 3)
    assert torch.all(_streams(grafted, "noise_mask")[0][:, :, :7] == 0)
    assert torch.all(originals[0] == 3)
    assert torch.all(_streams(anchor_core.graft_keystone(held, anchor, "capstone")[0])[0][:, :, 0] == 9)
    with pytest.raises(ValueError, match="does not match"):
        anchor_core.graft_keystone(held, anchor[..., :1], "keystone")


def test_capstone_is_tacked_on_locked_from_the_late_lock_step_and_cut_off():
    video, audio = torch.zeros(1, 24, 6, 2, 2), torch.zeros(1, 32, 2, 10)
    x, shapes = comfy.utils.pack_latents([video, audio])
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    seen = []

    def apply_model(x_in, t, **c):
        seen.append(([tuple(s) for s in c["latent_shapes"]], c["denoise_mask"].clone()))
        return x_in + 1

    wrapper = anchor_core._tack_wrapper(torch.full((1, 24, 1, 2, 2), 5.0), 2, None)
    c = {"latent_shapes": shapes, "denoise_mask": torch.ones(1, 1, 6, 2, 2),
         "transformer_options": {"sample_sigmas": sigmas}}
    out = wrapper(apply_model, {"input": x, "timestep": torch.tensor([0.8]), "c": c})
    assert seen[-1][0][0] == (1, 24, 6, 2, 2) and out.shape == x.shape  # step 1: not yet
    out = wrapper(apply_model, {"input": x, "timestep": torch.tensor([0.5]), "c": c})
    shapes_seen, mask = seen[-1]
    assert shapes_seen[0] == (1, 24, 7, 2, 2)
    assert torch.all(mask[:, :, 6] == 0) and torch.all(mask[:, :, :6] == 1)
    assert out.shape == x.shape and torch.all(comfy.utils.unpack_latents(out, shapes)[0] == 1)


def test_restore_puts_the_held_first_latent_back_into_the_output_and_the_denoised_steps():
    video, audio = torch.zeros(1, 24, 6, 2, 2), torch.zeros(1, 32, 2, 10)
    packed, shapes = comfy.utils.pack_latents([video, audio])
    seen = []

    def outer_sample(noise, latent_image, sampler, sigmas, denoise_mask=None, callback=None, *rest, **kwargs):
        # The sampler reports each step's denoised latent, which sampler nodes return as denoised_output.
        callback(0, packed.clone(), packed, 1)
        return packed.clone()

    wrapper = anchor_core._restore_wrapper({0: torch.full((1, 24, 1, 2, 2), 4.0)})
    out = wrapper(outer_sample, None, None, None, None, None, lambda step, x0, x, total: seen.append(x0),
                  latent_shapes=shapes)
    for latent in (out, seen[0]):
        restored = comfy.utils.unpack_latents(latent, shapes)[0]
        assert torch.all(restored[:, :, 0] == 4) and torch.all(restored[:, :, 1:] == 0)
    assert torch.all(packed == 0)  # The sampler's own tensors are left alone.


def _anchor_nodes(graph):
    return {key: node for key, node in graph.items() if node["class_type"] == "easy h3NativeAnchor"}


@pytest.mark.parametrize("mode,options,stages", [("single", {}, 1), ("dual", {"upscale_by": [1.25], "upscale_model": ["learned.safetensors"]}, 2)])
def test_project_anchors_every_pass_with_the_matching_stage(monkeypatch, mode, options, stages):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs(mode, **options)
    _task_content(inputs)["continuity_mode"] = "context_anchor"
    graph = module.EasyMultiTrackProject.execute(**inputs).expand
    nodes = _anchor_nodes(graph)
    assert len(nodes) == stages
    assert "easy MiniMaxH3MotionContextHard" not in {n["class_type"] for n in graph.values()}
    prepare = next(key for key, node in graph.items() if node["class_type"] == "easy h3NativePrepare"
                   and json.loads(node["inputs"]["plan_json"])["segment_id"] == "b")
    assert json.loads(graph[prepare]["inputs"]["plan_json"])["anchor_segment_id"] == "a"
    first = nodes[next(k for k in nodes if "first_pass" in k)]["inputs"]
    assert first["anchor"][1] == (5 if mode == "dual" else 4) and first["late"] == 0.5 and first["freeze_audio"] is False
    assert first["noise"] == 0.8
    if mode == "dual":
        # The refine anchors from its first step and gets no Context Noise.
        second = nodes[next(k for k in nodes if "second_pass" in k)]["inputs"]
        assert second["anchor"][1] == 4 and second["late"] == 0.0 and second["noise"] == 0.0 and second["freeze_audio"] is True
    # The continuation saves the overlap it drew, for the composed video to play over its predecessor's end.
    lead_ins = [node for node in graph.values() if node["class_type"] == "easy h3NativeLeadIn"]
    saved = [node["inputs"] for node in graph.values()
             if node["class_type"] == "easy h3NativeArtifact" and "lead_in_path" in node["inputs"]]
    assert len(lead_ins) == 1 and len(saved) == 1 and saved[0]["segment_index"] == 1
    assert graph[saved[0]["lead_in_path"][0]]["inputs"]["filename_prefix"].endswith("/.staging_lead_in_1")


def test_only_anchor_continuations_save_a_lead_in(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    graph = module.EasyMultiTrackProject.execute(**native_inputs()).expand
    assert _task_content(native_inputs())["continuity_mode"] != "context_anchor"
    assert not any(node["class_type"] == "easy h3NativeLeadIn" for node in graph.values())


def test_project_holds_without_an_anchor_on_a_task_that_restarts_it(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = native_inputs()
    _task_content(inputs).update(continuity_mode="context_anchor", anchor_restart=True, anchor_placement="keystone_front")
    node = next(iter(_anchor_nodes(module.EasyMultiTrackProject.execute(**inputs).expand).values()))
    assert "anchor" not in node["inputs"] and node["inputs"]["noise"] == 0.8
    # The placement means nothing on a task that is not anchored, so it does not mark it edited.
    assert anchor_settings(_task_content(inputs)) == ("keystone_front_capstone_back", 0.5, 0.8, True)


class _Model:
    def __init__(self):
        self.model_options = {"transformer_options": {}}
        self.wrappers = []

    def clone(self):
        copy = _Model()
        copy.model_options = {**self.model_options}
        copy.wrappers = list(self.wrappers)
        return copy

    def set_model_unet_function_wrapper(self, wrapper):
        self.model_options["model_function_wrapper"] = wrapper

    def add_wrapper_with_key(self, kind, key, wrapper):
        self.wrappers.append((kind, key))


def test_anchor_stage_node_holds_grafts_and_patches_the_model(monkeypatch):
    _load_minimax_node(monkeypatch)
    spec = importlib.util.spec_from_file_location("easy_media.nodes.h3_native", Path(__file__).resolve().parents[1] / "nodes/h3_native.py")
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    # The node imports the anchor module lazily, relative to the package the test loader names.
    monkeypatch.setitem(sys.modules, "easy_media.modules.motion_context.anchor", anchor_core)
    # The test loader stubs ComfyUI's comfy package.
    monkeypatch.setitem(sys.modules, "comfy.patcher_extension",
                        types.SimpleNamespace(WrappersMP=types.SimpleNamespace(OUTER_SAMPLE="outer_sample")))
    spec.loader.exec_module(runtime)
    anchor = {"samples": torch.stack([torch.full((1, 24, 2, 2), 7.0), torch.full((1, 24, 2, 2), 9.0)], dim=2)}
    sigmas = torch.linspace(1, 0, 11)
    model, latent = runtime.EasyH3NativeAnchor.execute(_Model(), _av(22), _av(12, 3.0), sigmas, anchor=anchor).values
    assert torch.all(_streams(latent)[0][:, :, 0] == 7)
    wrapper = model.model_options["model_function_wrapper"]
    assert wrapper.easy_media_anchor_back and model.wrappers == [("outer_sample", anchor_core._RESTORE_KEY)]
    # Keystone front only: nothing tacked on.
    model, _ = runtime.EasyH3NativeAnchor.execute(_Model(), _av(22), _av(12, 3.0), sigmas, anchor=anchor,
                                                  placement="keystone_front").values
    assert "model_function_wrapper" not in model.model_options
    # A task that restarts the anchor holds with Context Noise and puts the noised latents back.
    model, latent = runtime.EasyH3NativeAnchor.execute(_Model(), _av(22), _av(12, 3.0), sigmas).values
    assert model.wrappers == [("outer_sample", anchor_core._RESTORE_KEY)] and torch.all(_streams(latent)[0][:, :, 0] == 3)
    plain = _Model()
    assert runtime.EasyH3NativeAnchor.execute(plain, _av(22), _av(12, 3.0), sigmas, noise=0.0).values[0] is plain
