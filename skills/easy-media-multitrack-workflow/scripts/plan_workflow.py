#!/usr/bin/env python3
"""Turn an H3 plan file into a runnable workflow, or read the length of media files.

convert: TEMPLATE.json + PLAN.json + project name -> a workflow file. Files the plan names are
copied into ComfyUI (uploaded when the server cannot read them), the plan is converted by an
open ComfyUI page with the Editor's own importer, and the Project starts at the first task
without a saved result. Nothing is queued; run the output with submit_workflow.py.

probe: the length of video and audio files, to plan tasks around a song or clip.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# ComfyUI's embedded Python does not put a script's own folder on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patch_workflow import (  # noqa: E402
    PROJECT_WIDGETS,
    WorkflowError,
    apply_plan,
    choose_editor,
    choose_project,
    load_object,
    track_data_widget,
    write_json_atomic,
)
from submit_workflow import request_json, resolve_comfyui_url  # noqa: E402

IMPORT_SUBFOLDER = "easy_media/imports"
MEDIA_FIELDS = (("images", "image"), ("videos", "video"), ("audios", "audio"))
EXTENSIONS = {
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
    "audio": {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"},
}
# Copying or downloading large files happens inside one request.
RESOLVE_TIMEOUT = 900
CHUNK_SIZE = 1024 * 1024


class PlanError(ValueError):
    """A plan that cannot become a workflow; the message says what to fix."""


def media_type_of(path: str) -> str:
    extension = Path(path.split("?")[0]).suffix.lower()
    for media_type, extensions in EXTENSIONS.items():
        if extension in extensions:
            return media_type
    raise PlanError(f"{path}: unknown media type; use a video, audio or image file")


# ---------------------------------------------------------------------------
# Files the plan names
# ---------------------------------------------------------------------------

def file_references(plan: dict[str, Any]) -> list[tuple[Any, Any, str, str]]:
    """(holder, key, media type, where) for every `file` reference in the plan."""
    refs: list[tuple[Any, Any, str, str]] = []
    for number, task in enumerate(plan.get("tasks") or [], 1):
        if not isinstance(task, dict):
            continue
        for field, media_type in MEDIA_FIELDS:
            items = task.get(field)
            for index, item in enumerate(items if isinstance(items, list) else []):
                if isinstance(item, str) and item and item != "previous_frame":
                    refs.append((items, index, media_type, f"task {number} {field}"))
                elif isinstance(item, dict) and isinstance(item.get("file"), str):
                    refs.append((item, "file", media_type, f"task {number} {field}"))
    for number, track in enumerate(plan.get("tracks") or [], 1):
        if not isinstance(track, dict) or track.get("type") not in ("video", "audio"):
            continue
        for clip_number, clip in enumerate(track.get("clips") or [], 1):
            if isinstance(clip, dict) and isinstance(clip.get("file"), str):
                refs.append((clip, "file", track["type"], f"track {number} clip {clip_number}"))
    return refs


def _value(holder: Any, key: Any) -> str:
    return holder[key] if isinstance(holder, list) else holder["file"]


def _set_input(holder: Any, key: Any, path: str) -> None:
    if isinstance(holder, list):
        holder[key] = {"input": path}
    else:
        del holder["file"]
        holder["input"] = path


def _upload(base_url: str, source: Path, subfolder: str) -> str:
    """Upload one file to ComfyUI's input folder; return its input path."""
    boundary = uuid.uuid4().hex
    filename = source.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    fields = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="subfolder"\r\n\r\n{subfolder}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\nfalse\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    closing = f"\r\n--{boundary}--\r\n".encode()

    def body():
        yield fields
        with source.open("rb") as handle:
            yield from iter(lambda: handle.read(CHUNK_SIZE), b"")
        yield closing

    request = Request(base_url + "/upload/image", data=body(), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(fields) + source.stat().st_size + len(closing)),
    })
    try:
        with urlopen(request, timeout=RESOLVE_TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as error:
        raise PlanError(f"Uploading {source} failed: HTTP {error.code}: {error.read().decode('utf-8', 'replace')}") from error
    except (URLError, TimeoutError) as error:
        raise PlanError(f"Uploading {source} failed: {error}") from error
    return f"{result.get('subfolder') or subfolder}/{result['name']}"


def _content_hash(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _server_cannot_read(error: str) -> bool:
    return "was not found" in error or "is not an absolute path" in error


def place_files(base_url: str, items: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Copy each {media_type, file} into ComfyUI; upload what the server cannot read.

    Returns one result per item ({input path, uploaded} or {error}) and the uploaded files.
    """
    if not items:
        return [], []
    answers = request_json(base_url, "/easy-media/plan/resolve-media",
                           {"items": [{"media_type": item["media_type"], "file": item["file"]} for item in items]},
                           timeout=RESOLVE_TIMEOUT)["results"]
    results: list[dict[str, Any]] = []
    uploaded: list[str] = []
    for item, answer in zip(items, answers):
        if "error" not in answer:
            results.append({"input": answer["file_path"]})
            continue
        local = Path(item["file"]).expanduser()
        # The agent may run in WSL, a container or on another machine than ComfyUI.
        if not item["file"].lower().startswith(("http://", "https://")) and _server_cannot_read(answer["error"]) and local.is_file():
            results.append({"input": _upload(base_url, local, f"{IMPORT_SUBFOLDER}/{_content_hash(local)}")})
            uploaded.append(str(local))
        else:
            results.append({"error": f"{item['file']}: {answer['error']}"})
    return results, uploaded


def resolve_plan_files(base_url: str, plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A copy of the plan whose `file` references point at files inside ComfyUI."""
    resolved = copy.deepcopy(plan)
    refs = file_references(resolved)
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for holder, key, media_type, _where in refs:
        unique.setdefault((media_type, _value(holder, key)), {"media_type": media_type, "file": _value(holder, key)})
    results, uploaded = place_files(base_url, list(unique.values()))
    by_key = dict(zip(unique, results))
    errors = []
    for holder, key, media_type, where in refs:
        result = by_key[(media_type, _value(holder, key))]
        if "error" in result:
            errors.append(f"{where}: {result['error']}")
        else:
            _set_input(holder, key, result["input"])
    if errors:
        raise PlanError("\n".join(errors))
    return resolved, uploaded


# ---------------------------------------------------------------------------
# Conversion and saved results
# ---------------------------------------------------------------------------

def convert_on_page(base_url: str, track_data: dict, editor_format: str, plan: dict,
                    client_id: str | None, timeout: float) -> dict:
    request_id = f"plan-{uuid.uuid4()}"
    payload = {"request_id": request_id, "track_data": track_data, "format": editor_format, "plan": plan}
    if client_id:
        payload["client_id"] = client_id
    result = request_json(base_url, "/easy-media/workflow/convert", payload)
    deadline = time.monotonic() + timeout
    while result["status"] in {"pending", "loading"} and time.monotonic() < deadline:
        time.sleep(0.5)
        result = request_json(base_url, f"/easy-media/workflow/submissions/{quote(request_id, safe='')}")
    if result["status"] == "converted":
        return result["conversion"]
    raise PlanError(f"The ComfyUI page did not convert the plan: {result.get('error') or result['status']}")


def _link_origin(workflow: dict, node: dict, input_name: str) -> dict | None | bool:
    """The node feeding an input (through reroutes); None when unlinked, False when unreadable."""
    nodes = {item.get("id"): item for item in workflow.get("nodes", []) if isinstance(item, dict)}
    links = {link[0]: link for link in workflow.get("links", []) if isinstance(link, list) and len(link) >= 5}
    current, name, seen = node, input_name, set()
    while True:
        entry = next((item for item in current.get("inputs", []) if isinstance(item, dict) and item.get("name") == name), None)
        link_id = entry.get("link") if entry else None
        if link_id is None:
            return None if current is node else False
        origin = nodes.get(links.get(link_id, [None, None])[1])
        if origin is None or id(origin) in seen:
            return False
        if origin.get("type") != "Reroute":
            return origin
        seen.add(id(origin))
        current, name = origin, (origin.get("inputs") or [{}])[0].get("name")


def project_snapshot(workflow: dict, project: dict) -> dict:
    """What the Project's saved-result check needs: its LoRA rules and sampling recipe."""
    widgets = project.get("widgets_values") or []
    known = all(_link_origin(workflow, project, name) is None
                for name in ("project_name", "sampling_mode", "1st_pass_only", "segment_start_number", "segment_count"))
    rules: Any = {"version": 1, "rules": []}
    lora = _link_origin(workflow, project, "segment_loras")
    if lora is False or (isinstance(lora, dict) and (lora.get("type") != "easy h3SegmentLoras" or _link_origin(workflow, lora, "rules") is not None)):
        known = False
    elif isinstance(lora, dict):
        raw = (lora.get("widgets_values") or [None])[0]
        try:
            rules = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            known = False
        if not isinstance(rules, dict) or rules.get("version") != 1 or not isinstance(rules.get("rules"), list):
            known, rules = False, {"version": 1, "rules": []}
    mode = widgets[PROJECT_WIDGETS["sampling_mode"]] if len(widgets) > PROJECT_WIDGETS["sampling_mode"] else "single"
    if mode not in ("single", "dual", "selflift", "passthrough"):
        known, mode = False, "single"
    first_pass_only = bool(widgets[PROJECT_WIDGETS["1st_pass_only"]]) if len(widgets) > PROJECT_WIDGETS["1st_pass_only"] else False
    return {"known": known, "segment_loras": rules,
            "recipe": {"sampling_mode": mode, "first_pass_only": first_pass_only and mode != "selflift"}}


def saved_tasks(base_url: str, project_name: str, track_data: dict, snapshot: dict) -> tuple[list[dict], set[str]]:
    """Each task's saved-result status, and the statuses that can be reused as they are."""
    result = request_json(base_url, "/easy-media/project/timeline-status",
                          {"project_name": project_name, "tracks_info": track_data, "project_snapshot": snapshot})
    # Without the LoRA settings a saved result reports "pending"; it still matches the task.
    return result["tasks"], {"saved"} if snapshot["known"] else {"saved", "pending"}


def first_task_to_run(statuses: list[dict], reusable: set[str]) -> int | None:
    """1-based number of the first task without a usable saved result; later tasks run too."""
    pending = [item["index"] for item in statuses if item["status"] not in reusable]
    return min(pending) + 1 if pending else None


def convert(args: argparse.Namespace) -> int:
    base_url = resolve_comfyui_url(args.url, args.config)
    template = load_object(args.template, "template workflow")
    plan = load_object(args.plan, "plan")
    project = choose_project(template)
    editor = choose_editor(template, project)
    track_index, track_data = track_data_widget(editor)
    editor_format = editor["widgets_values"][track_index - 1]
    resolved, uploaded = resolve_plan_files(base_url, plan)
    conversion = convert_on_page(base_url, track_data, editor_format, resolved, args.client_id, args.timeout)
    report: dict[str, Any] = {"errors": conversion["errors"], "warnings": conversion["warnings"]}
    if uploaded:
        report["uploaded"] = uploaded
    if conversion["errors"] or conversion["track_data"] is None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    converted = conversion["track_data"]
    statuses, reusable = saved_tasks(base_url, args.project_name, converted, project_snapshot(template, project))
    first = first_task_to_run(statuses, reusable) if args.start == "auto" else int(args.start)
    saved = {item["segment_id"].split(":marker:")[0] for item in statuses if item["status"] in reusable}
    result, _ = apply_plan(template, {
        "editor": {"track_data": converted},
        "project": {"project_name": args.project_name, "segment_start_number": first or 1, "segment_count": args.count},
    })
    output = args.output or args.plan.with_suffix(".workflow.json")
    write_json_atomic(output, result)
    tasks = [row for row in conversion["tasks"] if row["change"] != "removed"]
    report = {
        "output": str(output),
        "project_name": args.project_name,
        # Nothing to run when every task already has its saved result.
        "run": {"start": first, "count": args.count} if first else {"nothing_to_run": True},
        **report,
        "tasks": [{"number": row["index"], "id": row["id"], "seconds": row["seconds"], "mode": row["mode"],
                   "continuity": row["continuity"], "references": f"img {row['images']} · vid {row['videos']} · aud {row['audios']}",
                   "saved": row["id"] in saved} for row in tasks],
        "tracks": [{"number": row["index"], "type": row["type"], "audio_locked": row["audioLocked"], "muted": row["muted"],
                    "clips": row["clips"]} for row in conversion["tracks"] if row["change"] != "removed"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def probe(args: argparse.Namespace) -> int:
    base_url = resolve_comfyui_url(args.url, args.config)
    items = [{"media_type": media_type_of(path), "file": path} for path in args.files]
    answers = []
    for item in items:
        # Read the file where it is when the server can; upload it only when it cannot.
        answer = request_json(base_url, "/easy-media/plan/resolve-media",
                              {"items": [{"media_type": item["media_type"], "local": item["file"], "probe": True}]})["results"][0]
        if "error" in answer and _server_cannot_read(answer["error"]):
            placed, _ = place_files(base_url, [item])
            if "error" in placed[0]:
                raise PlanError(placed[0]["error"])
            answer = request_json(base_url, "/easy-media/plan/resolve-media", {"items": [
                {"media_type": item["media_type"], "input": placed[0]["input"], "probe": True}]})["results"][0]
        if "error" in answer:
            raise PlanError(f"{item['file']}: {answer['error']}")
        answers.append({"file": item["file"], "media_type": item["media_type"], "seconds": answer.get("duration"),
                        **({"has_audio": answer.get("has_audio")} if item["media_type"] == "video" else {})})
    print(json.dumps(answers, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    convert_parser = commands.add_parser("convert", help="Write a runnable workflow from a template and a plan")
    convert_parser.add_argument("template", type=Path, help="UI workflow with the resolution and Project settings to use")
    convert_parser.add_argument("plan", type=Path)
    convert_parser.add_argument("--project-name", required=True)
    convert_parser.add_argument("-o", "--output", type=Path, help="Default: PLAN.workflow.json")
    convert_parser.add_argument("--start", default="auto",
                                help='"auto" (first task without a saved result) or a 1-based task number')
    convert_parser.add_argument("--count", type=int, default=-1, help="Tasks to run from the start; -1 runs the rest")
    convert_parser.add_argument("--client-id", help="The ComfyUI page to convert on, when several are open")
    convert_parser.add_argument("--timeout", type=float, default=150, help="Seconds to wait for the page")
    probe_parser = commands.add_parser("probe", help="Print the length of video and audio files")
    probe_parser.add_argument("files", nargs="+")
    for sub in (convert_parser, probe_parser):
        sub.add_argument("--url", help="Override config.yaml COMFYUI_URL (fallback: http://127.0.0.1:8188)")
        sub.add_argument("--config", type=Path, help="Path to the Easy Media config.yaml")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "convert" and args.start != "auto" and not (args.start.isdigit() and int(args.start) >= 1):
        print('--start must be "auto" or a task number from 1', file=sys.stderr)
        return 1
    try:
        return convert(args) if args.command == "convert" else probe(args)
    except (PlanError, WorkflowError) as error:
        # Problems to fix in the plan or template, reported like the page's own errors.
        print(json.dumps({"errors": str(error).splitlines()}, ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
