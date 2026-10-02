"""Native source integration tests using real torch, safetensors and NestedTensor."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
# Isolate plugin startup; import the actual implementation and Comfy tensor type.
for name, path in (("native_artifact_unit", ROOT), ("native_artifact_unit.utils", ROOT / "utils"),
                   ("native_artifact_unit.modules", ROOT / "modules")):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
native = importlib.import_module("native_artifact_unit.utils.h3_native")
artifacts = importlib.import_module("native_artifact_unit.utils.h3_native_artifacts")
timing = importlib.import_module("native_artifact_unit.utils.h3_native_timing")


def _plan(sid="a", start=0, duration=243, parent=None):
    context = 39 if parent else 0
    return timing.NativeTaskPlan(sid, start, start + duration, "context" if parent else "shot",
                                 parent, context, start - context, duration + context)


def _latent(plan=None, parent=None, stage="single_final", dtype=torch.float32):
    plan = plan or _plan()
    meta = native.new_native_metadata(plan, stage, {"seed": 51, "adapter": "test"}, parent)
    video = torch.arange(24 * timing.video_steps(plan.raw_frames) * 4, dtype=torch.float32)
    video = video.reshape(1, 24, -1, 2, 2).to(dtype)
    audio = torch.arange(64 * meta["audio_ticks"], dtype=torch.float32).reshape(1, 32, 2, -1).to(dtype)
    return native.stamp_native_result({"samples": native._official_nested_tensor((video, audio))}, meta)


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_raw_sidecar_roundtrip_preserves_all_values_and_precision(tmp_path, dtype):
    original = _latent(dtype=dtype)
    descriptor = artifacts.save_native_latent(original, tmp_path / "raw.safetensors")
    loaded = artifacts.load_native_latent(tmp_path, descriptor)
    assert loaded["h3_native"] == original["h3_native"]
    for left, right in zip(native._streams_from_latent(original), native._streams_from_latent(loaded)):
        assert left.dtype == right.dtype
        assert torch.equal(left, right)
    assert descriptor["bytes"] > 0 and descriptor["save_seconds"] >= 0


def test_corrupt_file_or_manifest_never_becomes_fallback(tmp_path):
    descriptor = artifacts.save_native_latent(_latent(), tmp_path / "raw.safetensors")
    descriptor["metadata"]["recipe"]["seed"] = 33
    with pytest.raises(timing.NativePlanError, match="ARTIFACT_METADATA"):
        artifacts.load_native_latent(tmp_path, descriptor)
    path = tmp_path / "raw.safetensors"
    path.write_bytes(path.read_bytes()[:-1] + b"\xff")
    with pytest.raises(timing.NativePlanError, match="CHECKSUM"):
        artifacts.load_native_latent(tmp_path, descriptor)
    with pytest.raises(timing.NativePlanError, match="ARTIFACT_PATH"):
        artifacts.load_native_latent(tmp_path, {"file": "../outside.safetensors"})


def test_context_slice_ends_at_delivered_seam_and_never_mutates_parent():
    parent = _latent()
    plan = _plan("b", 243, 238, "a")
    source = native.slice_native_context(parent, plan, "single_final", (2, 2))
    original_video, original_audio = native._streams_from_latent(parent)
    video, audio = native._streams_from_latent(source)
    assert video.shape[2] == 12 and audio.shape[-1] == 65
    assert torch.equal(video, original_video[:, :, 60:72])
    assert torch.equal(audio, original_audio[..., 340:405])
    video.zero_()
    audio.zero_()
    assert original_video[:, :, 60:72].count_nonzero() > 0
    assert original_audio[..., 340:405].count_nonzero() > 0
    with pytest.raises(timing.NativePlanError, match="STAGE_MISMATCH"):
        native.slice_native_context(parent, plan, "selflift_low_prediction")
    with pytest.raises(timing.NativePlanError, match="SIZE_MISMATCH"):
        native.slice_native_context(parent, plan, "single_final", (4, 4))


def test_audio_canvas_extra_tick_is_allocated_before_sampling():
    # 56 frames need 94 ticks for coverage; core's rounded empty canvas has 93.
    plan = _plan(duration=56)
    meta = native.new_native_metadata(plan, "single_final", {})
    video = torch.zeros(1, 24, timing.video_steps(56), 2, 2)
    audio = torch.zeros(1, 32, 2, 93)
    empty = {"samples": native._official_nested_tensor((video, audio))}
    output = native.prepare_native_canvas(empty, meta)
    assert native._streams_from_latent(output)[1].shape[-1] == 94
    assert audio.shape[-1] == 93
    audio.fill_(1)
    with pytest.raises(timing.NativePlanError, match="CANVAS_ENCODED"):
        native.prepare_native_canvas(empty, meta)


def test_fallback_history_survives_native_descendants():
    first = native.new_native_metadata(_plan(), "single_final", {}, fallback_reasons=("GENERATED_IMAGE",))
    second = native.new_native_metadata(_plan("b", 243, 238, "a"), "single_final", {}, first)
    assert second["source_kind"] == "native_sampler"
    assert second["fallback_history"] == [{"segment_id": "a", "reason": "GENERATED_IMAGE"}]
    assert second["parent_artifact_id"] == first["artifact_id"]


def test_output_trim_uses_global_audio_clock_and_keeps_raw_source():
    parent = _latent()
    plan = _plan("b", 243, 238, "a")
    meta = native.new_native_metadata(plan, "single_final", {}, parent["h3_native"])
    images = torch.arange(plan.raw_frames).reshape(-1, 1, 1, 1)
    rate = 44100
    wave = torch.arange(meta["audio_ticks"] * rate // 40).reshape(1, 1, -1)
    result_images, result_audio = native.trim_native_media(meta, images, {"sample_rate": rate, "waveform": wave})
    assert result_images[0].item() == 39 and len(result_images) == 238
    assert result_audio["waveform"].shape[-1] == timing.sample_at_frame(481, rate) - timing.sample_at_frame(243, rate)
    assert len(images) == 277


def _fields(plans):
    return {"project_name": "test", "width": 64, "height": 64, "fps": 24,
            "h3_native": {"version": 1, "allow_vae_fallback": False},
            "task_segments": [p.as_dict() for p in plans]}


def _commit(directory, latent, plans, index=0, **kwargs):
    return artifacts.commit_native_generation(directory, index, _fields(plans),
                                               {"continuity_mode": plans[index].continuity_mode},
                                               {"high": latent}, kwargs.get("media", {}))


def test_failed_generation_preserves_active_manifest_and_artifacts(tmp_path, monkeypatch):
    plan = _plan()
    _commit(tmp_path, _latent(plan), [plan])
    before = (tmp_path / "project.json").read_bytes()
    files = set(tmp_path.iterdir())
    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(artifacts, "save_native_latent", fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        _commit(tmp_path, _latent(plan), [plan])
    assert (tmp_path / "project.json").read_bytes() == before
    assert set(tmp_path.iterdir()) == files


def test_manifest_failure_rolls_back_newly_published_files(tmp_path, monkeypatch):
    plan = _plan()
    _commit(tmp_path, _latent(plan), [plan])
    before = (tmp_path / "project.json").read_bytes()
    files = set(tmp_path.iterdir())
    replace = Path.replace
    def fail_manifest(self, target):
        if self.name == "project.json":
            raise OSError("manifest replacement failed")
        return replace(self, target)
    monkeypatch.setattr(Path, "replace", fail_manifest)
    with pytest.raises(OSError, match="manifest replacement"):
        _commit(tmp_path, _latent(plan), [plan])
    assert (tmp_path / "project.json").read_bytes() == before
    assert set(tmp_path.iterdir()) == files


def test_parent_replacement_marks_descendants_stale_but_preserves_versions(tmp_path):
    first_plan, second_plan = _plan(), _plan("b", 243, 238, "a")
    first = _latent(first_plan)
    _commit(tmp_path, first, [first_plan, second_plan])
    second = _latent(second_plan, first["h3_native"])
    _commit(tmp_path, second, [first_plan, second_plan], 1)
    manifest = artifacts.read_native_manifest(tmp_path)
    assert artifacts.find_native_generation(manifest, "b")["native"]["high"]["metadata"] == second["h3_native"]
    assert artifacts.native_dependents(manifest, {first["h3_native"]["artifact_id"]}) == ["b"]
    _commit(tmp_path, _latent(first_plan), [first_plan, second_plan])
    manifest = artifacts.read_native_manifest(tmp_path)
    assert len(manifest["segments"]["0"]["generations"]) == 2
    with pytest.raises(timing.NativePlanError, match="STALE_SOURCE"):
        artifacts.find_native_generation(manifest, "b")
    # Selecting the original complete chain restores validity.
    manifest["segments"]["0"]["active_generation"] = 0
    artifacts.refresh_native_dependencies(manifest)
    artifacts.find_native_generation(manifest, "b")


def test_project_write_lock_prevents_competing_generations(tmp_path):
    with artifacts.native_project_transaction(tmp_path):
        with pytest.raises(timing.NativePlanError, match="PROJECT_BUSY"):
            _commit(tmp_path, _latent(), [_plan()])
    assert not (tmp_path / ".native-write.lock").exists()


def test_task_reorder_retains_versions_by_identity(tmp_path):
    first, second = _plan(), _plan("b", 243, 243)
    _commit(tmp_path, _latent(first), [first, second])
    _commit(tmp_path, _latent(second), [first, second], 1)
    reordered = [_plan("b"), _plan("a", 243)]
    _commit(tmp_path, _latent(reordered[0]), reordered)
    manifest = artifacts.read_native_manifest(tmp_path)
    assert manifest["segments"]["0"]["segment_id"] == "b"
    assert len(manifest["segments"]["0"]["generations"]) == 2
    assert manifest["segments"]["1"]["segment_id"] == "a"
    assert "0" in manifest["segments"]["1"]["generations"]
