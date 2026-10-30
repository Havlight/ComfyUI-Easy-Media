"""Files a skill agent keeps for a project: its notes, the plans it ran and the run history.

They live in output/easy_media/agent/<project>/ so they stay beside the generated videos, and go
through the server so an agent in WSL, a container or on another machine can keep them too.
"""

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

# Only the agent's own small text files; nothing executable, nothing outside the project folder.
RECORD_PATH = re.compile(r"(README\.md|history\.json|plans/[A-Za-z0-9_.-]{1,100}\.json)")
MAX_RECORD_BYTES = 4 * 1024 * 1024


class AgentRecords:
    def __init__(self, root: Callable[[], Path], safe_name: Callable[[object], str]) -> None:
        self.root = root
        self.safe_name = safe_name

    def directory(self, project: object) -> Path:
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be the project name")
        return self.root() / self.safe_name(project)

    def _file(self, project: object, path: object) -> Path:
        if not isinstance(path, str) or not RECORD_PATH.fullmatch(path) or ".." in path:
            raise ValueError("path must be README.md, history.json or plans/<name>.json")
        return self.directory(project) / path

    def read(self, project: object, path: object) -> dict:
        target = self._file(project, path)
        text = target.read_text(encoding="utf-8") if target.is_file() else None
        return {"path": str(target), "text": text}

    def list(self, project: object) -> dict:
        directory = self.directory(project)
        files = sorted(item.relative_to(directory).as_posix() for item in directory.rglob("*") if item.is_file()) \
            if directory.is_dir() else []
        return {"directory": str(directory), "files": [name for name in files if RECORD_PATH.fullmatch(name)]}

    def write(self, project: object, path: object, text: object) -> dict:
        target = self._file(project, path)
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError(f"text must be a string of at most {MAX_RECORD_BYTES} bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".record-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
                output.write(text)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return {"path": str(target)}


def register_record_routes(routes: web.RouteTableDef, root: Callable[[], Path],
                           safe_name: Callable[[object], str]) -> None:
    records = AgentRecords(root, safe_name)

    def respond(action: Callable[[], dict]) -> web.Response:
        try:
            return web.json_response(action())
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)

    @routes.get("/easy-media/agent/records")
    async def read(request: web.Request) -> web.Response:
        query = request.rel_url.query
        if "path" in query:
            return respond(lambda: records.read(query.get("project"), query["path"]))
        return respond(lambda: records.list(query.get("project")))

    @routes.post("/easy-media/agent/records")
    async def write(request: web.Request) -> web.Response:
        # Same guard as the workflow bridge: no cross-site form posts.
        if request.content_type != "application/json":
            return web.json_response({"error": "Content-Type must be application/json"}, status=415)
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            return web.json_response({"error": "Cross-origin record writes are not allowed"}, status=403)
        try:
            data = await request.json()
        except (ValueError, UnicodeError):
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "Request must contain a JSON object"}, status=400)
        return respond(lambda: records.write(data.get("project"), data.get("path"), data.get("text")))
