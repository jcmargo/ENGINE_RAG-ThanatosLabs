"""Puertos del backup. Los binarios y el HTTP viven en infraestructura."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DatabaseDumper(Protocol):
    """Vuelca PostgreSQL en formato custom."""

    def dump(self, destination: Path) -> None:
        """Escribe el dump en `destination`."""


class DatabaseRestorer(Protocol):
    """Restaura un dump custom sobre una base vacía o limpia."""

    def restore(self, source: Path) -> None:
        """Aplica el dump."""


class VectorSnapshotter(Protocol):
    """Pide y descarga snapshots de Qdrant."""

    def capture(self, destination: Path) -> tuple[str, ...]:
        """Empaqueta los snapshots y devuelve los nombres de colección."""

    def restore(self, source: Path) -> None:
        """Sube los snapshots. Si no puede, el llamador reconstruye."""


class OriginalsArchiver(Protocol):
    """Copia los originales sin mantener una transacción abierta."""

    def archive(self, destination: Path) -> None:
        """Escribe el tar de originales."""

    def restore(self, source: Path) -> None:
        """Repone los originales."""


class BackupPublisher(Protocol):
    """Repositorio restic cifrado."""

    def publish(self, directory: Path, *, tags: tuple[str, ...]) -> str:
        """Sube el directorio y devuelve el id del snapshot."""

    def verify(self) -> None:
        """Comprueba el repositorio."""

    def apply_retention(self) -> None:
        """Aplica la política diaria, semanal y mensual."""

    def publish_bytes(self, name: str, payload: bytes, *, tags: tuple[str, ...]) -> None:
        """Sube un único archivo, por ejemplo los tombstones."""

    def latest_bytes(self, name: str, *, tag: str) -> bytes:
        """Lee un archivo del snapshot más reciente con esa etiqueta."""

    def restore_snapshot(self, snapshot_id: str, destination: Path) -> None:
        """Materializa un snapshot en un directorio vacío."""
