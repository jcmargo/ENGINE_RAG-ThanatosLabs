"""Prepara los volúmenes compartidos para el usuario de la API y el worker."""

from __future__ import annotations

import os
from pathlib import Path

_UID = 10001
_GID = 10001
_PATHS = (Path("/data/uploads"), Path("/cache/embeddings"), Path("/backups"))
# El volumen de certificados solo existe en el overlay del VPS.
_OPTIONAL_PATHS = (Path("/data/caddy"),)


def main() -> None:
    """Crea los directorios y se los entrega al uid 10001."""
    for path in _PATHS:
        _own(path)
    for path in _OPTIONAL_PATHS:
        if path.is_dir():
            _own(path)


def _own(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for current, directories, files in os.walk(path):
        os.chown(current, _UID, _GID)
        for name in (*directories, *files):
            os.chown(str(Path(current) / name), _UID, _GID)


if __name__ == "__main__":
    main()
