"""Versioned H3 tail images and fixed-length replacement bridges."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

from .h3_project import _load_h3_manifest, _project_child_path, h3_task_entries
from .video import ffprobe_info, get_ffmpeg_path

PREVIOUS_FRAME_SOURCE = "previous_frame"
BRIDGE_CONTEXT = 39
BRIDGE_BEFORE = 14
BRIDGE_AFTER = 15
BRIDGE_LENGTH = BRIDGE_BEFORE + BRIDGE_AFTER
BRIDGE_TOTAL = BRIDGE_CONTEXT * 2 + BRIDGE_LENGTH


def previous_frame_position(content: dict[str, Any]) -> int | None:
    images = [image for image in content.get("images", []) if image.get("muted") is not True]
    positions = [i for i, image in enumerate(images) if image.get("source_type") == PREVIOUS_FRAME_SOURCE]
    if len(positions) > 1:
        raise ValueError("Only one previous-frame image is allowed per segment")
    return positions[0] if positions else None


def project_generation(project_name: str, index: int, generation: str | None = None) -> tuple[Path, dict, str, dict]:
    root, manifest = _load_h3_manifest(project_name)
    try:
        segment = manifest["segments"][str(index)]
        version = str(segment["active_generation"]) if generation is None else str(generation)
        record = segment["generations"][version]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Segment {index + 1} has no saved version; generate or restore it first") from error
    return root, manifest, version, record


def video_identity(root: Path, index: int, version: str, record: dict) -> dict[str, Any]:
    path = _project_child_path(root, record["video"])
    return {"segment_index": index, "generation": version, "video": path.name,
            "revision": str(path.stat().st_mtime_ns)}


def read_video_frames(path: Path, start: int, count: int, fps: float | None = 24.0) -> torch.Tensor:
    """Decode a bounded window; fps=None keeps the original frame sequence."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required for H3 project frames")
    info = ffprobe_info(str(path))
    width, height = int(info["width"]), int(info["height"])
    filters = ["setpts=PTS-STARTPTS"]
    if fps is not None:
        filters.append(f"fps={fps}")
    filters.extend([f"trim=start_frame={start}:end_frame={start + count}", "setpts=PTS-STARTPTS"])
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-vf",
             ",".join(filters),
             "-frames:v", str(count), "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Cannot decode project frames from {path.name}: {error}") from error
    if result.returncode or len(result.stdout) != count * width * height * 3:
        raise RuntimeError(f"Cannot read {count} frames from {path.name}: {result.stderr.decode(errors='replace')[-500:]}")
    pixels = np.frombuffer(result.stdout, dtype=np.uint8).copy().reshape(count, height, width, 3)
    return torch.from_numpy(pixels).float().div_(255)


def video_frame_count(path: Path, fps: float = 24.0) -> int:
    info = ffprobe_info(str(path))
    return int(info.get("frame_count") or round(float(info["duration"]) * fps))


def save_tail_image(image: torch.Tensor, path: Path) -> None:
    frame = image[-1, ..., :3].detach().cpu()
    pixels = frame if frame.dtype == torch.uint8 else frame.clamp(0, 1).mul(255).round().to(torch.uint8)
    temporary = path.with_suffix(".tmp.png")
    try:
        Image.fromarray(pixels.numpy()).save(temporary, format="PNG")
        temporary.replace(path)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Unable to save project tail image {path.name}: {error}") from error


def load_previous_frame(project_name: str, index: int, resume: bool = False) -> tuple[torch.Tensor, str]:
    if index < 1:
        raise ValueError("The first segment has no previous frame")
    pinned = None
    if resume:
        _, _, _, checkpoint = project_generation(project_name, index)
        pinned = checkpoint.get("previous_frame_source")
        if not pinned:
            raise ValueError("This saved segment has no previous-frame source; regenerate the segment")
    root, manifest, version, record = project_generation(
        project_name, index - 1, pinned.get("generation") if pinned else None,
    )
    if record.get("sampling_pass") == "first":
        raise ValueError(f"Segment {index} has only a first-pass checkpoint; finish it before using its last frame")
    identity = video_identity(root, index - 1, version, record)
    if pinned is not None and pinned != identity:
        raise ValueError("The previous-frame source was overwritten; regenerate this segment instead of resuming")
    path = root / f"last_frame_{index - 1}_{version}.png"
    if not path.is_file():
        video = _project_child_path(root, record["video"])
        save_tail_image(read_video_frames(video, video_frame_count(video) - 1, 1, fps=None), path)
        record["last_frame"] = path.name
        write_project_manifest(root, manifest)
    try:
        with Image.open(path) as source:
            image = torch.from_numpy(np.array(source.convert("RGB"), copy=True)).float().div_(255).unsqueeze(0)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot load previous-frame image {path.name}: {error}") from error
    return image, json.dumps(identity)


def write_project_manifest(root: Path, manifest: dict[str, Any]) -> None:
    temporary = root / ".project.json.tmp"
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(root / "project.json")
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError(f"Cannot update H3 project manifest: {error}") from error


def bridge_sources(project_name: str, index: int) -> dict[str, Any]:
    """Pin source metadata without retaining decoded frames in the graph."""
    if index < 1:
        raise ValueError("Restart Bridge requires a previous segment")
    root, manifest, right_version, right = project_generation(project_name, index)
    _, _, left_version, left = project_generation(project_name, index - 1)
    if float(manifest.get("fps", 24)) != 24.0:
        raise ValueError("Restart Bridge requires a 24 fps H3 project")
    if any(record.get("sampling_pass") == "first" for record in (left, right)):
        raise ValueError("Restart Bridge requires two completed segments, not first-pass checkpoints")
    left_path = _project_child_path(root, left["video"])
    right_path = _project_child_path(root, right["video"])
    left_count, right_count = video_frame_count(left_path), video_frame_count(right_path)
    if left_count < BRIDGE_CONTEXT + BRIDGE_BEFORE or right_count < BRIDGE_CONTEXT + BRIDGE_AFTER:
        raise ValueError("Restart Bridge needs at least 53 frames before and 54 frames after the boundary")
    return {"left": video_identity(root, index - 1, left_version, left),
            "right": video_identity(root, index, right_version, right),
            "before": BRIDGE_BEFORE, "after": BRIDGE_AFTER,
            "left_frame_count": left_count, "right_frame_count": right_count}


def load_bridge_anchor(project_name: str, source: dict[str, Any], side: str) -> torch.Tensor:
    """Read one anchor only after checking that its pinned source is unchanged."""
    identity = source[side]
    index = identity["segment_index"]
    root, _, version, record = project_generation(project_name, index)
    if video_identity(root, index, version, record) != identity:
        raise ValueError("Bridge sources changed before encoding; regenerate this boundary")
    start = source["left_frame_count"] - source["before"] - BRIDGE_CONTEXT if side == "left" else source["after"]
    return read_video_frames(_project_child_path(root, record["video"]), start, BRIDGE_CONTEXT)


def bridge_tracks_info(info: dict, index: int) -> dict:
    """Reuse task media cropping on the original timeline across the boundary."""
    entries = h3_task_entries(info)
    entry = entries[index]
    start = int(entry["start_frame"]) - BRIDGE_BEFORE - BRIDGE_CONTEXT
    if start < 0:
        raise ValueError("Restart Bridge has insufficient source timeline before this boundary")
    result = dict(info)
    task = copy.deepcopy(entry["task"])
    task.update(start_frame=start, end_frame=start + BRIDGE_TOTAL)
    result["_h3_project_runtime"] = True
    result["_h3_bridge_runtime"] = True
    result["tracks"] = [{"id": "bridge-task", "type": "task", "segments": [task]},
                        *[track for track in info["tracks"] if track.get("type") != "task"]]
    result["task_markers"] = []
    result.pop("_preloaded_media", None)
    return result


def bridge_overlay(project_dir: Path, bridge: dict | None, left: dict, right: dict) -> dict:
    """Validate the exact selected sources and return a silent video overlay."""
    if not bridge:
        raise ValueError("Restart Bridge is missing; regenerate the boundary segment")
    for side, clip in (("left", left), ("right", right)):
        source = Path(clip["source"])
        identity = bridge[side]
        if source.name != identity["video"] or str(source.stat().st_mtime_ns) != identity["revision"]:
            raise ValueError("Restart Bridge sources changed; regenerate the boundary segment")
    if left["source_end_frame"] != bridge["left_frame_count"] or right["source_start_frame"] != 0:
        raise ValueError("Restart Bridge boundary was trimmed; restore the boundary or regenerate it")
    before, after = bridge["before"], bridge["after"]
    if left["end_frame"] - left["start_frame"] < before or right["end_frame"] - right["start_frame"] < after:
        raise ValueError("Restart Bridge exceeds the selected clips")
    path = _project_child_path(project_dir, bridge["file"])
    return {"source": str(path), "start_frame": right["start_frame"] - before,
            "end_frame": right["start_frame"] + after, "source_start_frame": 0,
            "audio_muted": True}
