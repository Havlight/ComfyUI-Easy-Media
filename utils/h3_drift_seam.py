"""Per-task seam settings for Matched Drift continuity, independent of ComfyUI and torch.

Matched Drift releases every copied context step with the noise schedule. A
task may keep the last few steps beside the seam partly visible from the start
by capping their noise: at a moderate cap they show pose and motion but not
fine texture, so the seam is pinned without copying the predecessor's detail.
"""
from __future__ import annotations

import math
from typing import Any

from .h3_native_timing import NativePlanError

MATCHED_DRIFT_MODE = "context_drift_matched"
DRIFT_SEAM_MAX_STEPS = 4
DRIFT_SEAM_DEFAULT_NOISE = 0.5


def effective_drift_seam(content: dict[str, Any] | None, segment_id: str = "") -> tuple[int, float] | None:
    """Return ``(steps, noise_cap)`` for an active seam setting, else ``None``.

    Other continuity modes and passthrough tasks ignore stored values, so they
    never change fingerprints or graphs.
    """
    if (not isinstance(content, dict) or content.get("continuity_mode") != MATCHED_DRIFT_MODE
            or content.get("task_mode") == "passthrough"):
        return None
    steps = content.get("drift_seam_steps")
    if steps is None:
        return None
    if isinstance(steps, bool) or not isinstance(steps, int) or not 0 <= steps <= DRIFT_SEAM_MAX_STEPS:
        raise NativePlanError(
            "DRIFT_SEAM_VALUE", f"Seam steps must be a whole number from 0 to {DRIFT_SEAM_MAX_STEPS}.", segment_id)
    if steps == 0:
        return None
    noise = content.get("drift_seam_noise", DRIFT_SEAM_DEFAULT_NOISE)
    if (isinstance(noise, bool) or not isinstance(noise, (int, float))
            or not math.isfinite(noise) or not 0.0 <= noise <= 1.0):
        raise NativePlanError("DRIFT_SEAM_VALUE", "Seam noise cap must be a number from 0 to 1.", segment_id)
    return steps, float(noise)
