"""Archivos de exportación bajo `.exports`, fuera del barrido de huérfanos."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from app.domain.errors import InvalidStateError

_KEY = re.compile(r"^\.exports/[0-9a-fA-F-]{36}\.bin$")


class ExportFileStore:
    """Guarda ciphertext. No acepta una clave que salga del directorio."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._directory = root / ".exports"

    def write(self, package_id: UUID, payload: bytes) -> str:
        """Escribe el paquete y devuelve la clave interna."""
        self._directory.mkdir(parents=True, exist_ok=True)
        key = f".exports/{package_id}.bin"
        path = self._path(key)
        path.write_bytes(payload)
        return key

    def read(self, key: str) -> bytes:
        """Lee el ciphertext."""
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """Borra el paquete. Un ausente no es un error."""
        path = self._path(key)
        if path.is_file() and not path.is_symlink():
            path.unlink()

    def _path(self, key: str) -> Path:
        if _KEY.fullmatch(key) is None:
            message = "La clave de exportación no es válida"
            raise InvalidStateError(message)
        path = self._root / key
        if path.is_symlink():
            message = "La clave de exportación no es válida"
            raise InvalidStateError(message)
        return path
