#!/usr/bin/env python3
"""Inspect or safely patch the MultiTrack Editor and Project of a ComfyUI workflow file.

Settings go by the names ComfyUI saves them under (widgets_values_named), the same names
plan_workflow.py read lists and apply --set takes; the running ComfyUI checks them against
the nodes' definitions, so it must be reachable.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ComfyUI's embedded Python does not put a script's own folder on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

EDITOR_TYPE = "easy multiTrackEditor"
PROJECT_TYPE = "easy multitrackProject"
# What a plan's editor section holds besides settings.
EDITOR_PLAN_KEYS = {"node_id", "track_data", "recalculate_total_length"}
# Checks setting changes to a node against its definition in ComfyUI (/easy-media/workflow/settings):
# check(node, changes) -> {"changes", "settings", "widgets_values_named", "widgets_values"}.
SettingsCheck = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class WorkflowError(ValueError):
    """Raised when a workflow cannot be patched without ambiguity."""


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be a JSON object: {path}")
    return value


def nodes_by_type(workflow: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [
        node for node in workflow.get("nodes", [])
        if isinstance(node, dict) and node.get("type") == node_type
    ]


def node_by_id(workflow: dict[str, Any], node_id: int, node_type: str) -> dict[str, Any]:
    matches = [
        node for node in workflow.get("nodes", [])
        if isinstance(node, dict) and node.get("id") == node_id
    ]
    if len(matches) != 1:
        raise WorkflowError(f"Expected exactly one node with id {node_id}, found {len(matches)}")
    node = matches[0]
    if node.get("type") != node_type:
        raise WorkflowError(
            f"Node {node_id} is {node.get('type')!r}, expected {node_type!r}"
        )
    return node


def choose_project(workflow: dict[str, Any], requested_id: Any = None) -> dict[str, Any]:
    if requested_id is not None:
        return node_by_id(workflow, int(requested_id), PROJECT_TYPE)
    projects = nodes_by_type(workflow, PROJECT_TYPE)
    if len(projects) != 1:
        ids = [node.get("id") for node in projects]
        raise WorkflowError(f"Project node is ambiguous; candidates: {ids}")
    return projects[0]


def linked_editor_id(workflow: dict[str, Any], project: dict[str, Any]) -> int | None:
    input_entry = next(
        (
            item for item in project.get("inputs", [])
            if isinstance(item, dict) and item.get("name") == "tracks_info"
        ),
        None,
    )
    link_id = input_entry.get("link") if input_entry else None
    if link_id is None:
        return None
    links = [
        link for link in workflow.get("links", [])
        if isinstance(link, list) and len(link) >= 5 and link[0] == link_id
    ]
    if len(links) != 1:
        raise WorkflowError(f"tracks_info link {link_id!r} is missing or duplicated")
    return int(links[0][1])


def choose_editor(
    workflow: dict[str, Any],
    project: dict[str, Any],
    requested_id: Any = None,
) -> dict[str, Any]:
    linked_id = linked_editor_id(workflow, project)
    if requested_id is not None:
        editor = node_by_id(workflow, int(requested_id), EDITOR_TYPE)
        if linked_id is not None and editor.get("id") != linked_id:
            raise WorkflowError(
                f"Editor {editor.get('id')} is not connected to project {project.get('id')}; "
                f"connected editor is {linked_id}"
            )
        return editor
    if linked_id is not None:
        return node_by_id(workflow, linked_id, EDITOR_TYPE)
    editors = nodes_by_type(workflow, EDITOR_TYPE)
    if len(editors) != 1:
        ids = [node.get("id") for node in editors]
        raise WorkflowError(f"Editor node is ambiguous; candidates: {ids}")
    return editors[0]


def track_data_widget(node: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, value in enumerate(node.get("widgets_values", [])):
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and {"tracks", "total_length", "frame_rate"} <= parsed.keys():
            candidates.append((index, parsed))
    if len(candidates) != 1:
        raise WorkflowError(
            f"Editor {node.get('id')} must contain exactly one TRACK_DATA widget; "
            f"found {len(candidates)}"
        )
    return candidates[0]


def validate_track_data(
    data: dict[str, Any],
    *,
    recalculate: bool,
) -> None:
    tracks = data.get("tracks")
    frame_rate = data.get("frame_rate")
    if not isinstance(tracks, list):
        raise WorkflowError("track_data.tracks must be an array")
    if not isinstance(frame_rate, int) or isinstance(frame_rate, bool) or frame_rate < 1:
        raise WorkflowError("track_data.frame_rate must be a positive integer")

    track_ids: set[str] = set()
    segment_ids: set[str] = set()
    locked_media_tracks: list[tuple[str, str]] = []
    shared_media_references: list[tuple[str, str, str]] = []
    max_end = 0
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict):
            raise WorkflowError(f"tracks[{track_index}] must be an object")
        track_id = track.get("id")
        if not isinstance(track_id, str) or not track_id:
            raise WorkflowError(f"tracks[{track_index}].id must be a non-empty string")
        if track_id in track_ids:
            raise WorkflowError(f"Duplicate track id: {track_id}")
        track_ids.add(track_id)
        track_type = track.get("type")
        if track_type not in {"task", "video", "audio", "subtitle"}:
            raise WorkflowError(f"Unsupported track type at tracks[{track_index}]: {track_type!r}")
        if "audio_locked" in track and not isinstance(track.get("audio_locked"), bool):
            raise WorkflowError(f"Track {track_id} audio_locked must be a boolean")
        if track.get("audio_locked") is True:
            if track_type not in {"audio", "video"}:
                raise WorkflowError(
                    f"Only audio/video tracks may set audio_locked: true (track {track_id})"
                )
            locked_media_tracks.append((track_id, str(track_type)))
        segments = track.get("segments")
        if not isinstance(segments, list):
            raise WorkflowError(f"tracks[{track_index}].segments must be an array")
        previous_end = -1
        for segment_index, segment in enumerate(segments):
            where = f"tracks[{track_index}].segments[{segment_index}]"
            if not isinstance(segment, dict):
                raise WorkflowError(f"{where} must be an object")
            segment_id = segment.get("id")
            if not isinstance(segment_id, str) or not segment_id:
                raise WorkflowError(f"{where}.id must be a non-empty string")
            if segment_id in segment_ids:
                raise WorkflowError(f"Duplicate segment id: {segment_id}")
            segment_ids.add(segment_id)
            start = segment.get("start_frame")
            end = segment.get("end_frame")
            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                raise WorkflowError(f"{where}.start_frame must be a non-negative integer")
            if not isinstance(end, int) or isinstance(end, bool) or end <= start:
                raise WorkflowError(f"{where}.end_frame must be greater than start_frame")
            if start < previous_end:
                raise WorkflowError(f"Segments overlap or are unsorted in track {track_id}")
            previous_end = end
            max_end = max(max_end, end)
            content = segment.get("content")
            if not isinstance(content, dict):
                raise WorkflowError(f"{where}.content must be an object")
            if track_type in {"video", "audio"} and content.get("media_type") != track_type:
                raise WorkflowError(f"{where}.content.media_type must be {track_type!r}")
            if track_type == "task" and "continuity_mode" in content:
                continuity_mode = content.get("continuity_mode")
                if continuity_mode not in {"shot", "context", "context_drift"}:
                    raise WorkflowError(
                        f"{where}.content.continuity_mode must be 'shot', 'context', or 'context_drift'"
                    )
            # Keep these ranges in sync with utils/h3_drift_taper.py.
            if track_type == "task" and "drift_taper_steps" in content:
                steps = content.get("drift_taper_steps")
                if isinstance(steps, bool) or not isinstance(steps, int) or not 0 <= steps <= 12:
                    raise WorkflowError(f"{where}.content.drift_taper_steps must be a whole number from 0 to 12")
            if track_type == "task" and "drift_taper_floor" in content:
                floor = content.get("drift_taper_floor")
                if (
                    isinstance(floor, bool)
                    or not isinstance(floor, (int, float))
                    or not math.isfinite(floor)
                    or not 0.0 <= floor <= 1.0
                ):
                    raise WorkflowError(f"{where}.content.drift_taper_floor must be a number from 0 to 1")
            if track_type == "task" and "drift_taper_low_only" in content:
                if not isinstance(content.get("drift_taper_low_only"), bool):
                    raise WorkflowError(f"{where}.content.drift_taper_low_only must be a boolean")
            if "shared_reference" in content:
                if not isinstance(content.get("shared_reference"), bool):
                    raise WorkflowError(
                        f"{where}.content.shared_reference must be a boolean"
                    )
                if track_type not in {"audio", "video"} or content.get("media_type") != track_type:
                    raise WorkflowError(
                        f"Only audio/video segments may contain shared_reference ({where})"
                    )
            if content.get("shared_reference") is True:
                shared_media_references.append((track_id, segment_id, str(track_type)))
            images = content.get("images", [])
            if track_type == "task" and (not isinstance(images, list) or len(images) > 9):
                raise WorkflowError(f"{where}.content.images must contain at most 9 items")
            if track_type == "task" and isinstance(images, list):
                for image_index, image in enumerate(images):
                    if not isinstance(image, dict):
                        raise WorkflowError(
                            f"{where}.content.images[{image_index}] must be an object"
                        )
                    if "shared_reference" in image and not isinstance(
                        image.get("shared_reference"), bool
                    ):
                        raise WorkflowError(
                            f"{where}.content.images[{image_index}].shared_reference must be a boolean"
                        )
            for field in ("videos", "audios"):
                if track_type != "task" or field not in content:
                    continue
                media_items = content[field]
                if not isinstance(media_items, list):
                    raise WorkflowError(f"{where}.content.{field} must be a list")
                for item_index, item in enumerate(media_items):
                    item_where = f"{where}.content.{field}[{item_index}]"
                    if not isinstance(item, dict):
                        raise WorkflowError(f"{item_where} must be an object")
                    for flag in ("shared_reference", "muted", "fit", "soundtrack", "has_audio"):
                        if flag in item and not isinstance(item[flag], bool):
                            raise WorkflowError(f"{item_where}.{flag} must be a boolean")
                    if item.get("source_type") == "track":
                        if not isinstance(item.get("track_id"), str) or not item["track_id"]:
                            raise WorkflowError(f"{item_where} is a timeline placeholder and needs a track_id")
                    elif not any(isinstance(item.get(key), str) and item[key]
                                 for key in ("file_path", "local_path", "url")):
                        raise WorkflowError(f"{item_where} needs a file_path, local_path or url")

    for locked_type in ("audio", "video"):
        locked_ids = [
            track_id
            for track_id, track_type in locked_media_tracks
            if track_type == locked_type
        ]
        if len(locked_ids) > 1:
            raise WorkflowError(
                f"Only one {locked_type} track may be audio locked; found {locked_ids}"
            )
    shared_media_tracks = [track_id for track_id, _, _ in shared_media_references]
    duplicate_shared_media_tracks = sorted({
        track_id
        for track_id in shared_media_tracks
        if shared_media_tracks.count(track_id) > 1
    })
    if duplicate_shared_media_tracks:
        raise WorkflowError(
            "Each audio/video track may contain only one shared reference; found multiple in "
            f"{duplicate_shared_media_tracks}"
        )

    if recalculate and max_end > 0:
        data["total_length"] = max_end
    total_length = data.get("total_length")
    if not isinstance(total_length, int) or isinstance(total_length, bool) or total_length < 0:
        raise WorkflowError("track_data.total_length must be a non-negative integer")


def patch_settings(node: dict[str, Any], changes: dict[str, Any], check: SettingsCheck) -> dict[str, Any]:
    """Apply setting changes checked against the node's definition; return the node's settings
    after them and each change's from and to."""
    nested = sorted(name for name, value in changes.items() if isinstance(value, (dict, list)))
    if nested:
        raise WorkflowError(f"{', '.join(nested)}: write each setting by its own name and a plain value, such as "
                            '"sampling_mode": "dual", "sampling_mode.upscale_by": 1.5 or '
                            '"resolution": "width x height (custom)", "resolution.width": 1024')
    named = node.get("widgets_values_named")
    if changes and (not isinstance(named, dict) or list(named.values()) != node.get("widgets_values")):
        raise WorkflowError(f"Node {node.get('id')} has widget values that do not match their names; "
                            "open the workflow in ComfyUI and save it again")
    result = check(node, changes)
    if changes:
        node["widgets_values_named"] = result["widgets_values_named"]
        node["widgets_values"] = result["widgets_values"]
    before = named if isinstance(named, dict) else {}
    return {"settings": result["settings"], "changes": {
        name: {"from": before.get(name), "to": value} for name, value in result["changes"] if before.get(name) != value}}


def set_track_data(editor: dict[str, Any], serialized: str) -> None:
    named = editor.get("widgets_values_named")
    if not isinstance(named, dict) or list(named.values()) != editor.get("widgets_values"):
        raise WorkflowError(f"Editor {editor.get('id')} has widget values that do not match their names; "
                            "open the workflow in ComfyUI and save it again")
    named["track_data"] = serialized
    editor["widgets_values"] = list(named.values())


def apply_plan(
    workflow: dict[str, Any], plan: dict[str, Any], check: SettingsCheck
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Patch the Project's and Editor's settings (by the names ComfyUI saves them under) and the
    Editor's track data; nothing else in the workflow changes."""
    editor_patch = plan.get("editor", {})
    project_patch = plan.get("project", {})
    if not isinstance(editor_patch, dict) or not isinstance(project_patch, dict):
        raise WorkflowError("plan.editor and plan.project must be objects")
    unknown_top = sorted(set(plan) - {"editor", "project"})
    if unknown_top:
        raise WorkflowError(f"Unsupported top-level plan fields: {unknown_top}")

    result = copy.deepcopy(workflow)
    project = choose_project(result, project_patch.get("node_id"))
    editor = choose_editor(result, project, editor_patch.get("node_id"))
    target_ids = {int(project["id"]), int(editor["id"])}
    before_widgets = {
        int(node["id"]): {
            "widgets_values": copy.deepcopy(node.get("widgets_values")),
            "widgets_values_named": copy.deepcopy(node.get("widgets_values_named")),
        }
        for node in result.get("nodes", []) if node.get("id") in target_ids
    }
    project_result = patch_settings(project, {key: value for key, value in project_patch.items()
                                              if key != "node_id"}, check)
    editor_changes = {key: value for key, value in editor_patch.items() if key not in EDITOR_PLAN_KEYS}
    editor_result = patch_settings(editor, editor_changes, check) if editor_changes else {"changes": {}}

    _track_index, current_track_data = track_data_widget(editor)
    if "track_data" in editor_patch:
        next_track_data = copy.deepcopy(editor_patch["track_data"])
        if not isinstance(next_track_data, dict):
            raise WorkflowError("editor.track_data must be an object")
        validate_track_data(
            next_track_data,
            recalculate=editor_patch.get("recalculate_total_length", True) is not False,
        )
        set_track_data(editor, json.dumps(next_track_data, ensure_ascii=False, separators=(",", ":")))
    else:
        validate_track_data(current_track_data, recalculate=False)

    original_without_targets = copy.deepcopy(workflow)
    result_without_targets = copy.deepcopy(result)
    for candidate in (original_without_targets, result_without_targets):
        for node in candidate.get("nodes", []):
            if node.get("id") in target_ids:
                node["widgets_values"] = "<target-widgets>"
                node["widgets_values_named"] = "<target-named-widgets>"
    if original_without_targets != result_without_targets:
        raise WorkflowError("Invariant failed: data outside target widgets changed")

    after_widgets = {
        int(node["id"]): {
            "widgets_values": copy.deepcopy(node.get("widgets_values")),
            "widgets_values_named": copy.deepcopy(node.get("widgets_values_named")),
        }
        for node in result.get("nodes", []) if node.get("id") in target_ids
    }
    changed_nodes = [node_id for node_id in sorted(target_ids) if before_widgets[node_id] != after_widgets[node_id]]
    report = {
        "editor_node_id": editor["id"],
        "project_node_id": project["id"],
        "changed_node_ids": changed_nodes,
        "node_count": len(result.get("nodes", [])),
        "link_count": len(result.get("links", [])),
        "settings_changes": {name: changes for name, changes in (
            ("project", project_result["changes"]), ("editor", editor_result["changes"])) if changes},
        "graph_preserved": True,
    }
    return result, report


def summarize_track(track: dict[str, Any]) -> dict[str, Any]:
    segments = track.get("segments", [])
    summary: dict[str, Any] = {
        "id": track.get("id"),
        "name": track.get("name"),
        "type": track.get("type"),
        "segments": len(segments) if isinstance(segments, list) else 0,
    }
    track_type = track.get("type")
    if track_type not in {"audio", "video"}:
        return summary

    summary["audio_locked"] = track.get("audio_locked") is True
    summary["media"] = [
        {
            "file_name": content.get("file_name"),
            "file_path": content.get("file_path"),
            "source_type": content.get("source_type"),
            "start_frame": segment.get("start_frame"),
            "end_frame": segment.get("end_frame"),
            "shared_reference": content.get("shared_reference") is True,
        }
        for segment in segments
        if isinstance(segment, dict)
        and isinstance(segment.get("content"), dict)
        and (content := segment["content"]).get("media_type") == track_type
    ]
    return summary


def inspect_workflow(workflow: dict[str, Any], check: SettingsCheck) -> dict[str, Any]:
    """Each Project and Editor with its settings by name, and each Editor's tracks."""
    project_items = [
        {"id": project.get("id"), "editor_id": linked_editor_id(workflow, project),
         "settings": check(project, {})["settings"]}
        for project in nodes_by_type(workflow, PROJECT_TYPE)
    ]
    editor_items: list[dict[str, Any]] = []
    for editor in nodes_by_type(workflow, EDITOR_TYPE):
        _index, data = track_data_widget(editor)
        editor_items.append({
            "id": editor.get("id"),
            "settings": check(editor, {})["settings"],
            "frame_rate": data.get("frame_rate"),
            "total_length": data.get("total_length"),
            "tracks": [
                summarize_track(track)
                for track in data.get("tracks", []) if isinstance(track, dict)
            ],
        })
    return {
        "node_count": len(workflow.get("nodes", [])),
        "link_count": len(workflow.get("links", [])),
        "editors": editor_items,
        "projects": project_items,
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="summarize MultiTrack targets")
    inspect_parser.add_argument("workflow", type=Path)
    apply_parser = subparsers.add_parser("apply", help="validate and apply a patch plan")
    apply_parser.add_argument("workflow", type=Path)
    apply_parser.add_argument("--plan", required=True, type=Path)
    apply_parser.add_argument("--output", type=Path)
    apply_parser.add_argument("--write", action="store_true", help="write the output file")
    for sub in (inspect_parser, apply_parser):
        sub.add_argument("--url", help="ComfyUI, which checks the settings (default: config.yaml COMFYUI_URL, "
                                       "then http://127.0.0.1:8188)")
        sub.add_argument("--config", type=Path, help="Path to the Easy Media config.yaml")
    return parser


def comfyui_check(args: argparse.Namespace) -> SettingsCheck:
    """Settings checked by the running ComfyUI, against the node definitions the workflow will run with."""
    # submit_workflow imports this module, so it is imported once a command runs.
    from submit_workflow import node_settings, resolve_comfyui_url

    base_url = resolve_comfyui_url(args.url, args.config)
    return lambda node, changes: node_settings(base_url, node, changes)


def main() -> int:
    args = build_parser().parse_args()
    try:
        workflow = load_object(args.workflow, "workflow")
        check = comfyui_check(args)
        if args.command == "inspect":
            print(json.dumps(inspect_workflow(workflow, check), ensure_ascii=False, indent=2))
            return 0
        plan = load_object(args.plan, "plan")
        result, report = apply_plan(workflow, plan, check)
        if args.write:
            if args.output is None:
                raise WorkflowError("--output is required with --write")
            if args.output.resolve() == args.workflow.resolve():
                raise WorkflowError("Refusing to overwrite the source workflow; choose a new --output path")
            write_json_atomic(args.output, result)
            report["output"] = str(args.output)
        else:
            report["dry_run"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:  # WorkflowError, or a setting ComfyUI's check refused
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
