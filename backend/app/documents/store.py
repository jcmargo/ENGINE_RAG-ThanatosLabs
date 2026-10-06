"""Contrato de almacenamiento sustituible. No conoce HTTP ni PostgreSQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Protocol

ReadChunk = Callable[[], Awaitable[bytes]]


@dataclass(frozen=True)
class StagedUpload:
    """Archivo temporal ya medido y resumido. El nombre no es una ruta del cliente."""

    name: str
    byte_size: int
    content_sha256: str


class FileStore(Protocol):
    """Escritura, lectura interna y promoción atómica de originales."""

    async def stage(self, read_chunk: ReadChunk, *, max_bytes: int) -> StagedUpload:
        """Escribe un stream en staging y corta al superar `max_bytes`."""

    def detect_media_type(self, staged: StagedUpload, *, filename: str) -> str:
        """Inspecciona el staging y devuelve el MIME validado."""

    def promote(self, staged: StagedUpload, relative_key: str) -> str:
        """Mueve el staging a la clave interna de forma atómica."""

    def discard(self, staged: StagedUpload) -> None:
        """Borra un staging que no se llegó a confirmar."""

    def delete(self, storage_key: str) -> None:
        """Borra un original interno. No falla si ya no existe."""

    def exists(self, storage_key: str) -> bool:
        """Indica si la clave interna apunta a un archivo regular."""

    def open_read(self, storage_key: str) -> AbstractContextManager[BinaryIO]:
        """Abre un original para lectura interna."""
        ...


def iter_chunks(chunks: Iterator[bytes]) -> ReadChunk:
    """Adapta un iterador síncrono al contrato async del almacén. Útil en pruebas."""

    iterator = iter(chunks)

    async def _read() -> bytes:
        return next(iterator, b"")

    return _read
