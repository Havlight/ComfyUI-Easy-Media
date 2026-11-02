"""Per-task settings of Anchor continuity, independent of ComfyUI and torch.

Anchor continuity follows roycho87/clown_context_suite's Clown Context, Clown
Keystone and Clown Late Lock: the previous task's tail is held at the start of
the task, the anchor task's first single-frame latent (the keystone) is locked
into the task's first latent, and its last single-frame latent (the capstone)
is tacked onto the end of the video while sampling. The anchor is the first
task of the chain unless a later task restarts it, so a cut starts a new one.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from .h3_native_timing import ANCHOR_MODE, NativePlanError

# Where the keystone or capstone goes: front = locked into the first latent,
# back = tacked onto the end of the video while sampling.
ANCHOR_PLACEMENTS = {
    "keystone_front": ("keystone", None),
    "keystone_front_back": ("keystone", "keystone"),
    "capstone_front_back": ("capstone", "capstone"),
    "keystone_front_capstone_back": ("keystone", "capstone"),
}
DEFAULT_ANCHOR_PLACEMENT = "keystone_front_capstone_back"
# How far into the first pass the back latent comes in, as a share of its steps.
ANCHOR_LATE_CHOICES = (0.0, 0.25, 0.5, 0.75)
DEFAULT_ANCHOR_LATE = 0.5


class AnchorSettings(NamedTuple):
    placement: str
    late: float
    # This task restarts the anchor: it is not anchored itself, and later tasks use it.
    restart: bool


DEFAULT_ANCHOR_SETTINGS = AnchorSettings(DEFAULT_ANCHOR_PLACEMENT, DEFAULT_ANCHOR_LATE, False)


def anchor_settings(content: dict[str, Any] | None, segment_id: str = "") -> AnchorSettings | None:
    """Return the settings of an Anchor task, else ``None``."""
    if not isinstance(content, dict) or content.get("task_mode") == "passthrough":
        return None
    if content.get("continuity_mode") != ANCHOR_MODE:
        return None
    placement = content.get("anchor_placement", DEFAULT_ANCHOR_PLACEMENT)
    if not isinstance(placement, str) or placement not in ANCHOR_PLACEMENTS:
        raise NativePlanError("ANCHOR_VALUE", f"Anchor placement must be one of {', '.join(ANCHOR_PLACEMENTS)}.", segment_id)
    late = content.get("anchor_late", DEFAULT_ANCHOR_LATE)
    if isinstance(late, bool) or late not in ANCHOR_LATE_CHOICES:
        raise NativePlanError("ANCHOR_VALUE", "Anchor late lock must be 0, 0.25, 0.5 or 0.75.", segment_id)
    restart = content.get("anchor_restart", False)
    if not isinstance(restart, bool):
        raise NativePlanError("ANCHOR_VALUE", "Anchor restart must be true or false.", segment_id)
    # A task that restarts the anchor is not anchored, and without a back latent the late lock
    # times nothing: settings without an effect normalize, so equal behavior has an equal signature.
    if restart:
        return AnchorSettings(DEFAULT_ANCHOR_PLACEMENT, DEFAULT_ANCHOR_LATE, True)
    if ANCHOR_PLACEMENTS[placement][1] is None:
        late = DEFAULT_ANCHOR_LATE
    return AnchorSettings(placement, float(late), restart)


def anchor_signature(content: dict[str, Any] | None) -> list[Any] | str | None:
    """The settings as the saved-result check compares them: none for defaults or other modes."""
    try:
        settings = anchor_settings(content)
    except NativePlanError:
        return repr(tuple((content or {}).get(key) for key in ("anchor_placement", "anchor_late", "anchor_restart")))
    return None if settings is None or settings == DEFAULT_ANCHOR_SETTINGS else list(settings)


def late_start_step(late: float, steps: int) -> int:
    """The step the back latent comes in: Clown Late Lock's swap_step (4 of 8 at one half)."""
    return max(0, min(int(steps), round(float(late) * int(steps))))
