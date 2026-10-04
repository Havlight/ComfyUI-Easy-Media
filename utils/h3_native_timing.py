"""Versioned H3 generation geometry, independent of ComfyUI and torch.

Video frames use a 24 Hz clock. Audio origins use integer 1/120-second units
(one video frame = 5 units, one 40 Hz audio latent tick = 3 units).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

NATIVE_VERSION = 2
CONTEXT_FRAMES = 39
FRAME_STEP = 17
MAX_RAW_FRAMES = 3592
CONTINUITY_MODES = frozenset({"shot", "context", "context_drift", "context_masked"})


class NativePlanError(ValueError):
    """A user-actionable failure with a stable code and segment identity."""

    def __init__(self, code: str, message: str, segment_id: str = "") -> None:
        self.code = code
        self.segment_id = segment_id
        super().__init__(f"H3 {code}{' [' + segment_id + ']' if segment_id else ''}: {message}")


def native_policy(info: dict[str, Any]) -> dict[str, Any] | None:
    value = info.get("h3_native")
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("version") not in {1, NATIVE_VERSION}:
        raise NativePlanError("POLICY_VERSION", "Unsupported native timing policy; upgrade the workflow.")
    if "allow_vae_fallback" in value and not isinstance(value["allow_vae_fallback"], bool):
        raise NativePlanError("POLICY_VALUE", "allow_vae_fallback must be a boolean.")
    return {"version": NATIVE_VERSION, "allow_vae_fallback": value.get("allow_vae_fallback", False)}


def round_ratio(numerator: int, denominator: int) -> int:
    """Nearest integer, ties toward the smaller integer, including negatives."""
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(remainder * 2 > denominator)


def video_steps(frames: int) -> int:
    if frames < 5 or frames % FRAME_STEP != 5:
        raise NativePlanError("VIDEO_GRID", f"{frames} frames are not on the 17k+5 video grid.")
    return 2 + 5 * ((frames - 5) // FRAME_STEP)


def snap_duration(frames: int | float, continuation: bool) -> int:
    import math

    if isinstance(frames, bool) or not math.isfinite(frames):
        raise NativePlanError("DURATION", "Duration must be finite.")
    remainder = 0 if continuation else 5
    minimum = FRAME_STEP if continuation else CONTEXT_FRAMES
    maximum = MAX_RAW_FRAMES - CONTEXT_FRAMES if continuation else MAX_RAW_FRAMES
    lower = math.floor((frames - remainder) / FRAME_STEP) * FRAME_STEP + remainder
    nearest = lower if frames - lower <= FRAME_STEP / 2 else lower + FRAME_STEP
    return max(minimum, min(maximum, nearest))


@dataclass(frozen=True)
class NativeTaskPlan:
    segment_id: str
    start_frame: int
    end_frame: int
    continuity_mode: str
    parent_segment_id: str | None
    context_frames: int
    raw_start_frame: int
    raw_frames: int
    passthrough: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_native_plan(info: dict[str, Any]) -> list[NativeTaskPlan]:
    if native_policy(info) is None:
        return []
    if info.get("format", "MiniMax") != "MiniMax" or float(info.get("frame_rate", 24)) != 24:
        raise NativePlanError("FORMAT", "Native H3 generation requires MiniMax at 24 fps.")
    segments = native_task_segments(info)
    result: list[NativeTaskPlan] = []
    seen: set[str] = set()
    for segment in segments:
        sid = segment.get("id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise NativePlanError("SEGMENT_ID", "Each task needs a unique, stable ID.")
        seen.add(sid)
        start, end = segment.get("start_frame"), segment.get("end_frame")
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start):
            raise NativePlanError("RANGE", "Use nonnegative integer half-open frame ranges.", sid)
        content = segment.get("content", {})
        mode = content.get("continuity_mode", "shot")
        mode = "context_drift" if mode == "context_swap" else mode
        if mode not in CONTINUITY_MODES:
            raise NativePlanError("METHOD", f"Unsupported continuation method: {mode}.", sid)
        passthrough = content.get("task_mode") == "passthrough"
        context = mode != "shot" and not passthrough and not (mode == "context_masked" and not result)
        previous = result[-1] if result else None
        if previous and start < previous.end_frame:
            raise NativePlanError("OVERLAP", "Generation tasks cannot overlap; context is managed internally.", sid)
        if context and (previous is None or start != previous.end_frame):
            raise NativePlanError("PARENT", "Continuation needs an adjacent predecessor; use Shot for a new chain.", sid)
        if context and previous and previous.raw_frames < CONTEXT_FRAMES:
            raise NativePlanError("CONTEXT_SHORT", "The predecessor cannot supply 39 context frames.", sid)
        duration = end - start
        if not passthrough and duration != snap_duration(duration, context):
            suggestion = snap_duration(duration, context)
            raise NativePlanError("DURATION_GRID", f"Use {suggestion} delivered frames instead of {duration}.", sid)
        prefix = CONTEXT_FRAMES if context else 0
        result.append(NativeTaskPlan(sid, start, end, mode,
                                    previous.segment_id if context and previous else None,
                                    prefix, start - prefix, duration + prefix, passthrough))
    return result


def native_task_segments(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Markers split legal tasks into stable virtual tasks, without moving media."""
    from copy import deepcopy

    tasks = sorted([s for t in info.get("tracks", []) if t.get("type") == "task"
                    for s in t.get("segments", [])], key=lambda s: (s.get("start_frame", 0), s.get("id", "")))
    markers = sorted(info.get("task_markers", []), key=lambda m: m.get("frame", 0))
    result: list[dict[str, Any]] = []
    for task in tasks:
        current = deepcopy(task)
        for marker in markers:
            frame = marker.get("frame", 0)
            if not current["start_frame"] < frame < current["end_frame"]:
                continue
            if current.get("content", {}).get("task_mode") == "passthrough":
                raise NativePlanError("MARKER_IMPORT", "Split imported source tasks explicitly instead of placing a generation marker inside them.", task["id"])
            continuation = current.get("content", {}).get("continuity_mode", "shot") != "shot"
            cut = native_split_frame(current["start_frame"], current["end_frame"], frame, continuation)
            if cut != frame:
                raise NativePlanError("MARKER_GRID", f"Move this marker to frame {cut}.", task["id"])
            result.append({**current, "end_frame": cut})
            current = {**current, "id": f"{task['id']}:marker:{marker.get('id', frame)}", "start_frame": cut,
                       "content": {**current.get("content", {}), "continuity_mode": current.get("content", {}).get("continuity_mode", "context") if continuation else "context"}}
        result.append(current)
    return result


def normalize_native_timeline(data: dict[str, Any]) -> dict[str, Any]:
    """Convert old H3 data at the boundary; all generated tasks use one geometry.

    Keep media timing, historical preferences and retired method identities.
    Validation of supported execution methods is a separate preflight step.
    """
    from copy import deepcopy

    output = {**data, "tracks": deepcopy(data.get("tracks", [])),
              "task_markers": deepcopy(data.get("task_markers", []))}
    policy = native_policy(output) or {"version": NATIVE_VERSION, "allow_vae_fallback": False}
    output["h3_native"] = policy
    if float(output.get("frame_rate", 24)) != 24:
        raise NativePlanError("FORMAT", "H3 generation requires a 24 fps timeline; convert its frame rate first.")
    output.setdefault("frame_rate", 24)
    tasks = []
    for ti, track in enumerate(output.get("tracks", [])):
        track.setdefault("id", f"legacy-track-{ti}")
        if track.get("type") != "task":
            continue
        for si, task in enumerate(track.get("segments", [])):
            task.setdefault("id", f"legacy-task-{track.get('id', ti)}-{si}")
            tasks.append((task, track))
    tasks.sort(key=lambda pair: (pair[0]["start_frame"], pair[0]["id"]))
    previous = None
    shift = 0
    seen: set[str] = set()
    for task, track in tasks:
        sid = task["id"]
        if not isinstance(sid, str) or not sid or sid in seen:
            raise NativePlanError("SEGMENT_ID", "Each task needs a unique stable ID.")
        seen.add(sid)
        start, end = task["start_frame"], task["end_frame"]
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise NativePlanError("RANGE", "Use nonnegative integer half-open frame ranges.", sid)
        content = task.setdefault("content", {})
        mode = content.get("continuity_mode", "shot")
        mode = {"context_swap": "context_drift", "context_test": "context"}.get(mode, mode)
        passthrough = content.get("task_mode") == "passthrough"
        continuation = previous is not None and mode != "shot" and not passthrough
        duration = end - start
        if passthrough and duration < CONTEXT_FRAMES:
            raise NativePlanError("SEED_SHORT", "A source task needs at least 39 frames.", sid)
        if not passthrough:
            duration = snap_duration(duration, continuation)
        new_start = previous["end_frame"] if continuation else max(previous["end_frame"] if previous else 0, start + shift)
        new_end = new_start + duration
        new_mode = mode if continuation or mode == "context_masked" else "shot"
        if track.get("locked") and (new_start != start or new_end != end or new_mode != content.get("continuity_mode", "shot")):
            raise NativePlanError("TRACK_LOCKED", "Unlock the task track before aligning its timing.", sid)
        task.update(start_frame=new_start, end_frame=new_end)
        content["continuity_mode"] = new_mode
        previous = task
        shift = new_end - end
    for task, track in tasks:
        start = task["start_frame"]
        continuation = task.get("content", {}).get("continuity_mode", "shot") != "shot"
        for marker in sorted(output.get("task_markers", []), key=lambda item: item["frame"]):
            if not start < marker["frame"] < task["end_frame"]:
                continue
            if task.get("content", {}).get("task_mode") == "passthrough":
                raise NativePlanError("MARKER_IMPORT", "Split imported source tasks explicitly.", task["id"])
            frame = native_split_frame(start, task["end_frame"], marker["frame"], continuation)
            if frame != marker["frame"] and track.get("locked"):
                raise NativePlanError("TRACK_LOCKED", "Unlock the task track before aligning its marker.", task["id"])
            marker["frame"] = frame
            start, continuation = frame, True
    output["total_length"] = max(output.get("total_length", 0),
        max((s["end_frame"] for t in output.get("tracks", []) for s in t.get("segments", [])), default=0))
    return output


def reconcile_native_override(data: dict[str, Any]) -> dict[str, Any]:
    """Prompt override uses the same transaction as editor/load normalization."""
    return normalize_native_timeline(data)


def audio_clock(plan: NativeTaskPlan, parent: dict[str, Any] | None = None) -> dict[str, int]:
    """Keep the inherited audio grid within one third tick of the video seam.

    Allocate enough native audio ticks to cover the raw video end. This can be
    one tick longer than core's rounded empty canvas. No encoded audio is
    resampled, and source tails beyond the delivered seam are not inherited.
    """
    source_end = source_start = 0
    if plan.context_frames:
        if parent is None:
            raise NativePlanError("SOURCE_MISSING", "A native predecessor is required.", plan.segment_id)
        parent_origin = int(parent["audio_origin_units"])
        source_end = round_ratio(plan.start_frame * 5 - parent_origin, 3)
        source_start = source_end - plan.context_frames * 5 // 3
        if source_start < 0 or source_end > int(parent["audio_ticks"]):
            raise NativePlanError("AUDIO_RANGE", "The predecessor does not cover the delivered audio seam.", plan.segment_id)
        origin = parent_origin + source_start * 3
    else:
        origin = plan.raw_start_frame * 5
    ticks = -(-(plan.end_frame * 5 - origin) // 3)
    return {"audio_origin_units": origin, "audio_ticks": ticks,
            "source_audio_start": source_start, "source_audio_end": source_end}


def source_video_slice(plan: NativeTaskPlan, parent: dict[str, Any]) -> tuple[int, int]:
    local_end = plan.start_frame - int(parent["raw_start_frame"])
    end = video_steps(local_end)
    start = end - video_steps(plan.context_frames)
    if start < 0 or start % 5 or end > video_steps(int(parent["raw_frames"])):
        raise NativePlanError("SOURCE_PHASE", "The delivered seam is outside the source's native video grid.", plan.segment_id)
    return start, end


def sample_at_frame(frame: int, sample_rate: int) -> int:
    return round_ratio(frame * sample_rate, 24)


def native_split_frame(start: int, end: int, requested: int, continuation: bool) -> int:
    minimum = FRAME_STEP if continuation else CONTEXT_FRAMES
    left = snap_duration(requested - start, continuation)
    left = min(left, end - start - FRAME_STEP)
    if left < minimum or left != snap_duration(left, continuation):
        raise NativePlanError("SPLIT_SHORT", "Both split tasks need a legal generation window.")
    return start + left
