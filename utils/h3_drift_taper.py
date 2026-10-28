"""Per-task Drift-Control seam taper, independent of ComfyUI and torch.

Drift-Control releases the copied context with the noise schedule: a context
step with weight ``w`` carries ``w`` times the next sigma's noise. The steps
beside the seam taper from 1 down to a floor weight, so they stay visible from
the first steps, where composition is decided, while the rest follow the
schedule. Four steps tapering to zero is the original Drift-Control; zero steps
release every step with the schedule (matched Drift).
"""
from __future__ import annotations

import math
from typing import Any, NamedTuple

from .h3_native_timing import NativePlanError

DRIFT_MODE = "context_drift"
DRIFT_TAPER_MAX_STEPS = 12


class DriftTaper(NamedTuple):
    steps: int
    floor: float
    # Keep the high-resolution stage on the plain schedule.
    low_only: bool


DEFAULT_DRIFT_TAPER = DriftTaper(4, 0.0, False)
MATCHED_DRIFT_TAPER = DriftTaper(0, 0.0, False)


def drift_taper(content: dict[str, Any] | None, segment_id: str = "") -> DriftTaper | None:
    """Return the normalized taper of a Drift task, else ``None``.

    Other continuity modes and passthrough tasks ignore stored values, so they
    never change fingerprints or graphs. Settings without an effect normalize
    to matched Drift, so equal behavior has an equal signature.
    """
    if not isinstance(content, dict) or content.get("task_mode") == "passthrough":
        return None
    if content.get("continuity_mode") != DRIFT_MODE:
        return None
    steps = content.get("drift_taper_steps", DEFAULT_DRIFT_TAPER.steps)
    if isinstance(steps, bool) or not isinstance(steps, int) or not 0 <= steps <= DRIFT_TAPER_MAX_STEPS:
        raise NativePlanError(
            "DRIFT_TAPER_VALUE", f"Drift taper steps must be a whole number from 0 to {DRIFT_TAPER_MAX_STEPS}.",
            segment_id)
    floor = content.get("drift_taper_floor", DEFAULT_DRIFT_TAPER.floor)
    if (isinstance(floor, bool) or not isinstance(floor, (int, float))
            or not math.isfinite(floor) or not 0.0 <= floor <= 1.0):
        raise NativePlanError("DRIFT_TAPER_VALUE", "Drift taper floor must be a number from 0 to 1.", segment_id)
    low_only = content.get("drift_taper_low_only", DEFAULT_DRIFT_TAPER.low_only)
    if not isinstance(low_only, bool):
        raise NativePlanError("DRIFT_TAPER_VALUE", "Drift taper low_only must be true or false.", segment_id)
    if steps == 0 or floor == 1.0:
        return MATCHED_DRIFT_TAPER
    return DriftTaper(steps, float(floor), low_only)


def drift_stage_taper(taper: DriftTaper, high_resolution: bool) -> DriftTaper:
    """Return the taper one sampling stage uses."""
    return MATCHED_DRIFT_TAPER if high_resolution and taper.low_only else taper
