"""Resolve the files an imported H3 plan names.

A plan names references by local path or URL (``file``), by a path under ComfyUI's
input or output folder (``input`` / ``output``), or as a local file kept in place
(``local``). ``file`` references are copied under ``input/easy_media/imports`` and
named by their content hash, so repeated imports reuse one copy and the project
keeps working after the source folder changes. The other kinds are only checked.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import folder_paths

from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

IMPORT_SUBFOLDER = "easy_media/imports"
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3
DOWNLOAD_TIMEOUT_SECONDS = 60
MEDIA_EXTENSIONS = {"image": IMAGE_EXTENSIONS, "video": VIDEO_EXTENSIONS, "audio": AUDIO_EXTENSIONS}
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
    "audio/ogg": ".ogg", "audio/mp4": ".m4a",
}
CHUNK_SIZE = 1024 * 1024


class PlanMediaError(ValueError):
    """A plan reference that cannot be used."""


def _extension(name: str, media_type: str) -> str:
    extension = Path(name).suffix.lower()
    if extension not in MEDIA_EXTENSIONS[media_type]:
        raise PlanMediaError(f"{name} is not a supported {media_type} file")
    return extension


def _import_directory() -> Path:
    directory = Path(folder_paths.get_input_directory()) / IMPORT_SUBFOLDER
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _store(source: Path, extension: str) -> str:
    """Copy a file under the import folder by content hash; return its input path."""
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    name = f"{digest.hexdigest()[:16]}{extension}"
    target = _import_directory() / name
    if not target.exists():
        partial = target.with_name(f".{name}.{uuid.uuid4().hex[:8]}.part")
        try:
            with source.open("rb") as reader, partial.open("wb") as writer:
                for chunk in iter(lambda: reader.read(CHUNK_SIZE), b""):
                    writer.write(chunk)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
    return f"{IMPORT_SUBFOLDER}/{name}"


def _download(url: str, media_type: str) -> tuple[str, str]:
    """Download a URL and store it; return (input path, display name)."""
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    with tempfile.TemporaryDirectory(prefix=".plan-download-", dir=_import_directory()) as work:
        path = Path(work) / "download"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ComfyUI-Easy-Media"})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
                content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
                size = 0
                with path.open("wb") as writer:
                    for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise PlanMediaError(f"{url} is larger than {MAX_DOWNLOAD_BYTES // 1024 ** 3} GB")
                        writer.write(chunk)
        except PlanMediaError:
            raise
        except (OSError, ValueError) as error:
            raise PlanMediaError(f"{url} could not be downloaded: {error}") from error
        if Path(name).suffix.lower() in MEDIA_EXTENSIONS[media_type]:
            extension = Path(name).suffix.lower()
        else:
            extension = _extension(f"download{CONTENT_TYPE_EXTENSIONS.get(content_type, '')}", media_type)
            name = f"{Path(name).stem or 'download'}{extension}"
        return _store(path, extension), name


def _folder_file(base: str, relative: str, media_type: str) -> tuple[Path, str]:
    root = Path(base).resolve()
    path = (root / relative).resolve()
    try:
        normalized = path.relative_to(root).as_posix()
    except ValueError as error:
        raise PlanMediaError(f"{relative} is outside the folder") from error
    if not path.is_file():
        raise PlanMediaError(f"{relative} was not found")
    _extension(path.name, media_type)
    return path, normalized


def _media_path(result: dict[str, Any]) -> Path:
    if result["source_type"] == "local":
        return Path(result["local_path"])
    base = folder_paths.get_input_directory() if result["source_type"] == "input" else folder_paths.get_output_directory()
    return Path(base) / result["file_path"]


def _probe(path: Path) -> dict[str, Any]:
    from .video import ffprobe_info

    try:
        return ffprobe_info(str(path)) or {}
    except RuntimeError:
        return {}


def resolve_plan_media(item: Any) -> dict[str, Any]:
    """Resolve one plan reference; raise PlanMediaError when it cannot be used.

    With ``probe: true`` a video or audio result also carries ``duration`` in seconds, and a
    video ``has_audio``; either is None when the file cannot be read. Timeline clips placed
    without an end take the whole file, so they need its length.
    """
    result: dict[str, Any] = _resolve(item)
    if item.get("probe") is True and item["media_type"] in {"video", "audio"}:
        info = _probe(_media_path(result))
        result["duration"] = info.get("duration")
        if item["media_type"] == "video":
            result["has_audio"] = info.get("has_audio") if info else None
    return result


def _resolve(item: Any) -> dict[str, str]:
    if not isinstance(item, dict) or item.get("media_type") not in MEDIA_EXTENSIONS:
        raise PlanMediaError("each item needs a media_type of image, video or audio")
    media_type = str(item["media_type"])
    kinds = [kind for kind in ("file", "local", "input", "output") if isinstance(item.get(kind), str) and item[kind]]
    if len(kinds) != 1:
        raise PlanMediaError("each item needs exactly one of file, local, input or output")
    kind = kinds[0]
    value = str(item[kind])
    if kind == "file" and value.lower().startswith(("http://", "https://")):
        file_path, name = _download(value, media_type)
        return {"source_type": "input", "file_path": file_path, "file_name": name}
    if kind in {"file", "local"}:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise PlanMediaError(f"{value} is not an absolute path")
        if not path.is_file():
            raise PlanMediaError(f"{value} was not found")
        extension = _extension(path.name, media_type)
        if kind == "local":
            return {"source_type": "local", "local_path": str(path), "file_name": path.name}
        return {"source_type": "input", "file_path": _store(path, extension), "file_name": path.name}
    base = folder_paths.get_input_directory() if kind == "input" else folder_paths.get_output_directory()
    path, normalized = _folder_file(base, value, media_type)
    return {"source_type": kind, "file_path": normalized, "file_name": path.name}
