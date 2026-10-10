"""Serializable per-task LoRA plans. No ComfyUI, torch, file or media access."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


EMPTY_PLAN: dict[str, Any] = {"version": 1, "rules": []}
STAGES = ("first", "second")


class SegmentLoraError(ValueError):
    """An actionable configuration or compatibility error."""


def normalize_lora_plan(value: Any) -> dict[str, Any]:
    if value is None:
        value = EMPTY_PLAN
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as error:
            raise SegmentLoraError("Segment LoRA: invalid JSON.") from error
    if not isinstance(value, Mapping) or type(value.get("version")) is not int or value["version"] != 1:
        raise SegmentLoraError("Segment LoRA: expected plan version 1.")
    if not isinstance(value.get("rules"), list):
        raise SegmentLoraError("Segment LoRA: rules must be a list.")
    rules = []
    ids: set[str] = set()
    for index, raw in enumerate(value["rules"]):
        label = f"Segment LoRA row {index + 1}"
        if not isinstance(raw, Mapping):
            raise SegmentLoraError(f"{label}: expected an object.")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in ids:
            raise SegmentLoraError(f"{label}: id must be a unique nonempty string.")
        ids.add(rule_id)
        enabled = raw.get("enabled", True)
        if type(enabled) is not bool:
            raise SegmentLoraError(f"{label}: enabled must be a boolean.")
        name = raw.get("lora", "")
        if not isinstance(name, str) or "\x00" in name:
            raise SegmentLoraError(f"{label}: invalid LoRA filename.")
        # Paths are registered ComfyUI relative names, never host paths.
        name = name.replace("\\", "/")
        if name.startswith("/") or ":" in name or ".." in name.split("/"):
            raise SegmentLoraError(f"{label}: use a registered relative LoRA filename.")
        start, count = raw.get("start_segment", 1), raw.get("segment_count", -1)
        if type(start) is not int or start < 1:
            raise SegmentLoraError(f"{label}: start segment must be an integer >= 1.")
        if type(count) is not int or (count != -1 and count < 1):
            raise SegmentLoraError(f"{label}: segment count must be -1 or an integer >= 1.")
        strength = raw.get("strength", 1.0)
        if type(strength) not in (int, float) or not math.isfinite(strength):
            raise SegmentLoraError(f"{label}: strength must be finite.")
        stage = raw.get("stage", "all")
        if stage not in ("all", *STAGES):
            raise SegmentLoraError(f"{label}: stage must be all, first or second.")
        rules.append(dict(id=rule_id, enabled=enabled, lora=name, start_segment=start,
                          segment_count=count, strength=float(strength), stage=stage))
    return {"version": 1, "rules": rules}


def compile_lora_plan(value: Any, segment_ids: Sequence[str], sampling_mode: str,
                      *, first_pass_only: bool = False, passthrough: set[int] | None = None,
                      selected: Sequence[int] | None = None,
                      identities: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Compile the full timeline; selection controls execution, never numbering.

    Conflicts remain data so the UI can display them. Execution must call
    validate_lora_selection after resolving content identities for its scope.
    """
    plan = normalize_lora_plan(value)
    if sampling_mode not in ("single", "dual", "selflift", "passthrough"):
        raise SegmentLoraError(f"Segment LoRA: unknown sampling mode {sampling_mode!r}.")
    selected_set = set(range(len(segment_ids)) if selected is None else selected)
    passthrough = passthrough or set()
    stages = [] if sampling_mode == "passthrough" else ["first"]
    if sampling_mode == "selflift" or (sampling_mode == "dual" and not first_pass_only):
        stages.append("second")
    tasks: list[dict[str, Any]] = []
    usages: dict[str, int] = {rule["id"]: 0 for rule in plan["rules"]}
    conflicts: list[dict[str, Any]] = []
    for index, segment_id in enumerate(segment_ids):
        active = [] if index in passthrough else stages
        task: dict[str, Any] = dict(index=index, number=index + 1, segment_id=segment_id,
                                   selected=index in selected_set, stages={key: [] for key in active})
        for stage in active:
            seen: dict[str, int] = {}
            for row, rule in enumerate(plan["rules"], 1):
                end = len(segment_ids) if rule["segment_count"] == -1 else rule["start_segment"] + rule["segment_count"] - 1
                if (not rule["enabled"] or not rule["strength"] or rule["stage"] not in ("all", stage)
                        or not rule["start_segment"] <= index + 1 <= end):
                    continue
                usages[rule["id"]] += 1
                item = {**rule, "row": row}
                identity = (identities or {}).get(rule["lora"])
                if identity is not None:
                    item["sha256"] = identity
                key = identity or rule["lora"]
                if key in seen:
                    conflicts.append(dict(index=index, stage=stage, rows=[seen[key], row], lora=rule["lora"]))
                seen[key] = row
                task["stages"][stage].append(item)
        tasks.append(task)
    summaries = []
    for row, rule in enumerate(plan["rules"], 1):
        reason = None
        if not rule["enabled"]:
            reason = "disabled"
        elif not rule["strength"]:
            reason = "zero_strength"
        elif rule["start_segment"] > len(segment_ids):
            reason = "outside_timeline"
        elif rule["stage"] != "all" and rule["stage"] not in stages:
            reason = "inactive_stage"
        elif not usages[rule["id"]]:
            reason = "passthrough"
        end = len(segment_ids) if rule["segment_count"] == -1 else min(len(segment_ids), rule["start_segment"] + rule["segment_count"] - 1)
        summaries.append(dict(id=rule["id"], row=row, unused_reason=reason,
                              start=rule["start_segment"], end=end, incomplete=not rule["lora"]))
    return dict(version=1, tasks=tasks, rules=summaries, conflicts=conflicts)


def validate_lora_selection(compiled: dict[str, Any], indices: Sequence[int]) -> None:
    needed = set(indices)
    errors = []
    for conflict in compiled["conflicts"]:
        if conflict["index"] in needed:
            errors.append(f"Task {conflict['index'] + 1} {conflict['stage']}: duplicate LoRA in rows "
                          f"{conflict['rows'][0]} and {conflict['rows'][1]} ({conflict['lora']}).")
    for task in compiled["tasks"]:
        if task["index"] in needed:
            for items in task["stages"].values():
                errors.extend(f"Task {task['number']}: select a LoRA in row {item['row']}."
                              for item in items if not item["lora"])
    if errors:
        raise SegmentLoraError("Segment LoRA preflight:\n" + "\n".join(dict.fromkeys(errors)))


def lora_effect(task: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Persistent identity: only effective content, order, strength and stage."""
    result = {}
    for stage, rules in task["stages"].items():
        if rules:
            if any(not rule.get("sha256") for rule in rules):
                raise SegmentLoraError("LoRA file identity is pending verification.")
            result[stage] = [{"sha256": rule["sha256"], "strength": rule["strength"]} for rule in rules]
    return result


def lora_effect_fingerprint(effect: dict[str, Any] | None) -> str:
    return hashlib.sha256(json.dumps(effect or {}, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()
