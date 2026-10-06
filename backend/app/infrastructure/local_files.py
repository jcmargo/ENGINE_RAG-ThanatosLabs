"""Almacén local. Las rutas salen de UUID, nunca del nombre enviado por el cliente."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.documents.sniff import sniff_file
from app.documents.store import ReadChunk, StagedUpload
from app.domain.errors import InvalidStateError, PayloadTooLargeError

_STAGED_NAME = re.compile(r"^[0-9a-f]{32}$")
_STORAGE_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_INVALID_PATH = "Ruta de almacenamiento inválida"
_FILE_TOO_LARGE = "El archivo supera el tamaño permitido"
_EMPTY = "El archivo está vacío"
_SYMLINK_ROOT = "La raíz de almacenamiento no puede ser un enlace simbólico"


class LocalFileStore:
    """Guarda originales bajo una raíz configurable, con staging y promoción atómica."""

    def __init__(self, root: Path) -> None:
        if root.exists() and root.is_symlink():
            raise InvalidStateError(_SYMLINK_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._staging = self._root / ".staging"
        self._staging.mkdir(exist_ok=True)

    async def stage(self, read_chunk: ReadChunk, *, max_bytes: int) -> StagedUpload:
        """Escribe el stream en staging, resumiendo y cortando por tamaño."""
        name = uuid4().hex
        path = self._staging / name
        digest = hashlib.sha256()
        size = 0
        handle = await asyncio.to_thread(path.open, "wb")
        try:
            while True:
                chunk = await read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLargeError(_FILE_TOO_LARGE)
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
        except Exception:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(path.unlink, True)
            raise
        await asyncio.to_thread(handle.close)
        if size == 0:
            await asyncio.to_thread(path.unlink, True)
            raise InvalidStateError(_EMPTY)
        return StagedUpload(name=name, byte_size=size, content_sha256=digest.hexdigest())

    def detect_media_type(self, staged: StagedUpload, *, filename: str) -> str:
        """Inspecciona el archivo staged sin moverlo a su clave final."""
        return sniff_file(self._staged_path(staged), filename)

    def promote(self, staged: StagedUpload, relative_key: str) -> str:
        """Renombra el staging a la clave interna en el mismo volumen."""
        source = self._staged_path(staged)
        target = self._resolve_key(relative_key)
        self._mkdir_contained(target.parent)
        os.replace(source, target)
        return relative_key

    def discard(self, staged: StagedUpload) -> None:
        """Elimina un staging huérfano."""
        self._staged_path(staged).unlink(missing_ok=True)

    def delete(self, storage_key: str) -> None:
        """Elimina un original. Un ausente no es un error."""
        path = self._resolve_key(storage_key)
        if path.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        if path.is_file():
            path.unlink()

    def exists(self, storage_key: str) -> bool:
        """True solo si la clave es un archivo regular dentro de la raíz."""
        try:
            path = self._resolve_key(storage_key)
        except InvalidStateError:
            return False
        return path.is_file() and not path.is_symlink()

    def list_stored_mtimes(self) -> dict[str, datetime]:
        """Claves promovidas bajo la raíz con su mtime UTC."""
        found: dict[str, datetime] = {}
        if self._root.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        for tenant_dir in self._root.iterdir():
            if tenant_dir.name.startswith(".") or not tenant_dir.is_dir():
                continue
            if tenant_dir.is_symlink():
                continue
            for document_dir in tenant_dir.iterdir():
                if not document_dir.is_dir() or document_dir.is_symlink():
                    continue
                for version_file in document_dir.iterdir():
                    if not version_file.is_file() or version_file.is_symlink():
                        continue
                    key = f"{tenant_dir.name}/{document_dir.name}/{version_file.name}"
                    if _STORAGE_KEY.fullmatch(key) is None:
                        continue
                    found[key] = datetime.fromtimestamp(version_file.stat().st_mtime, tz=UTC)
        return found

    def list_staged_mtimes(self) -> dict[str, datetime]:
        """Archivos en `.staging` con su mtime UTC."""
        found: dict[str, datetime] = {}
        if self._staging.is_symlink() or not self._staging.is_dir():
            return found
        for path in self._staging.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            if _STAGED_NAME.fullmatch(path.name) is None:
                continue
            found[path.name] = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return found

    def discard_staged_name(self, name: str) -> None:
        """Elimina un staging por nombre interno. Un ausente no es un error."""
        if _STAGED_NAME.fullmatch(name) is None or self._staging.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        path = self._staging / name
        if path.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        path.unlink(missing_ok=True)

    @contextmanager
    def open_read(self, storage_key: str) -> Iterator[BinaryIO]:
        """Abre la clave interna para lectura del worker o de pruebas."""
        path = self._resolve_key(storage_key)
        if path.is_symlink() or not path.is_file():
            raise InvalidStateError(_INVALID_PATH)
        handle = path.open("rb")
        try:
            yield handle
        finally:
            handle.close()

    def _staged_path(self, staged: StagedUpload) -> Path:
        if _STAGED_NAME.fullmatch(staged.name) is None or self._staging.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        path = self._staging / staged.name
        if path.is_symlink():
            raise InvalidStateError(_INVALID_PATH)
        return path

    def _resolve_key(self, relative_key: str) -> Path:
        if _STORAGE_KEY.fullmatch(relative_key) is None:
            raise InvalidStateError(_INVALID_PATH)
        current = self._root
        for part in relative_key.split("/"):
            current = current / part
            if current.is_symlink():
                raise InvalidStateError(_INVALID_PATH)
        resolved = current.resolve()
        if not resolved.is_relative_to(self._root):
            raise InvalidStateError(_INVALID_PATH)
        return current

    def _mkdir_contained(self, directory: Path) -> None:
        relative = directory.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise InvalidStateError(_INVALID_PATH)
            if not current.exists():
                current.mkdir()
            elif not current.is_dir():
                raise InvalidStateError(_INVALID_PATH)
