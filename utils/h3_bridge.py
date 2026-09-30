"""Fixed-length replacement bridges for completed H3 project segments."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import torch

from .h3_project import _project_child_path, h3_task_entries
from .h3_previous_frame import project_generation, video_identity, read_video_frames, video_frame_count

BRIDGE_CONTEXT = 39
BRIDGE_BEFORE = 14
BRIDGE_AFTER = 15
BRIDGE_LENGTH = BRIDGE_BEFORE + BRIDGE_AFTER
BRIDGE_TOTAL = BRIDGE_CONTEXT * 2 + BRIDGE_LENGTH


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
