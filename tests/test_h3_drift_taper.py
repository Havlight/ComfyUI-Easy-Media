from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    # Fake parent packages keep the plugin and utils __init__ startup out of unit tests.
    for package, path in {"drift_taper_test": ROOT, "drift_taper_test.utils": ROOT / "utils"}.items():
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path)]
            sys.modules[package] = module
    return importlib.import_module(f"drift_taper_test.{name}")


taper_module = _load("utils.h3_drift_taper")
status = _load("utils.h3_native_status")
DRIFT = {"continuity_mode": "context_drift"}


def test_drift_taper_defaults_to_the_original_drift_control():
    taper = taper_module.drift_taper
    assert taper(DRIFT) == (4, 0.0, False)
    assert taper({**DRIFT, "drift_taper_steps": 3, "drift_taper_floor": 0.25}) == (3, 0.25, False)
    assert taper({**DRIFT, "drift_taper_steps": 12, "drift_taper_floor": 0, "drift_taper_low_only": True}) == (12, 0.0, True)
    # Settings without an effect are matched Drift.
    assert taper({**DRIFT, "drift_taper_steps": 0, "drift_taper_floor": 0.5, "drift_taper_low_only": True}) == (0, 0.0, False)
    assert taper({**DRIFT, "drift_taper_steps": 3, "drift_taper_floor": 1.0}) == (0, 0.0, False)


def test_drift_taper_ignores_other_modes_and_passthrough():
    taper = taper_module.drift_taper
    assert taper({"continuity_mode": "context", "drift_taper_steps": 99}) is None
    assert taper({"continuity_mode": "shot"}) is None
    assert taper({**DRIFT, "task_mode": "passthrough", "drift_taper_steps": 99}) is None
    assert taper(None) is None


def test_stage_taper_keeps_high_resolution_on_the_schedule_when_low_only():
    stage = taper_module.drift_stage_taper
    low_only = taper_module.DriftTaper(3, 0.25, True)
    assert stage(low_only, high_resolution=False) == low_only
    assert stage(low_only, high_resolution=True) == (0, 0.0, False)
    both = taper_module.DriftTaper(3, 0.25, False)
    assert stage(both, high_resolution=True) == both


@pytest.mark.parametrize("values", [
    {"drift_taper_steps": 13}, {"drift_taper_steps": -1}, {"drift_taper_steps": 1.5}, {"drift_taper_steps": True},
    {"drift_taper_floor": 1.2}, {"drift_taper_floor": float("nan")}, {"drift_taper_floor": "0.5"},
    {"drift_taper_low_only": 1},
])
def test_drift_taper_rejects_invalid_values(values):
    with pytest.raises(ValueError, match=r"DRIFT_TAPER_VALUE.*\[b\]"):
        taper_module.drift_taper({**DRIFT, **values}, "b")


def test_task_signature_changes_only_for_a_custom_taper():
    content = {**DRIFT, "task_mode": "default", "user_prompt": "go", "images": []}
    baseline = status.task_content_signature(content)
    assert "drift_taper" not in baseline
    assert status.task_content_signature({**content, "drift_taper_steps": 4, "drift_taper_floor": 0.0}) == baseline
    custom = status.task_content_signature({**content, "drift_taper_steps": 3, "drift_taper_floor": 0.25})
    assert custom["drift_taper"] == [3, 0.25, False]
    assert status.task_content_signature({**content, "drift_taper_low_only": True})["drift_taper"] == [4, 0.0, True]
    shot = {**content, "continuity_mode": "context"}
    assert status.task_content_signature({**shot, "drift_taper_steps": 2}) == status.task_content_signature(shot)
    assert status.task_content_signature({**content, "drift_taper_steps": 99})["drift_taper"] == "(99, None, None)"

