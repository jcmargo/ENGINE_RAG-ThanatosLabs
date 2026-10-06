"""Tar de originales. Ignora staging, exportaciones y enlaces simbólicos."""

from __future__ import annotations

import tarfile
from pathlib import Path

from app.domain.errors import InvalidStateError


class OriginalsArchive:
    """Empaqueta el almacén de archivos sin los paquetes temporales."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def archive(self, destination: Path) -> None:
        """Crea el tar. Un directorio ausente produce un tar vacío."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w") as archive:
            if not self._root.is_dir() or self._root.is_symlink():
                return
            for path in sorted(self._root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                if _skipped(path.relative_to(self._root)):
                    continue
                archive.add(path, arcname=path.relative_to(self._root).as_posix())

    def restore(self, source: Path) -> None:
        """Repone los archivos. Rechaza rutas que salgan de la raíz."""
        self._root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk() or not member.isfile():
                    continue
                relative = Path(member.name)
                if relative.is_absolute() or ".." in relative.parts or _skipped(relative):
                    message = "El archivo de originales no es seguro"
                    raise InvalidStateError(message)
                target = self._root.joinpath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                target.write_bytes(extracted.read())


def _skipped(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)
