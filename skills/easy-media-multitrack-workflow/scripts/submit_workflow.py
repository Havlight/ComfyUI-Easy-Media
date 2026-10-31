#!/usr/bin/env python3
"""Run a workflow tab open in ComfyUI (or open a workflow file in a new tab) through the page's
native Run button, wait for the generation, and keep the run in the project's agent records."""

import argparse
import datetime
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

# ComfyUI's embedded Python does not put a script's own folder on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patch_workflow import PROJECT_WIDGETS, WorkflowError, choose_editor, choose_project, inspect_workflow  # noqa: E402


def default_config_path() -> Path:
    # A bundled/symlinked skill can locate its node pack independently of cwd.
    bundled_root = Path(__file__).resolve().parents[3]
    if (bundled_root / "routes.py").is_file():
        return bundled_root / "config.yaml"
    # A copied skill uses the node pack containing the current working directory.
    for root in (Path.cwd(), *Path.cwd().parents):
        if (root / "routes.py").is_file() and (root / "skills/easy-media-multitrack-workflow").is_dir():
            return root / "config.yaml"
    return Path.cwd() / "config.yaml"


def read_config_value(name: str, config_path: Path | None = None) -> object:
    """One field of the node pack's config.yaml, or None when the file or field is absent."""
    path = config_path if config_path is not None else default_config_path()
    if not path.exists():
        return None
    # Keep the copied skill self-contained; backend utils also import
    # ComfyUI/model dependencies. YAML is needed only for config.
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("Reading config.yaml requires PyYAML; use the ComfyUI Python environment or install PyYAML") from error
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        # Parser errors may contain unrelated API keys from the file.
        raise ValueError(f"Invalid YAML in {path}; fix the config before submitting") from error
    if config is not None and not isinstance(config, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return config.get(name) if config else None


def resolve_comfyui_url(explicit_url: str | None, config_path: Path | None = None) -> str:
    value = explicit_url
    if value is None:
        value = read_config_value("COMFYUI_URL", config_path)
        if value is not None and not isinstance(value, str):
            raise ValueError("COMFYUI_URL must be a string")
        if value is None or not value.strip():
            value = "http://127.0.0.1:8188"
    base_url = value.strip().rstrip("/")
    if "://" not in base_url:
        base_url = "http://" + base_url
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("ComfyUI URL must be an http(s) server address without a query or fragment")
    _ = parsed.port  # Reject malformed/out-of-range ports before a network request.
    return base_url


def request_json(base_url: str, path: str, payload: dict | None = None, timeout: float = 20) -> dict:
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
    request = Request(base_url + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"ComfyUI request failed: {error}. Do not resubmit with a new request_id; query the existing request first.") from error


def page_job(base_url: str, route: str, payload: dict, timeout: float) -> dict:
    """Hand a job to the ComfyUI page and wait until the page reports back (not for generation)."""
    payload = {"request_id": f"{route}-{uuid.uuid4()}", **payload}
    result = request_json(base_url, f"/easy-media/workflow/{route}", payload)
    deadline = time.monotonic() + timeout
    while result["status"] in {"pending", "loading"} and time.monotonic() < deadline:
        time.sleep(0.5)
        result = request_json(base_url, f"/easy-media/workflow/submissions/{quote(payload['request_id'], safe='')}")
    return result


def read_tab(base_url: str, tab: str | None, client_id: str | None = None, timeout: float = 150) -> dict:
    """The page's open tabs, and the content of `tab` (unsaved edits included) when one is named."""
    payload = {**({"tab": tab} if tab else {}), **({"client_id": client_id} if client_id else {})}
    result = page_job(base_url, "tabs", payload, timeout)
    if result["status"] != "read":
        raise RuntimeError(f"The ComfyUI page did not read the tab: {result.get('error') or result['status']}")
    return result


def open_workflow(base_url: str, workflow: dict, name: str, client_id: str | None = None, timeout: float = 150) -> str:
    """Open a workflow file's content in a new tab without running it; return the tab key."""
    payload = {"workflow": workflow, "auto_queue": False, "name": name, **({"client_id": client_id} if client_id else {})}
    result = page_job(base_url, "submit", payload, timeout)
    if result["status"] != "loaded" or not result.get("tab"):
        raise RuntimeError(f"The ComfyUI page did not open the workflow: {result.get('error') or result['status']}")
    return result["tab"]


def project_fields(project: dict) -> dict:
    widgets = project.get("widgets_values") or []
    return {name: widgets[index] if index < len(widgets) else None
            for name, index in PROJECT_WIDGETS.items() if name in ("project_name", "segment_start_number", "segment_count")}


def get_record(base_url: str, project: str, path: str) -> str | None:
    """A file of the project's agent records, or None when it does not exist yet."""
    query = urlencode({"project": project, "path": path})
    return request_json(base_url, f"/easy-media/agent/records?{query}")["text"]


def put_record(base_url: str, project: str, path: str, text: str) -> str:
    """Write a file of the project's agent records; return where it is on the ComfyUI machine."""
    return request_json(base_url, "/easy-media/agent/records", {"project": project, "path": path, "text": text})["path"]


def history_outputs(entry: dict) -> list[dict]:
    """Every file a finished prompt reported, as /view can serve it."""
    files = []
    for node_id, output in (entry.get("outputs") or {}).items():
        for kind, items in (output or {}).items():
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and isinstance(item.get("filename"), str):
                    files.append({"node": node_id, "kind": kind, "filename": item["filename"],
                                  "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
    return files


def generation_state(base_url: str, prompt_id: str) -> dict:
    """Where a queued prompt is now: finished (success or error), running, pending or unknown."""
    entry = request_json(base_url, f"/history/{quote(prompt_id, safe='')}").get(prompt_id)
    if entry:
        status = entry.get("status") or {}
        if status.get("status_str") == "success" and status.get("completed"):
            return {"state": "success", "outputs": history_outputs(entry)}
        errors = [message[1] for message in status.get("messages", [])
                  if isinstance(message, list) and len(message) == 2 and message[0] == "execution_error"]
        detail = errors[0] if errors else {}
        return {"state": "error", "node_type": detail.get("node_type"), "message": detail.get("exception_message")}
    queue = request_json(base_url, "/queue")
    for state, key in (("running", "queue_running"), ("pending", "queue_pending")):
        ids = [item[1] for item in queue.get(key, []) if isinstance(item, list) and len(item) > 1]
        if prompt_id in ids:
            return {"state": state, **({"ahead": ids.index(prompt_id)} if state == "pending" else {})}
    return {"state": "unknown"}


def wait_for_generation(base_url: str, prompt_id: str, seconds: float | None, interval: float = 5) -> dict:
    """Wait at most `seconds` (None: until it finishes); calling again later continues the same wait."""
    deadline = None if seconds is None else time.monotonic() + seconds
    while True:
        state = generation_state(base_url, prompt_id)
        if state["state"] in {"success", "error"} or (deadline is not None and time.monotonic() >= deadline):
            return state
        time.sleep(interval if deadline is None else min(interval, max(0.0, deadline - time.monotonic())))


def record_run(base_url: str, content: dict, result: dict) -> dict | None:
    """Keep a queued run of a tab: the plan it ran and an entry in history.json. How it ended is
    in ComfyUI's history (prompt_id) and the Project (each task's saved versions)."""
    workflow = content["workflow"]
    project = choose_project(workflow)
    editor = choose_editor(workflow, project)
    fields = project_fields(project)
    name = fields["project_name"]
    if not isinstance(name, str) or not name.strip():
        return None
    runs = json.loads(get_record(base_url, name, "history.json") or "[]")
    number = max((entry.get("run", 0) for entry in runs), default=0) + 1
    entry = {"run": number, "queued_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
             "request_id": result["request_id"], "prompt_id": result["prompt_id"], "tab": content["key"],
             "start": fields["segment_start_number"], "count": fields["segment_count"],
             "resolution": next(item["resolution"] for item in inspect_workflow(workflow)["editors"] if item["id"] == editor.get("id"))}
    plan = content["plans"].get(str(editor.get("id")))
    if plan is not None:
        entry["plan"] = f"plans/run-{number:03d}.json"
        put_record(base_url, name, entry["plan"], json.dumps(plan, ensure_ascii=False, indent=2))
    runs.append(entry)
    put_record(base_url, name, "history.json", json.dumps(runs, ensure_ascii=False, indent=2))
    return {"project_name": name, "run": number}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", nargs="?", type=Path, help="A workflow file to open in a new tab (instead of --tab)")
    parser.add_argument("--tab", help="Run this open tab: its key as listed by plan_workflow.py tabs, or a name only one tab has")
    parser.add_argument("--url", help="Override config.yaml COMFYUI_URL (fallback: http://127.0.0.1:8188)")
    parser.add_argument("--config", type=Path, help="Path to the Easy Media config.yaml")
    parser.add_argument("--client-id")
    parser.add_argument("--name")
    parser.add_argument("--request-id", help="Reuse the same id and payload after an uncertain response")
    parser.add_argument("--no-queue", action="store_true", help="Open the workflow file without running it")
    parser.add_argument("--clients", action="store_true", help="List available browser pages")
    parser.add_argument("--status", metavar="REQUEST_ID", help="Read an existing submission without resubmitting")
    parser.add_argument("--timeout", type=float, default=150, help="Seconds to wait for browser acknowledgement, not generation")
    wait = parser.add_mutually_exclusive_group()
    wait.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                      help="After queueing (or with --status), wait up to this long for the generation to finish; "
                           "exit 3 while it still runs, so the same command can be repeated")
    wait.add_argument("--until-done", action="store_true",
                      help="Wait however long the generation takes; for running in the background")
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error("--timeout must be between 0 and 3600 seconds")
    if not 0 <= args.wait <= 3600:
        parser.error("--wait must be between 0 and 3600 seconds")
    if not args.clients and not args.status and (args.workflow is None) == (args.tab is None):
        parser.error("give a workflow file or --tab (or use --clients or --status)")
    try:
        base_url = resolve_comfyui_url(args.url, args.config)
        if args.clients:
            result = request_json(base_url, "/easy-media/workflow/clients")
        elif args.status:
            result = request_json(base_url, f"/easy-media/workflow/submissions/{quote(args.status, safe='')}")
        else:
            request_id = args.request_id or str(uuid.uuid4())
            client = {"client_id": args.client_id} if args.client_id else {}
            content = None
            if args.tab:
                # Read first: the run is recorded with the plan the tab holds.
                content = read_tab(base_url, args.tab, args.client_id, args.timeout)["tab"]
                route, payload = "apply", dict(tab=args.tab, queue=True, **client)
            else:
                workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
                if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list) or not isinstance(workflow.get("links"), list):
                    raise ValueError("Expected UI workflow JSON with nodes and links, not API-format prompt")
                route, payload = "submit", dict(workflow=workflow, auto_queue=not args.no_queue,
                                                name=args.name or args.workflow.stem, **client)
            # Print before POST so even an interrupted call leaves a lookup key.
            print(f"request_id={request_id}", file=sys.stderr, flush=True)
            result = request_json(base_url, f"/easy-media/workflow/{route}", {**payload, "request_id": request_id})
            deadline = time.monotonic() + args.timeout
            while result["status"] in {"pending", "loading"} and time.monotonic() < deadline:
                time.sleep(1)
                result = request_json(base_url, f"/easy-media/workflow/submissions/{quote(request_id, safe='')}")
            if content is not None and result.get("status") == "queued":
                try:
                    recorded = record_run(base_url, content, result)
                except (RuntimeError, ValueError, WorkflowError) as error:
                    recorded = {"error": f"The run was queued but not recorded: {error}"}
                if recorded:
                    result["record"] = recorded
        if (args.wait or args.until_done) and result.get("status") == "queued" and result.get("prompt_id"):
            result["generation"] = wait_for_generation(base_url, result["prompt_id"], None if args.until_done else args.wait)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if "generation" in result:
            return {"success": 0, "error": 2}.get(result["generation"]["state"], 3)
        return 0 if "clients" in result or result.get("status") in {"loaded", "queued"} else 2
    except (OSError, ValueError, RuntimeError, WorkflowError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
