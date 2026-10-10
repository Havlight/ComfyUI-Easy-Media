"""Streaming content identities without loading media or model runtimes."""
from __future__ import annotations

import hashlib
from pathlib import Path


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
