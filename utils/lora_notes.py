"""The LoRA notes a skill agent goes by: what each LoRA is for, its trigger words, strength and
stage, in output/easy_media/loras.md, shared by every project.

The person writes them, or tells the agent, which writes their words. Read with the installed
LoRA files and what LoRA Manager keeps beside each one, through the server, so an agent in WSL,
a container or on another machine reads the same notes.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from .agent_files import json_object, write_text_atomic

NOTES_FILE = "loras.md"
MAX_NOTES_BYTES = 1024 * 1024
# A note's heading is the LoRA's file name as rules write it: "## mmh3/style.safetensors".
HEADING = re.compile(r"##[ \t]+(\S.*?)[ \t]*")


def documented_loras(text: str) -> list[str]:
    """The LoRA names the notes' headings give, outside fenced code, with forward slashes."""
    names, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (match := HEADING.fullmatch(line)):
            names.append(match.group(1).replace("\\", "/"))
    return list(dict.fromkeys(names))


def _manager_fields(lora_path: str | None) -> dict:
    """What LoRA Manager keeps in <name>.metadata.json beside the file, when it does."""
    if not lora_path:
        return {}
    metadata = Path(lora_path).with_suffix(".metadata.json")
    if not metadata.is_file():
        return {}
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logging.warning("Easy Media: cannot read LoRA Manager metadata %s: %s", metadata, error)
        return {}
    if not isinstance(data, dict):
        return {}
    civitai = data.get("civitai") if isinstance(data.get("civitai"), dict) else {}
    tips = data.get("usage_tips")
    if isinstance(tips, str):
        try:
            tips = json.loads(tips)
        except ValueError:
            pass  # free text, kept as written
    fields = {
        "base_model": data.get("base_model") if data.get("base_model") not in (None, "", "Unknown") else None,
        "trigger_words": data.get("trainedWords") or civitai.get("trainedWords") or None,
        "usage_tips": tips or None,
        "notes": data.get("notes") or None,
        "tags": data.get("tags") or None,
    }
    return {key: value for key, value in fields.items() if value}


class LoraNotes:
    def __init__(self, root: Callable[[], Path], installed: Callable[[], list[str]],
                 full_path: Callable[[str], str | None]) -> None:
        self.root = root
        self.installed = installed
        self.full_path = full_path

    def path(self) -> Path:
        return self.root() / NOTES_FILE

    def read(self, find: str | None = None) -> dict:
        """The notes, the LoRAs they name (installed or not, with LoRA Manager's fields), and the
        installed LoRAs whose name has every word of `find` ("" lists them all)."""
        path = self.path()
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        installed = sorted(name.replace("\\", "/") for name in self.installed())
        known = set(installed)

        def entry(name: str) -> dict:
            manager = _manager_fields(self.full_path(name)) if name in known else {}
            return {"name": name, "installed": name in known, **({"lora_manager": manager} if manager else {})}

        result = {"path": str(path), "text": text, "documented": [entry(name) for name in documented_loras(text or "")],
                  "installed_count": len(installed)}
        if find is not None:
            words = find.lower().split()
            # Names only: reading every LoRA's metadata would be slow for a large library.
            result["found"] = [{"name": name, "installed": True} for name in installed
                               if all(word in name.lower() for word in words)]
        return result

    def write(self, text: object) -> dict:
        write_text_atomic(self.path(), text, MAX_NOTES_BYTES)
        return {"path": str(self.path())}


def register_lora_notes_routes(routes: web.RouteTableDef, root: Callable[[], Path],
                               installed: Callable[[], list[str]], full_path: Callable[[str], str | None]) -> None:
    notes = LoraNotes(root, installed, full_path)

    @routes.get("/easy-media/agent/loras")
    async def read(request: web.Request) -> web.Response:
        try:
            # Files and LoRA Manager metadata are read off the server's event loop.
            return web.json_response(await asyncio.to_thread(notes.read, request.rel_url.query.get("find")))
        except (OSError, ValueError) as error:
            return web.json_response({"error": str(error)}, status=400)

    @routes.post("/easy-media/agent/loras")
    async def write(request: web.Request) -> web.Response:
        data = await json_object(request)
        try:
            return web.json_response(notes.write(data.get("text")))
        except (OSError, ValueError) as error:
            return web.json_response({"error": str(error)}, status=400)
