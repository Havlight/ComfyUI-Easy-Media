import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import types
import weakref

import pytest
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import h3_bridge as bridge
from test_minimax_node import (
    _load_minimax_node, _h3_project_inputs, _h3_sampling_mode, _NestedTensor,
)


def _bridge_node(monkeypatch):
    _load_minimax_node(monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "easy_media.nodes.h3_bridge", Path(__file__).resolve().parents[1] / "nodes/h3_bridge.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_previous_frame_pins_version_and_rejects_overwritten_source(monkeypatch, tmp_path):
    video = tmp_path / "video_0_1.mp4"
    video.write_bytes(b"video")
    record = {"video": video.name, "sampling_pass": "second"}
    checkpoint = {"sampling_pass": "second"}
    active_version = "1"
    records = {"1": record}
    selected = []

    def generation(name, index, version=None):
        selected.append((index, version))
        resolved = version or active_version
        return tmp_path, {}, resolved, records[resolved] if index == 0 else checkpoint

    monkeypatch.setattr(bridge, "project_generation", generation)
    bridge.save_tail_image(torch.tensor([[[[1.0, 0.5, 0.0]]]]), tmp_path / "last_frame_0_1.png")
    image, source = bridge.load_previous_frame("demo", 1)
    assert image[0, 0, 0].tolist() == pytest.approx([1, 128 / 255, 0])
    checkpoint["previous_frame_source"] = json.loads(source)
    # The bridge must reuse the completed segment's source even if selection changes.
    active_version = "2"
    (tmp_path / "video_0_2.mp4").write_bytes(b"new video")
    records["2"] = {"video": "video_0_2.mp4"}
    restored, restored_source = bridge.load_previous_frame("demo", 1, resume=True)
    assert torch.equal(restored, image) and restored_source == source
    assert selected[-1] == (0, "1")
    checkpoint["previous_frame_source"]["revision"] = "overwritten"
    with pytest.raises(ValueError, match="overwritten"):
        bridge.load_previous_frame("demo", 1, resume=True)
    active_version = "1"
    record["sampling_pass"] = "first"
    with pytest.raises(ValueError, match="first-pass"):
        bridge.load_previous_frame("demo", 1)


def test_old_version_extracts_tail_once_and_preserves_uint8(monkeypatch, tmp_path):
    video = tmp_path / "video_0_1.mp4"
    video.write_bytes(b"video")
    record = {"video": video.name}
    monkeypatch.setattr(bridge, "project_generation", lambda *args: (tmp_path, {}, "1", record))
    monkeypatch.setattr(bridge, "video_frame_count", lambda path: 73)
    reads = []
    monkeypatch.setattr(bridge, "read_video_frames", lambda path, start, count, fps: (
        reads.append((start, count, fps)) or torch.full((1, 1, 1, 3), 128, dtype=torch.uint8)
    ))
    for _ in range(2):
        image, _ = bridge.load_previous_frame("demo", 1)
        assert image.mean().item() == pytest.approx(128 / 255)
    assert reads == [(72, 1, None)]
    assert record["last_frame"] == "last_frame_0_1.png"


@pytest.mark.parametrize("fps", [12, 30])
def test_legacy_tail_uses_the_actual_last_frame_at_source_rate(monkeypatch, tmp_path, fps):
    ffmpeg = bridge.get_ffmpeg_path()
    if not ffmpeg or not bridge.get_ffmpeg_path("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    video = tmp_path / "video_0_1.mp4"
    count = fps * 2
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=red:s=32x32:r={fps}:d=2",
        "-vf", f"drawbox=color=blue:t=fill:enable='eq(n,{count - 1})'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    record = {"video": video.name, "sampling_pass": "single"}
    monkeypatch.setattr(bridge, "project_generation", lambda *args: (tmp_path, {}, "1", record))
    assert bridge.video_frame_count(video) == count
    image, _ = bridge.load_previous_frame("demo", 1)
    assert image.shape == (1, 32, 32, 3)
    assert image.mean(dim=(0, 1, 2)).argmax().item() == 2  # Only the final frame is blue.
    assert (tmp_path / record["last_frame"]).is_file()


def test_bridge_anchors_are_loaded_on_demand_and_overlay_keeps_audio(monkeypatch, tmp_path):
    for index in range(2):
        (tmp_path / f"video_{index}_1.mp4").write_bytes(b"video")
    monkeypatch.setattr(bridge, "project_generation", lambda name, index: (
        tmp_path, {"fps": 24}, "1", {"video": f"video_{index}_1.mp4"},
    ))
    monkeypatch.setattr(bridge, "video_frame_count", lambda path: 120)
    reads = []
    monkeypatch.setattr(bridge, "read_video_frames", lambda path, start, count: reads.append((start, count)))
    metadata = bridge.bridge_sources("demo", 1)
    assert reads == []
    for side in ("left", "right"):
        bridge.load_bridge_anchor("demo", metadata, side)
    assert reads == [(67, 39), (15, 39)]
    (tmp_path / "bridge_1_1.mp4").write_bytes(b"bridge")
    metadata["file"] = "bridge_1_1.mp4"
    left = {"source": str(tmp_path / "video_0_1.mp4"), "start_frame": 0,
            "end_frame": 120, "source_end_frame": 120}
    right = {"source": str(tmp_path / "video_1_1.mp4"), "start_frame": 120,
             "end_frame": 240, "source_start_frame": 0}
    overlay = bridge.bridge_overlay(tmp_path, metadata, left, right)
    assert (overlay["start_frame"], overlay["end_frame"]) == (106, 135)
    assert overlay["audio_muted"] is True
    with pytest.raises(ValueError, match="trimmed"):
        bridge.bridge_overlay(tmp_path, metadata, left, {**right, "source_start_frame": 1})
    metadata["left"]["revision"] = "stale"
    with pytest.raises(ValueError, match="changed"):
        bridge.load_bridge_anchor("demo", metadata, "left")
    with pytest.raises(ValueError, match="changed"):
        bridge.bridge_overlay(tmp_path, metadata, left, right)


def test_bridge_crops_original_timeline_media_without_mutating_it():
    info = _h3_project_inputs()["tracks_info"][0]
    info["tracks"][0]["segments"].append({
        "start_frame": 120, "end_frame": 240,
        "content": {"images": [{"source_type": "input"}, {"source_type": "previous_frame"}]},
    })
    info["tracks"].append({"type": "video", "segments": []})
    result = bridge.bridge_tracks_info(info, 1)
    task = result["tracks"][0]["segments"][0]
    assert (task["start_frame"], task["end_frame"]) == (67, 174)
    assert task["content"]["images"] == info["tracks"][0]["segments"][1]["content"]["images"]
    assert len(info["tracks"][0]["segments"][1]["content"]["images"]) == 2
    assert result["tracks"][1] is info["tracks"][1]


def test_ffmpeg_replacement_preserves_frame_count_and_original_audio(tmp_path):
    from utils.video import merge_video_track_with_ffmpeg
    ffmpeg = bridge.get_ffmpeg_path()
    if not ffmpeg or not bridge.get_ffmpeg_path("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    paths = [tmp_path / name for name in ("left.mp4", "right.mp4", "bridge.mp4")]
    for path, color, frequency in zip(paths, ("red", "blue", "lime"), (440, 880, None)):
        command = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=32x32:r=24:d=2.5"]
        if frequency:
            command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2.5"]
        subprocess.run(command + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
    clips = [{"source": str(path), "start_frame": index * 60, "end_frame": (index + 1) * 60,
              "source_start_frame": 0} for index, path in enumerate(paths[:2])]
    overlay = {"source": str(paths[2]), "start_frame": 46, "end_frame": 75, "source_start_frame": 0, "audio_muted": True}
    baseline = merge_video_track_with_ffmpeg(clips, 120, 24, 32, 32)
    result = merge_video_track_with_ffmpeg(clips + [overlay], 120, 24, 32, 32)
    assert baseline and result
    try:
        assert bridge.video_frame_count(Path(result)) == 120
        frames = bridge.read_video_frames(Path(result), 45, 31).mean(dim=(1, 2))
        assert frames[[0, 1, 29, 30]].argmax(dim=1).tolist() == [0, 1, 1, 2]
        audio = []
        for path in (baseline, result):
            decoded = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vn", "-f", "f32le", "-"], check=True, capture_output=True)
            audio.append(np.frombuffer(decoded.stdout, dtype=np.float32))
        np.testing.assert_allclose(audio[0], audio[1], atol=1e-6)
        assert np.abs(audio[1]).max() > 0.01
    finally:
        Path(baseline).unlink()
        Path(result).unlink()


def test_bridge_mask_preserves_both_anchors_and_existing_audio_lock(monkeypatch):
    module = _bridge_node(monkeypatch)
    decoded = []

    def read_anchor(project_name, source, side):
        assert all(frame() is None for frame in decoded)
        frames = torch.full((39, 2, 2, 3), 1.0 if side == "left" else 2.0)
        decoded.append(weakref.ref(frames))
        return frames

    monkeypatch.setattr(module, "load_bridge_anchor", read_anchor)
    vae = types.SimpleNamespace(encode=lambda image: torch.full((1, 24, 12, 2, 2), image.mean()))
    video = torch.zeros(1, 24, 32, 2, 2)
    audio = torch.ones(1, 32, 2, 178)
    audio_mask = torch.zeros_like(audio)
    latent = {"samples": _NestedTensor((video, audio)), "noise_mask": _NestedTensor((video, audio_mask))}
    result = module.EasyH3BridgeMask.execute(latent, vae, "demo", "{}")
    assert len(decoded) == 2 and all(frame() is None for frame in decoded)
    output = result.values[0]
    mixed, _ = output["samples"].unbind()
    mask, locked = output["noise_mask"].unbind()
    assert torch.all(mixed[:, :, :12] == 1) and torch.all(mixed[:, :, -12:] == 2)
    assert torch.all(mask[:, :, :12] == 0) and torch.all(mask[:, :, 20:] == 0)
    assert torch.all(mask[:, :, 12:20] == 1)
    assert locked is audio_mask
    assert torch.count_nonzero(video) == 0
    frames = torch.arange(107).view(107, 1, 1, 1)
    assert module.EasyH3BridgeFrames.execute(frames).values[0].flatten().tolist() == list(range(39, 68))


@pytest.mark.parametrize("position", [0, 1, 2, None])
def test_bridge_keeps_reference_slots_and_pinned_tail_without_graph_pixel_constants(monkeypatch, position):
    module = _bridge_node(monkeypatch)
    metadata = {"left": {"generation": "1"}, "right": {"generation": "2"}}
    monkeypatch.setattr(module, "bridge_sources", lambda *args: metadata)
    info = _h3_project_inputs()["tracks_info"][0]
    images = [{"source_type": "input", "media_index": i} for i in range(2)]
    tail = {"source_type": "previous_frame"}
    images.insert(position if position is not None else 1, {**tail, "muted": position is None})
    prompt = "Use image2 as the person and image3 as the scene"
    info["tracks"][0]["segments"].append({
        "start_frame": 120, "end_frame": 240,
        "content": {"task_mode": "ref", "images": images, "user_prompt": prompt},
    })
    nodes = module.EasyH3ProjectBridge.execute(
        "demo", 1, info, types.SimpleNamespace(model=types.SimpleNamespace(_denoise_mask_conds=lambda: None)),
        "clip", "vae", "audio_vae", "sampler", [1.0, 0.0], 1, 32, 32, False, 2, "demo",
    ).expand
    task = next(node for node in nodes.values() if node["class_type"] == "easy multiTrackTaskOutput")
    content = task["inputs"]["tracks_info"]["tracks"][0]["segments"][0]["content"]
    assert content["images"] == images and content["user_prompt"] == prompt
    mask = next(node for node in nodes.values() if node["class_type"] == "easy h3BridgeMask")
    assert "left" not in mask["inputs"] and "right" not in mask["inputs"]
    assert json.loads(mask["inputs"]["source"]) == metadata
    resolvers = [node for node in nodes.values() if node["class_type"] == "easy h3PreviousFrame"]
    if position is None:
        assert resolvers == []
        return
    resolver = resolvers[0]["inputs"]
    assert resolver["position"] == position and resolver["resume"] is True
    calls = []

    def load_tail(name, index, resume):
        calls.append((name, index, resume))
        return torch.full((1, 2, 2, 3), 9.0), "pinned"

    monkeypatch.setattr(module, "load_previous_frame", load_tail)
    inputs = [torch.full((1, 2, 2, 3), float(i)) for i in range(2)]
    output = module.EasyH3PreviousFrame.execute(
        inputs, ["demo"], [1], [resolver["position"]], [resolver["resume"]],
    )
    expected = [0.0, 1.0]
    expected.insert(position, 9.0)
    assert [image.mean().item() for image in output.values[0]] == expected
    assert calls == [("demo", 1, True)]


def test_dual_resume_still_has_full_bridge_schedule(monkeypatch, tmp_path):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    project = tmp_path / "easy_media/projects/demo"
    project.mkdir(parents=True)
    (project / "context_latent_1_1.safetensors").write_bytes(b"checkpoint")
    (project / "project.json").write_text(json.dumps({"segments": {"1": {
        "active_generation": 1, "generations": {"1": {
            "context_latent": "context_latent_1_1.safetensors", "sampling_pass": "first",
        }},
    }}}), encoding="utf-8")
    inputs = _h3_project_inputs(project_name=["demo"], project_save=["override"], segment_start_number=[2],
                                sampling_mode=_h3_sampling_mode("dual", upscale_by=[1.0]))
    inputs["tracks_info"][0]["tracks"][0]["segments"].append({
        "start_frame": 120, "end_frame": 240, "content": {
            "task_mode": "ref", "continuity_mode": "restart_bridge",
            "images": [{"source_type": "previous_frame"}],
        },
    })
    nodes = module.EasyMultiTrackProject.execute(**inputs).expand
    replacement = next(node for node in nodes.values() if node["class_type"] == "easy h3ProjectBridge")
    assert nodes[replacement["inputs"]["sampler"][0]]["class_type"] == "KSamplerSelect"
    assert nodes[replacement["inputs"]["sigmas"][0]]["class_type"] == "ManualSigmas"
    previous = next(node for node in nodes.values() if node["class_type"] == "easy h3PreviousFrame")
    assert previous["inputs"]["resume"] is True
    starts = [node for node in nodes.values() if node["class_type"] == "easy h3SegmentSamplingStart"]
    assert [node["inputs"]["sampling_pass"] for node in starts] == ["second"]


@pytest.mark.parametrize("mode", ["single", "dual", "selflift"])
def test_restart_and_previous_image_stay_independent_of_context_chain(monkeypatch, mode):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode(mode, upscale_by=[1.0]))
    segments = inputs["tracks_info"][0]["tracks"][0]["segments"]
    for index, continuity in ((1, "restart_bridge"), (2, "context")):
        segments.append({"start_frame": index * 120, "end_frame": (index + 1) * 120,
                         "content": {"task_mode": "ref", "continuity_mode": continuity,
                                     "images": [{"source_type": "previous_frame"}], "user_prompt": "dance"}})
    result = module.EasyMultiTrackProject.execute(**inputs)
    nodes = result.expand
    bridges = [node for node in nodes.values() if node["class_type"] == "easy h3ProjectBridge"]
    assert len(bridges) == 1
    assert bridges[0]["_meta"].get("easy_media_segment_saved") is True
    assert bridges[0]["inputs"]["segment_index"] == 1
    assert bridges[0]["inputs"]["sigmas"] is not None
    bridge_sigmas = nodes[bridges[0]["inputs"]["sigmas"][0]]
    assert bridge_sigmas["class_type"] == "ManualSigmas"
    images = [node for node in nodes.values() if node["class_type"] == "easy h3PreviousFrame"]
    assert [node["inputs"]["segment_index"] for node in images] == [1, 2]
    assert not any(node["class_type"] == "easy h3ConditioningCache" and node["inputs"]["segment_index"] in {1, 2}
                   for node in nodes.values())
    # The next segment waits for the bridge, but its context comes from the
    # generated segment's own latents, never from the replacement bridge.
    artifacts = [node for node in nodes.values() if node["class_type"] == "easy h3ProjectArtifact"]
    assert all("last_frame" in node["inputs"] for node in artifacts)
    if mode == "selflift":
        samplers = [node for node in nodes.values() if node["class_type"] == "easy minimaxH3SelfLiftSampler"]
        assert "low_context_latent" not in samplers[1]["inputs"]
        assert samplers[1]["inputs"]["rho"] == 0
        assert "low_context_latent" in samplers[2]["inputs"]
        assert samplers[2]["inputs"]["rho"] == 0.1
    else:
        contexts = [node for node in nodes.values() if node["class_type"] == "easy MiniMaxH3MotionContextHard"]
        assert len(contexts) == 1
