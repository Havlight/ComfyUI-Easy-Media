from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    # Fake parent packages keep the plugin and utils __init__ startup out of unit tests.
    for package, path in {"drift_seam_test": ROOT, "drift_seam_test.utils": ROOT / "utils"}.items():
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path)]
            sys.modules[package] = module
    return importlib.import_module(f"drift_seam_test.{name}")


seam = _load("utils.h3_drift_seam")
status = _load("utils.h3_native_status")
MATCHED = {"continuity_mode": "context_drift_matched"}


def test_seam_settings_apply_only_to_matched_drift_continuations():
    effective = seam.effective_drift_seam
    assert effective({**MATCHED, "drift_seam_steps": 2, "drift_seam_noise": 0.3}) == (2, 0.3)
    assert effective({**MATCHED, "drift_seam_steps": 1}) == (1, 0.5)
    assert effective({**MATCHED, "drift_seam_steps": 0, "drift_seam_noise": 0.3}) is None
    assert effective(MATCHED) is None
    assert effective({"continuity_mode": "context_drift", "drift_seam_steps": 2}) is None
    assert effective({**MATCHED, "task_mode": "passthrough", "drift_seam_steps": 2}) is None
    assert effective(None) is None


@pytest.mark.parametrize("values", [
    {"drift_seam_steps": 5}, {"drift_seam_steps": -1}, {"drift_seam_steps": 1.5}, {"drift_seam_steps": True},
    {"drift_seam_steps": 2, "drift_seam_noise": 1.2}, {"drift_seam_steps": 2, "drift_seam_noise": float("nan")},
    {"drift_seam_steps": 2, "drift_seam_noise": "0.5"},
])
def test_seam_settings_reject_invalid_values(values):
    with pytest.raises(ValueError, match=r"DRIFT_SEAM_VALUE.*\[b\]"):
        seam.effective_drift_seam({**MATCHED, **values}, "b")


def test_task_signature_changes_only_for_an_active_seam_setting():
    content = {**MATCHED, "task_mode": "default", "user_prompt": "go", "images": []}
    baseline = status.task_content_signature(content)
    assert "drift_seam" not in baseline
    assert status.task_content_signature({**content, "drift_seam_steps": 2, "drift_seam_noise": 0.3})["drift_seam"] == [2, 0.3]
    assert status.task_content_signature({**content, "drift_seam_steps": 0}) == baseline
    drift = {**content, "continuity_mode": "context_drift"}
    assert status.task_content_signature({**drift, "drift_seam_steps": 2}) == status.task_content_signature(drift)
    assert status.task_content_signature({**content, "drift_seam_steps": 9})["drift_seam"] == "(9, None)"
