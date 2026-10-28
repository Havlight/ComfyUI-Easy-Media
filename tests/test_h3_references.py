from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/h3_references.json").read_text(encoding="utf-8"))


def _load(name: str):
    # Fake parent packages keep the plugin and utils __init__ startup out of unit tests.
    for package, path in {"h3_references_test": ROOT, "h3_references_test.utils": ROOT / "utils"}.items():
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path)]
            sys.modules[package] = module
    return importlib.import_module(f"h3_references_test.{name}")


references = _load("utils.h3_references")


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=[case["name"] for case in FIXTURES["cases"]])
def test_shared_reference_plan_cases(case):
    plan = references.plan_task_references(
        case["tracks"], case["task"], case["next_start"], audio_only=case.get("audio_only", False))
    expected = case["expected"]
    fields = ("key", "kind", "start_frame", "frames", "soundtrack")
    assert [image["id"] for image in plan.images] == expected["images"]
    assert [{field: getattr(video, field) for field in fields} for video in plan.videos] == expected["videos"]
    assert [{field: getattr(audio, field) for field in fields} for audio in plan.audios] == expected["audios"]
    assert plan.labels() == expected["labels"]


def test_plan_is_json_ready_and_does_not_mutate_its_input():
    case = FIXTURES["cases"][1]
    tracks = json.loads(json.dumps(case["tracks"]))
    plan = references.plan_task_references(tracks, case["task"], case["next_start"])
    assert tracks == case["tracks"]
    assert json.loads(json.dumps(plan.as_dict()))["labels"] == case["expected"]["labels"]
