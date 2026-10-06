"""Shared project interpretation, file preflight and read-only LoRA status."""
from __future__ import annotations

from typing import Any

from .h3_native_timing import compile_native_plan, native_task_segments
from .h3_segment_loras import SegmentLoraError, compile_lora_plan, lora_effect, validate_lora_selection


def compile_project_loras(info: dict[str, Any], value: Any, recipe: dict[str, Any],
                          selected: list[int] | None = None,
                          identities: dict[str, str] | None = None) -> dict[str, Any]:
    plans = compile_native_plan(info)
    return compile_lora_plan(value, [plan.segment_id for plan in plans], recipe['sampling_mode'],
        first_pass_only=bool(recipe.get('first_pass_only')), passthrough={i for i, plan in enumerate(plans) if plan.passthrough},
        selected=selected, identities=identities)


def task_dependencies(info: dict[str, Any]) -> list[set[int]]:
    plans = compile_native_plan(info)
    tasks = native_task_segments(info)
    indices = {plan.segment_id: index for index, plan in enumerate(plans)}
    result = []
    for index, plan in enumerate(plans):
        parents = {indices[plan.parent_segment_id]} if plan.parent_segment_id else set()
        if index and not plan.passthrough and any(
                image.get('source_type') == 'previous_frame' and not image.get('muted')
                for image in tasks[index].get('content', {}).get('images', [])):
            parents.add(index - 1)
        result.append(parents)
    return result


def required_lora_tasks(info: dict[str, Any], selected: list[int]) -> list[int]:
    dependencies = task_dependencies(info)
    needed = set(selected)
    pending = list(selected)
    while pending:
        for parent in dependencies[pending.pop()] - needed:
            needed.add(parent)
            pending.append(parent)
    return sorted(needed)


def preflight_project_loras(info: dict[str, Any], value: Any, recipe: dict[str, Any],
                            selected: list[int], model: Any, second_model: Any) -> dict[str, Any]:
    from .h3_lora_models import file_identity, validate_model_lora

    needed = sorted(selected) if recipe['sampling_mode'] == 'passthrough' else required_lora_tasks(info, selected)
    compiled = compile_project_loras(info, value, recipe, selected)
    validate_lora_selection(compiled, needed)
    names = dict.fromkeys(rule['lora'] for index in needed
                         for rules in compiled['tasks'][index]['stages'].values() for rule in rules)
    files = {}
    for name in names:
        try:
            files[name] = file_identity(name)
        except (OSError, ValueError) as error:
            locations = [f"task {index + 1} {stage} row {rule['row']}" for index in needed
                         for stage, rules in compiled['tasks'][index]['stages'].items()
                         for rule in rules if rule['lora'] == name]
            raise SegmentLoraError(f"{', '.join(locations)}: {error}") from error
    compiled = compile_project_loras(info, value, recipe, selected,
                                     {name: identity['sha256'] for name, identity in files.items()})
    validate_lora_selection(compiled, needed)
    # Only models sampled in this run need compatibility checks. Ancestors need
    # content identity to compare their saved recipe, not newly patched models.
    checked: set[tuple[int, str, str]] = set()
    for index in selected:
        for stage, rules in compiled['tasks'][index]['stages'].items():
            base = model if stage == 'first' else second_model
            for rule in rules:
                key = (id(base), str(getattr(base, 'patches_uuid', '')), rule['sha256'])
                if key not in checked:
                    try:
                        validate_model_lora(base, files[rule['lora']])
                    except (ValueError, RuntimeError) as error:
                        raise SegmentLoraError(f"Task {index + 1} {stage} row {rule['row']}: {error}") from error
                    checked.add(key)
    compiled['files'] = files
    compiled['required'] = needed
    compiled['effects'] = {compiled['tasks'][index]['segment_id']: lora_effect(compiled['tasks'][index]) for index in needed}
    return compiled


def preview_lora_effects(compiled: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """Never read weights/hash new files while a user edits the timeline."""
    from .h3_lora_models import file_identity

    identities = {}
    for task in compiled['tasks']:
        for rules in task['stages'].values():
            for rule in rules:
                name = rule['lora']
                if name and name not in identities:
                    try:
                        identities[name] = file_identity(name, cached_only=True)
                    except (OSError, ValueError) as error:
                        # The full preflight reports missing files. Status stays
                        # explicitly pending instead of silently claiming saved.
                        identities[name] = None
                        compiled.setdefault('file_errors', {})[name] = str(error)
                if identities.get(name):
                    rule['sha256'] = identities[name]['sha256']
    return {task['segment_id']: (None if any(not rule.get('sha256')
            for rules in task['stages'].values() for rule in rules) else lora_effect(task)) for task in compiled['tasks']}


def task_lora_recipe(task: dict[str, Any]) -> dict[str, Any]:
    return {'segment_loras': lora_effect(task), 'segment_lora_files': {
        stage: [{key: rule[key] for key in ('lora', 'sha256', 'strength')} for rule in rules]
        for stage, rules in task['stages'].items() if rules}}
