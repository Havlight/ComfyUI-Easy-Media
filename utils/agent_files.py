"""Small text files a skill agent keeps through the server: an atomic write and the guard on posts."""

import json
import os
import tempfile
from pathlib import Path

from aiohttp import web


def write_text_atomic(target: Path, text: object, max_bytes: int) -> None:
    """Replace `target` with `text` (at most `max_bytes` in UTF-8) so a reader never sees half of it."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"text must be a string of at most {max_bytes} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _refuse(error: type[web.HTTPException], message: str) -> web.HTTPException:
    return error(text=json.dumps({"error": message}), content_type="application/json")


async def json_object(request: web.Request) -> dict:
    """The posted JSON object. Refuses cross-site form posts even when the server allows broad CORS."""
    if request.content_type != "application/json":
        raise _refuse(web.HTTPUnsupportedMediaType, "Content-Type must be application/json")
    origin = request.headers.get("Origin")
    if origin and origin != f"{request.scheme}://{request.host}":
        raise _refuse(web.HTTPForbidden, "Cross-origin writes are not allowed")
    try:
        data = await request.json()
    except (ValueError, UnicodeError) as error:
        raise _refuse(web.HTTPBadRequest, "Invalid JSON") from error
    if not isinstance(data, dict):
        raise _refuse(web.HTTPBadRequest, "Request must contain a JSON object")
    return data
