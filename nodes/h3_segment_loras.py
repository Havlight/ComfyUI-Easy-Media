"""Declarative segment LoRA configuration and a sequenced model boundary."""
from __future__ import annotations

from typing import Any

from comfy_api.latest import io

from ..utils.h3_segment_loras import normalize_lora_plan

TYPE_H3_LORA_PLAN = io.Custom(io_type="H3_LORA_PLAN")
TYPE_H3_LORA_RULES = io.Custom(io_type="H3_LORA_RULES")


class EasyH3SegmentLoras(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3SegmentLoras", display_name="H3 Segment LoRA",
                         category="EasyUse/H3",
                         inputs=[TYPE_H3_LORA_RULES.Input("rules")],
                         outputs=[TYPE_H3_LORA_PLAN.Output("segment_loras")])

    @classmethod
    def execute(cls, rules: Any) -> io.NodeOutput:
        return io.NodeOutput(normalize_lora_plan(rules))


class EasyH3SegmentLoraModels(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3SegmentLoraModels", display_name="H3 Segment LoRA Models",
                         category="EasyUse/H3/dev", is_dev_only=True, not_idempotent=True,
                         inputs=[io.Model.Input("model"), io.Model.Input("second_model"),
                                 io.AnyType.Input("run_state"), io.Int.Input("task_index"),
                                 io.AnyType.Input("previous")],
                         outputs=[io.Model.Output("first"), io.Model.Output("second")])

    @classmethod
    def execute(cls, model: Any, second_model: Any, run_state: dict[str, Any],
                task_index: int, previous: Any) -> io.NodeOutput:
        from ..utils.h3_lora_models import prepare_lora_model

        state = run_state["segment_loras"]
        task = state["tasks"][task_index]
        stages = task["stages"]
        first = prepare_lora_model(model, stages.get("first", []), state["files"], "first")
        second = prepare_lora_model(second_model, stages.get("second", []), state["files"], "second")
        return io.NodeOutput(first, second)
