#!/usr/bin/env python3
"""Save one contact sheet per generated task video, for an agent that can see images.

Off unless config.yaml sets AGENT_FRAME_REVIEW: true or the user asked for a review
(--user-asked). Each sheet is one image of a few evenly spaced, time-labelled frames.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

# ComfyUI's embedded Python does not put a script's own folder on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from submit_workflow import read_config_value, request_json, resolve_comfyui_url  # noqa: E402

OFF_MESSAGE = (
    "Frame review is off. Turn it on with AGENT_FRAME_REVIEW: true in config.yaml, or pass --user-asked "
    "when the user asked you to review the results. Skip it if your model cannot see images; report the "
    "output files instead."
)


def review_enabled(config_path: Path | None) -> bool:
    return read_config_value("AGENT_FRAME_REVIEW", config_path) is True


def default_folder(base_url: str, project: str) -> Path:
    """The project's agent records folder when this machine can reach it, else ./review/PROJECT."""
    try:
        records = Path(request_json(base_url, f"/easy-media/agent/records?{urlencode({'project': project})}")["directory"])
    except (RuntimeError, KeyError):
        return Path("review") / project
    # The records folder sits in ComfyUI's output/easy_media; another machine cannot write there.
    return records / "review" if records.parents[1].is_dir() else Path("review") / project


def download(base_url: str, item: dict, target: Path) -> None:
    query = urlencode({"filename": item["filename"], "subfolder": item["subfolder"], "type": item["type"]})
    try:
        with urlopen(f"{base_url}/view?{query}", timeout=60) as response:
            target.write_bytes(response.read())
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Downloading the contact sheet {item['filename']} failed: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="The project_name the run used")
    parser.add_argument("--tasks", help="Comma-separated task ids; default every task with a video")
    parser.add_argument("--frames", type=int, default=4, help="Frames per sheet, 1-9")
    parser.add_argument("--width", type=int, default=1024, help="Sheet width in pixels, 256-2048")
    parser.add_argument("--out", type=Path, help="Folder for the sheets; default the project's agent records "
                                                 "(output/easy_media/agent/PROJECT/review), or ./review/PROJECT on another machine")
    parser.add_argument("--user-asked", action="store_true", help="The user asked for this review")
    parser.add_argument("--url", help="Override config.yaml COMFYUI_URL (fallback: http://127.0.0.1:8188)")
    parser.add_argument("--config", type=Path, help="Path to the Easy Media config.yaml")
    args = parser.parse_args()
    try:
        if not (args.user_asked or review_enabled(args.config)):
            print(OFF_MESSAGE, file=sys.stderr)
            return 2
        base_url = resolve_comfyui_url(args.url, args.config)
        project = request_json(base_url, f"/easy-media/project?{urlencode({'project_name': args.project})}")
        wanted = {task.strip() for task in args.tasks.split(",")} if args.tasks else None
        clips = [clip for clip in project.get("clips", []) if wanted is None or clip["id"] in wanted]
        if wanted is not None and len(clips) < len(wanted):
            missing = sorted(wanted - {clip["id"] for clip in clips})
            raise RuntimeError(f"No generated video for task(s) {', '.join(missing)} in project {args.project}")
        if not clips:
            raise RuntimeError(f"Project {args.project} has no generated videos yet")
        results = request_json(base_url, "/easy-media/review/contact-sheet", {
            "items": [{"file_path": clip["file_path"]} for clip in clips], "frames": args.frames, "width": args.width,
        }, timeout=300)["results"]
        out = args.out or default_folder(base_url, args.project)
        out.mkdir(parents=True, exist_ok=True)
        sheets = []
        for clip, result in zip(clips, results):
            entry = {"task": clip["id"], "number": clip["index"] + 1, "video": clip["file_path"]}
            if "error" in result:
                entry["error"] = result["error"]
            else:
                target = out / f"{clip['index'] + 1:02d}-{clip['id']}.jpg"
                download(base_url, result, target)
                entry["image"] = str(target.resolve())
            sheets.append(entry)
        print(json.dumps(sheets, ensure_ascii=False, indent=2))
        return 0 if all("image" in sheet for sheet in sheets) else 2
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
