"""Lossless raw AV sidecars and atomic native project version transactions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import torch

from ..modules.motion_context.core import _official_nested_tensor, _streams_from_latent
from .h3_native import validate_native_latent
from .h3_native_timing import NativePlanError
from .file_hash import file_checksum


def native_child_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise NativePlanError("ARTIFACT_PATH", "Native artifacts must be direct files in their project directory.")
    return path


def save_native_latent(latent: dict[str, Any], path: Path) -> dict[str, Any]:
    from safetensors.torch import save_file

    meta = validate_native_latent(latent)
    if path.suffix != ".safetensors":
        raise ValueError("Native latents must use .safetensors")
    start = time.monotonic()
    streams = _streams_from_latent(latent)
    tensors = {f"samples.{i}": value.detach().to("cpu", copy=True).contiguous() for i, value in enumerate(streams)}
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        save_file(tensors, str(temporary), metadata={"h3_native": json.dumps(meta, sort_keys=True, allow_nan=False)})
        # Windows FlushFileBuffers requires a writable handle even though the
        # safetensors writer has already closed and flushed its own handle.
        with temporary.open("r+b") as saved:
            os.fsync(saved.fileno())
        checksum = file_checksum(temporary)
        temporary.replace(path)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to save native H3 latent: {error}") from error
    return {"file": path.name, "sha256": checksum, "bytes": path.stat().st_size,
            "save_seconds": time.monotonic() - start, "metadata": meta}


def load_native_latent(directory: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    from safetensors import safe_open

    path = native_child_path(directory, descriptor["file"])
    if not path.is_file():
        raise NativePlanError("ARTIFACT_MISSING", "The native source file is missing; restore or regenerate its version.")
    if file_checksum(path) != descriptor.get("sha256"):
        raise NativePlanError("CHECKSUM", "The native source file is damaged; restore or regenerate it. VAE fallback cannot repair corruption.")
    with safe_open(str(path), framework="pt", device="cpu") as source:
        meta = json.loads((source.metadata() or {}).get("h3_native", "null"))
        if meta != descriptor.get("metadata") or set(source.keys()) != {"samples.0", "samples.1"}:
            raise NativePlanError("ARTIFACT_METADATA", "The manifest and native sidecar do not agree.")
        parts = tuple(source.get_tensor(f"samples.{index}") for index in range(2))
    latent = {"samples": _official_nested_tensor(parts), "h3_native": meta}
    validate_native_latent(latent)
    return latent


@contextmanager
def native_project_transaction(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or (directory / "project.json").is_symlink():
        raise NativePlanError("PROJECT_PATH", "Project paths must not be symbolic links.")
    lock = directory / ".native-write.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise NativePlanError("PROJECT_BUSY", "Another project write is in progress. After an interrupted process, remove .native-write.lock only after confirming no writer is running.") from error
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def read_native_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "project.json"
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NativePlanError("MANIFEST", f"Cannot read project manifest: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("segments", {}), dict):
        raise NativePlanError("MANIFEST", "The project manifest must contain a segment object.")
    return manifest


def find_native_generation(manifest: dict[str, Any], segment_id: str) -> dict[str, Any]:
    for segment in manifest.get("segments", {}).values():
        if segment.get("segment_id") == segment_id:
            generation = segment.get("generations", {}).get(str(segment.get("active_generation")))
            if isinstance(generation, dict):
                if generation.get("native_stale"):
                    raise NativePlanError("STALE_SOURCE", "The source depends on a replaced version; regenerate the chain.", segment_id)
                return generation
    raise NativePlanError("SOURCE_MISSING", "No active version exists for the required task; generate it first.", segment_id)


def native_dependents(manifest: dict[str, Any], artifact_ids: set[str]) -> list[str]:
    """All versions count, including detached tasks retained after timeline edits."""
    found: list[str] = []
    segments = list(manifest.get("segments", {}).values()) + list(manifest.get("detached_segments", {}).values())
    for segment in segments:
        for generation in segment.get("generations", {}).values():
            if any(set(native_parent_ids(d.get("metadata", {}))) & artifact_ids
                   for d in generation.get("native", {}).values()):
                found.append(str(segment.get("segment_id", "unknown")))
    return found


def native_parent_ids(metadata: dict[str, Any]) -> list[str]:
    return [value for value in [metadata.get("parent_artifact_id"), *metadata.get("reference_artifact_ids", [])] if value]


def media_version_id(segment_id: str, generation_id: str, generation: dict[str, Any]) -> str:
    """Stable dependency on a delivered version when its conditioning is rebuilt."""
    identity = [segment_id, str(generation_id), generation.get("video"), generation.get("audio"), generation.get("updated_at")]
    return "media:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def refresh_native_dependencies(manifest: dict[str, Any]) -> None:
    """A version is valid only while its exact parent versions are active and valid."""
    active = [(s.get("segment_id", ""), str(s.get("active_generation")),
               s.get("generations", {}).get(str(s.get("active_generation")), {}))
              for s in manifest.get("segments", {}).values()]
    valid_ids: set[str] = set()
    remaining = [item for item in active if item[2]]
    while remaining:
        accepted = [item for item in remaining if all(set(native_parent_ids(d["metadata"])) <= valid_ids
                    for d in item[2].get("native", {}).values())]
        if not accepted:
            break
        for item in accepted:
            sid, gid, generation = item
            generation.pop("native_stale", None)
            valid_ids.update(d["metadata"]["artifact_id"] for d in generation.get("native", {}).values())
            valid_ids.add(media_version_id(sid, gid, generation))
            remaining.remove(item)
    for _, _, generation in remaining:
        generation["native_stale"] = True


def commit_native_generation(
    directory: Path, segment_index: int, project_fields: dict[str, Any],
    record: dict[str, Any], latents: dict[str, dict[str, Any]],
    media: dict[str, Path],
) -> dict[str, Any]:
    """Publish new files, then switch one manifest atomically. Never evict an old version.

    Override is intentionally a new recoverable generation for native projects.
    Source staging media remain available if any write fails.
    """
    if set(latents) - {"high", "low"} or "high" not in latents:
        raise NativePlanError("STAGE", "A generation needs a high stage and at most one low stage.")
    meta = validate_native_latent(latents["high"])
    for latent in latents.values():
        current = validate_native_latent(latent)
        if current["segment_id"] != meta["segment_id"]:
            raise NativePlanError("SEGMENT_ID", "Stages belong to different tasks.")
    required = sum(t.numel() * t.element_size() for latent in latents.values() for t in _streams_from_latent(latent))
    required += sum(path.stat().st_size for path in media.values()) + 1024 * 1024
    with native_project_transaction(directory):
        if shutil.disk_usage(directory).free < required:
            raise NativePlanError("DISK_SPACE", f"Need at least {required} additional bytes; old versions have been preserved.")
        manifest = read_native_manifest(directory)
        old_segments = manifest.get("segments", {})
        tasks = project_fields["task_segments"]
        detached = manifest.setdefault("detached_segments", {})
        by_id = {**detached, **{s["segment_id"]: s for s in old_segments.values() if s.get("segment_id")}}
        segments: dict[str, Any] = {}
        for index, task in enumerate(tasks):
            sid = task["segment_id"]
            prior = by_id.pop(sid, None)
            if prior is None:
                # Legacy versions remain visible during a first explicit migration.
                prior = old_segments.get(str(index), {}) if not manifest.get("h3_native") else {}
            segments[str(index)] = {**prior, "segment_id": sid}
        manifest["detached_segments"] = detached = by_id
        target = segments[str(segment_index)]
        if target["segment_id"] != meta["segment_id"]:
            raise NativePlanError("SEGMENT_ID", "The artifact does not belong at the selected timeline index.")
        # Global monotonically increasing IDs also prevent filename collisions after reorder.
        versions = [int(k) for s in list(old_segments.values()) + list(detached.values()) for k in s.get("generations", {})]
        generation = max(versions, default=-1) + 1
        staging = Path(tempfile.mkdtemp(prefix=".native-generation-", dir=directory))
        published: list[Path] = []
        committed = False
        try:
            saved = {**record, "native": {}, "updated_at": time.time()}
            for stage, latent in latents.items():
                field = "context_latent" if stage == "high" else "context_latent_low"
                name = f"{field}_{segment_index}_{generation}.safetensors"
                saved["native"][stage] = save_native_latent(latent, staging / name)
                saved[field] = name
            for field, source in media.items():
                if field not in {"video", "audio", "raw_audio", "last_frame", "locked_audio"}:
                    raise ValueError(f"Unsupported project media field: {field}")
                name = f"{field}_{segment_index}_{generation}{source.suffix}"
                shutil.copyfile(source, staging / name)
                saved[field] = name
            target.setdefault("generations", {})[str(generation)] = saved
            target.update({"active_generation": generation, "updated_at": saved["updated_at"],
                           "continuity_mode": record.get("continuity_mode", "shot")})
            manifest.update({**project_fields, "version": 3, "segments": segments, "updated_at": time.time()})
            manifest.pop("last_render", None)
            manifest.pop("tracks_info", None)
            refresh_native_dependencies(manifest)
            for source in staging.iterdir():
                destination = directory / source.name
                if destination.exists():
                    raise NativePlanError("VERSION_COLLISION", "A version filename already exists; no existing artifact was replaced.")
                source.replace(destination)
                published.append(destination)
            temporary = staging / "project.json"
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(directory / "project.json")
            committed = True
            return saved
        finally:
            if not committed:
                for path in published:
                    path.unlink(missing_ok=True)
            shutil.rmtree(staging)
