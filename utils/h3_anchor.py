"""Per-task settings of Anchor continuity, independent of ComfyUI and torch.

Anchor continuity follows roycho87/geiru_context_suite (formerly clown_context_suite):
Geiru Context holds the first 22 of the 39 overlap frames at the start of the task and
lets the task draw the other 17 again, Geiru Context Noise gives the held latents after
the first part of the current noise, the anchor task's first single-frame latent (the
keystone) is locked into the task's first latent, and its last single-frame latent (the
capstone) is tacked onto the end of the video while sampling. The anchor is the first
task of the chain unless a later task restarts it, so a cut starts a new one.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from .h3_native_timing import ANCHOR_MODE, CONTEXT_FRAMES, NativePlanError

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
# Geiru Context Noise on the first pass: the share of the current noise the second held
# latent gets. 0 is off; the author tested 0.8 and lists 0.6 and 1.0 to test.
ANCHOR_NOISE_CHOICES = (0.0, 0.6, 0.8, 1.0)
DEFAULT_ANCHOR_NOISE = 0.8
# Of the 39 overlap frames, Geiru Context's recommended 22 are held (latents 0-6) and the
# last 17 (one block) are drawn again by the task, which owns them in the composed video.
ANCHOR_HELD_FRAMES = 22
# Results saved before the method version changed are stale: version 1 held the whole
# overlap without Context Noise.
ANCHOR_METHOD = 2


class AnchorSettings(NamedTuple):
    placement: str
    late: float
    noise: float
    # This task restarts the anchor: it is not anchored itself, and later tasks use it.
    restart: bool


DEFAULT_ANCHOR_SETTINGS = AnchorSettings(DEFAULT_ANCHOR_PLACEMENT, DEFAULT_ANCHOR_LATE, DEFAULT_ANCHOR_NOISE, False)


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
    noise = content.get("anchor_noise", DEFAULT_ANCHOR_NOISE)
    if isinstance(noise, bool) or noise not in ANCHOR_NOISE_CHOICES:
        raise NativePlanError("ANCHOR_VALUE", "Anchor context noise must be 0, 0.6, 0.8 or 1.", segment_id)
    restart = content.get("anchor_restart", False)
    if not isinstance(restart, bool):
        raise NativePlanError("ANCHOR_VALUE", "Anchor restart must be true or false.", segment_id)
    # A task that restarts the anchor is not anchored, and without a back latent the late lock
    # times nothing: settings without an effect normalize, so equal behavior has an equal signature.
    # Context Noise still applies to a restarting task's held frames.
    if restart:
        return AnchorSettings(DEFAULT_ANCHOR_PLACEMENT, DEFAULT_ANCHOR_LATE, float(noise), True)
    if ANCHOR_PLACEMENTS[placement][1] is None:
        late = DEFAULT_ANCHOR_LATE
    return AnchorSettings(placement, float(late), float(noise), restart)


def anchor_signature(content: dict[str, Any] | None) -> list[Any] | str | None:
    """The settings as the saved-result check compares them: none for other modes.

    Every Anchor task carries the method version, so results saved by an earlier method are stale.
    """
    try:
        settings = anchor_settings(content)
    except NativePlanError:
        return repr(tuple((content or {}).get(key)
                          for key in ("anchor_placement", "anchor_late", "anchor_noise", "anchor_restart")))
    return None if settings is None else [ANCHOR_METHOD, *settings]


def anchor_held_frames(parent_source_kind: str | None) -> int:
    """How many of the 39 overlap frames an Anchor task holds.

    Of a sampled predecessor it holds 22 and redraws 17, which the composed video plays over the
    predecessor's end. An imported or rebuilt predecessor plays its own end (the composed video
    cannot tell that the task continued from it), so the task holds the whole overlap and the
    seam stays continuous.
    """
    return ANCHOR_HELD_FRAMES if parent_source_kind == "native_sampler" else CONTEXT_FRAMES


def anchor_drawn_from(meta: dict[str, Any]) -> int:
    """The first frame a saved task drew itself: after the frames it held, none for a new chain.

    Versions saved before the held count was recorded held the whole overlap.
    """
    if not meta.get("context_frames"):
        return 0
    return int(meta.get("anchor_held_frames", CONTEXT_FRAMES))


def late_start_step(late: float, steps: int) -> int:
    """The step the back latent comes in: Geiru Late Lock's swap_step (4 of 8 at one half)."""
    return max(0, min(int(steps), round(float(late) * int(steps))))


def context_noise_ramp(held_latents: int, strength: float) -> list[tuple[int, float]]:
    """Geiru Context Noise's mask values on the held latents after the first, as (latent, value).

    The second held latent gets ``strength`` times the current noise, fading linearly to none on
    the last one (the join with the new latents), which stays locked; the first is the keystone's.
    """
    count = int(held_latents) - 1
    if count < 1 or float(strength) <= 0.0:
        return []
    ramp = [(1 + index, float(strength) * (1.0 - index / max(1, count - 1))) for index in range(count)]
    return [(latent, round(value, 6)) for latent, value in ramp if value > 0.0]
