"""Versioned H3 generation geometry, independent of ComfyUI and torch.

Video frames use a 24 Hz clock. Audio origins use integer 1/120-second units
(one video frame = 5 units, one 40 Hz audio latent tick = 3 units).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

NATIVE_VERSION = 1
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
    if not isinstance(value, dict) or value.get("version") != NATIVE_VERSION:
        raise NativePlanError("POLICY_VERSION", "Unsupported native timing policy; upgrade the workflow.")
    if not isinstance(value.get("allow_vae_fallback"), bool):
        raise NativePlanError("POLICY_VALUE", "allow_vae_fallback must be a boolean.")
    return {"version": NATIVE_VERSION, "allow_vae_fallback": value["allow_vae_fallback"]}


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
    segments = [s for t in info.get("tracks", []) if t.get("type") == "task"
                for s in t.get("segments", [])]
    segments.sort(key=lambda s: (s.get("start_frame", 0), s.get("id", "")))
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
        context = mode != "shot" and not passthrough
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
