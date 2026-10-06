"""SHA-256 de artefactos. No interpreta el contenido."""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> tuple[int, str]:
    """Tamaño y SHA-256. Lee por bloques para no cargar el dump entero."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()
