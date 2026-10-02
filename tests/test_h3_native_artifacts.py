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
sources = importlib.import_module("native_artifact_unit.utils.h3_native_sources")


@pytest.fixture(autouse=True)
def real_nested_tensor(monkeypatch):
    # Other node tests replace the parent comfy module. Restore this one real
    # dependency for each test so suite order cannot change the implementation.
    path = ROOT.parent.parent / "comfy/nested_tensor.py"
    spec = importlib.util.spec_from_file_location("comfy.nested_tensor", path)
    nested = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nested)
    comfy = sys.modules.get("comfy", types.ModuleType("comfy"))
    monkeypatch.setitem(sys.modules, "comfy", comfy)
    monkeypatch.setitem(sys.modules, "comfy.nested_tensor", nested)
    monkeypatch.setattr(comfy, "nested_tensor", nested, raising=False)


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


def test_seed_encoder_guard_distinguishes_external_input_from_generated_rebuild():
    class Encoder:
        audio_sample_rate = 32000
        def __init__(self, shape):
            self.shape = shape
            self.calls = 0
        def encode(self, pixels):
            self.calls += 1
            return torch.ones(self.shape)
    video_vae = Encoder((1, 24, 12, 2, 2))
    audio_vae = Encoder((1, 32, 2, 65))
    images = torch.zeros(39, 32, 32, 3)
    audio = {"sample_rate": 32000, "waveform": torch.zeros(1, 2, 52000)}
    plan = _plan(duration=243)
    for kind in ("native_sampler", "rebuilt_generated"):
        with pytest.raises(timing.NativePlanError, match="ENCODER_PURPOSE|VAE_FALLBACK_REQUIRED"):
            sources.encode_native_seed(images, audio, video_vae, audio_vae, plan,
                                       "imported_seed", {}, 32, 32, kind, False)
    assert video_vae.calls == audio_vae.calls == 0
    imported = sources.encode_native_seed(images, audio, video_vae, audio_vae, plan,
                                          "imported_seed", {}, 32, 32, "imported_seed", False)
    assert imported["h3_native"]["fallback_history"] == []
    assert imported["h3_native"]["raw_start_frame"] == 204
    rebuilt = sources.encode_native_seed(images, audio, video_vae, audio_vae, plan,
                                         "single_final", {}, 32, 32, "rebuilt_generated", True)
    assert rebuilt["h3_native"]["fallback_history"][0]["reason"] == "DELIVERED_SOURCE_REBUILD"
    assert video_vae.calls == audio_vae.calls == 2
    audio_vae.shape = (1, 32, 2, 64)
    with pytest.raises(timing.NativePlanError, match="SEED_AUDIO_GRID"):
        sources.encode_native_seed(images, audio, video_vae, audio_vae, plan,
                                   "imported_seed", {}, 32, 32, "imported_seed", False)


def test_rebuilt_source_depends_on_the_selected_delivered_version():
    legacy = {"video": "video_0_0.mp4", "updated_at": 123}
    source = native.new_native_metadata(_plan(), "single_final", {}, fallback_reasons=("DELIVERED_SOURCE_REBUILD",))
    source["artifact_id"] = artifacts.media_version_id("a", "0", legacy)
    child = _latent(_plan("b", 243, 238, "a"), source)
    generation = {"native": {"high": {"metadata": child["h3_native"]}}}
    manifest = {"segments": {
        "0": {"segment_id": "a", "active_generation": 0, "generations": {"0": legacy}},
        "1": {"segment_id": "b", "active_generation": 0, "generations": {"0": generation}},
    }}
    artifacts.refresh_native_dependencies(manifest)
    assert not generation.get("native_stale")
    legacy["updated_at"] = 456
    artifacts.refresh_native_dependencies(manifest)
    assert generation["native_stale"] is True


def test_native_audio_lock_preserves_clock_and_overrides_context_only_in_locked_interval():
    lock = importlib.import_module('native_artifact_unit.utils.h3_native_lock')
    parent = _latent(_plan(duration=56))
    child = _latent(_plan('b', 56, 17, 'a'), parent['h3_native'])
    video, before = native._streams_from_latent(child)
    captured = []
    class Vae:
        audio_sample_rate = 32000
        def encode(self, value):
            captured.append(value)
            return torch.full_like(before, -7)
    waveform = torch.arange(120000, dtype=torch.float32).reshape(1, 1, -1).expand(1, 2, -1)
    result = lock.lock_native_audio(child, child['h3_native'], {'waveform': waveform, 'sample_rate': 32000}, Vae(), [[56, 73]])
    after_video, after = native._streams_from_latent(result)
    vm, am = native._noise_mask_streams(result)
    assert torch.equal(after_video, video)
    assert captured[0][0, 0, 0] == 22400  # inherited origin 84/120 seconds
    assert torch.equal(after[..., :64], before[..., :64])
    assert torch.all(after[..., 65:] == -7)
    assert torch.all(am[..., :64] == 1) and torch.all(am[..., 65:] == 0)
    assert torch.all(vm == 1)


def test_native_high_final_can_switch_sampler_modes_without_rebuilding():
    parent = _latent()
    next_plan = _plan('b', 243, 238, 'a')
    for stage in ('dual_high_final', 'selflift_high_final'):
        assert native.slice_native_context(parent, next_plan, stage)['samples'].unbind()[0].shape[2] == 12


def test_audio_assembly_twenty_segments_has_no_sample_drift_or_duplicate_overlap(tmp_path):
    import numpy as np
    import soundfile as sf
    assembly = importlib.import_module('native_artifact_unit.utils.h3_native_assembly')
    segments = []
    parent = None
    cursor = 0
    for index in range(20):
        plan = _plan(str(index), cursor, 56 if index == 0 else 51, str(index - 1) if index else None)
        metadata = native.new_native_metadata(plan, 'single_final', {}, parent)
        path = tmp_path / f'raw-{index}.wav'
        # Absolute sample-valued ramp catches both repeated and skipped samples.
        origin = timing.round_ratio(metadata['audio_origin_units'] * 32000, 120)
        wave = (np.arange(metadata['audio_ticks'] * 800, dtype=np.float32) + origin)[:, None]
        sf.write(path, wave, 32000, subtype='FLOAT')
        segments.append({'start_frame': cursor, 'end_frame': plan.end_frame, 'source_start_frame': 0,
                         'native_metadata': metadata, 'raw_audio_source': str(path)})
        cursor = plan.end_frame
        parent = metadata
    assembly.build_native_audio_views(segments, tmp_path)
    samples = np.concatenate([sf.read(s['audio_source'], dtype='float32', always_2d=True)[0] for s in segments])[:, 0]
    assert len(samples) == timing.sample_at_frame(cursor, 32000)
    assert np.array_equal(samples, np.arange(len(samples), dtype=np.float32))
    locked = dict(segments[0], audio_locked=True, audio_source='original.wav')
    assembly.build_native_audio_views([locked, segments[1]], tmp_path)
    assert locked['audio_source'] == 'original.wav'


def test_masked_source_is_immutable_video_is_hard_and_audio_releases_over_eight_ticks():
    drift = importlib.import_module('native_artifact_unit.modules.motion_context.drift_control_av')
    parent = _latent(_plan(duration=56))
    child_plan = _plan('b', 56, 17, 'a')
    child = _latent(child_plan, parent['h3_native'])
    context = native.slice_native_context(parent, child_plan, 'single_final')
    before = [tensor.clone() for tensor in native._streams_from_latent(context)]
    output, _, _ = drift.prepare_context_swap_latent(child, context, 39, continue_audio=True, freeze_audio=False)
    video_mask, audio_mask = native._noise_mask_streams(output)
    assert torch.all(video_mask[:, :, :12] == 0)
    assert torch.all(video_mask[:, :, 12:] == 1)
    assert torch.all(audio_mask[..., :57] == 0)
    assert torch.all(torch.diff(audio_mask[..., 57:65]) > 0)
    assert torch.all(audio_mask[..., 64] == 1)
    for original, source in zip(before, native._streams_from_latent(context)):
        assert torch.equal(original, source)


def test_locked_video_without_audio_keeps_native_generated_streams():
    lock = importlib.import_module('native_artifact_unit.utils.h3_native_lock')
    latent = _latent()
    assert lock.lock_native_audio(latent, latent['h3_native'], None, object(), [[0, 243]]) is latent


def test_locked_output_uses_cumulative_44100hz_endpoints_without_dropped_samples():
    lock = importlib.import_module('native_artifact_unit.utils.h3_native_lock')
    source = torch.arange(352800, dtype=torch.float32).reshape(1, 1, -1)
    audio = {'waveform': source, 'sample_rate': 44100}
    pieces = [lock.native_locked_audio_view({'start_frame': a, 'end_frame': b}, audio)['waveform']
              for a, b in [(0, 90), (90, 141), (141, 192)]]
    assert [piece.shape[-1] for piece in pieces] == [165375, 93712, 93713]
    assert torch.equal(torch.cat(pieces, dim=-1), source)
    assert lock.native_locked_audio_view({}, None) is None
