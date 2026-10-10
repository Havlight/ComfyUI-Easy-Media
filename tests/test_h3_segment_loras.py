from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("segment_loras_unit", Path(__file__).resolve().parents[1] / "utils/h3_segment_loras.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def rule(name="a.safetensors", **kwargs):
    return dict(id=kwargs.pop("id", name), lora=name, enabled=True, start_segment=1,
                segment_count=-1, strength=.7, stage="all") | kwargs


def compile_plan(*items, mode="dual", ids=("a", "b", "c", "d"), **kwargs):
    return rules.compile_lora_plan({"version": 1, "rules": list(items)}, ids, mode, **kwargs)


@pytest.mark.parametrize("key,value", [("start_segment", 0), ("start_segment", True), ("start_segment", 1.5),
    ("segment_count", 0), ("segment_count", -2), ("segment_count", 2.0), ("enabled", "true"),
    ("strength", float("nan")), ("strength", float("inf")), ("strength", True), ("stage", "both"),
    ("lora", "/tmp/secret"), ("lora", "../a"), ("lora", "C:\\a")])
def test_rejects_invalid_row(key, value):
    with pytest.raises(rules.SegmentLoraError, match="row 1"):
        compile_plan(rule(**{key: value}))


@pytest.mark.parametrize("value", ["bad json", {}, {"version": True, "rules": []}, {"version": 2, "rules": []},
    {"version": 1, "rules": {}}, {"version": 1, "rules": [rule(), rule()]}])
def test_rejects_bad_schema(value):
    with pytest.raises(rules.SegmentLoraError):
        rules.normalize_lora_plan(value)


def test_absolute_numbering_for_partial_run_and_clamped_ranges():
    plan = compile_plan(rule(start_segment=3, segment_count=5), selected=[3])
    assert [bool(t["stages"]["first"]) for t in plan["tasks"]] == [False, False, True, True]
    assert [t["number"] for t in plan["tasks"] if t["selected"]] == [4]
    assert plan["rules"][0]["end"] == 4


@pytest.mark.parametrize("mode,preview,expected", [("single", False, ["first"]),
    ("dual", False, ["first", "second"]), ("dual", True, ["first"]),
    ("selflift", True, ["first", "second"]), ("passthrough", False, [])])
def test_actual_stages_and_passthrough_keep_numbering(mode, preview, expected):
    plan = compile_plan(rule(), mode=mode, first_pass_only=preview, passthrough={1})
    assert list(plan["tasks"][0]["stages"]) == expected
    assert plan["tasks"][1]["stages"] == {}
    assert plan["tasks"][2]["number"] == 3


def test_different_files_stack_in_row_order_and_stage_specific_same_file_is_valid():
    plan = compile_plan(rule(stage="first"), rule(id="second", stage="second"), rule("b", strength=-.5))
    rules.validate_lora_selection(plan, [0, 1, 2, 3])
    assert [item["lora"] for item in plan["tasks"][0]["stages"]["first"]] == ["a.safetensors", "b"]
    assert plan["tasks"][0]["stages"]["second"][0]["id"] == "second"


def test_all_expands_before_content_alias_duplicate_check_only_for_selected_scope():
    plan = compile_plan(rule(), rule("copy", start_segment=3, stage="second"),
                        identities={"a.safetensors": "hash", "copy": "hash"})
    rules.validate_lora_selection(plan, [0, 1])
    with pytest.raises(rules.SegmentLoraError, match="Task 3 second: duplicate LoRA in rows 1 and 2"):
        rules.validate_lora_selection(plan, [2])


def test_disabled_zero_strength_out_of_range_and_inactive_stage_do_not_need_files():
    plan = compile_plan(rule("", id="disabled", enabled=False), rule("missing", strength=0),
        rule("future", start_segment=8), rule("second", stage="second"), mode="single")
    rules.validate_lora_selection(plan, [0, 1, 2, 3])
    assert [r["unused_reason"] for r in plan["rules"]] == ["disabled", "zero_strength", "outside_timeline", "inactive_stage"]
    assert all(rules.lora_effect(t) == {} for t in plan["tasks"])


def test_incomplete_active_row_fails_execution_but_is_previewable():
    plan = compile_plan(rule("", id="new"))
    assert plan["rules"][0]["incomplete"]
    with pytest.raises(rules.SegmentLoraError, match="select a LoRA in row 1"):
        rules.validate_lora_selection(plan, [0])


def test_effect_identity_ignores_ui_ids_paths_range_and_inactive_rules():
    a = compile_plan(rule(), identities={"a.safetensors": "hash"})["tasks"][2]
    b = compile_plan(rule("alias", id="new", start_segment=3, segment_count=1), rule("inactive", strength=0),
                     identities={"alias": "hash"})["tasks"][2]
    assert rules.lora_effect(a) == rules.lora_effect(b)
    assert rules.lora_effect_fingerprint(rules.lora_effect(a)) == rules.lora_effect_fingerprint(rules.lora_effect(b))
    b["stages"]["first"][0]["strength"] += .1
    assert rules.lora_effect(a) != rules.lora_effect(b)
    assert rules.lora_effect_fingerprint(None) == rules.lora_effect_fingerprint({})


def test_identity_pending_never_silently_becomes_empty():
    with pytest.raises(rules.SegmentLoraError, match="pending"):
        rules.lora_effect(compile_plan(rule())["tasks"][0])


def test_plan_roundtrip_and_positional_reorder():
    value = {"version": 1, "rules": [rule(start_segment=2, segment_count=1)]}
    assert rules.normalize_lora_plan(json.dumps(value)) == value
    for ids in [("a", "b", "c"), ("new", "a", "b", "c"), ("c", "b", "a")]:
        plan = rules.compile_lora_plan(value, ids, "single")
        assert [t["segment_id"] for t in plan["tasks"] if t["stages"]["first"]] == [ids[1]]
