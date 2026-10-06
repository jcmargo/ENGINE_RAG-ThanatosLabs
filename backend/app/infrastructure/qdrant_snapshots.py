"""Snapshots HTTP de Qdrant. No devuelve el contenido de los puntos al log."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

from app.domain.errors import InvalidStateError


class QdrantSnapshots:
    """Crea, descarga y vuelve a subir snapshots de cada colección."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def capture(self, destination: Path) -> tuple[str, ...]:
        """Empaqueta un snapshot por colección y lo borra del servidor."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=self._timeout) as client:
            names = _collection_names(client, self._base_url)
            with tarfile.open(destination, "w") as archive:
                for name in names:
                    created = client.post(f"{self._base_url}/collections/{name}/snapshots")
                    _raise(created)
                    snapshot = str(created.json()["result"]["name"])
                    downloaded = client.get(
                        f"{self._base_url}/collections/{name}/snapshots/{snapshot}"
                    )
                    _raise(downloaded)
                    payload = downloaded.content
                    info = tarfile.TarInfo(name=f"{name}/{snapshot}")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
                    client.delete(f"{self._base_url}/collections/{name}/snapshots/{snapshot}")
        return names

    def restore(self, source: Path) -> None:
        """Sube cada snapshot. Un fallo deja la reconstrucción al llamador."""
        with tarfile.open(source, "r") as archive, httpx.Client(timeout=self._timeout) as client:
            for member in archive.getmembers():
                if not member.isfile() or "/" not in member.name:
                    continue
                collection, snapshot_name = member.name.split("/", 1)
                extracted = archive.extractfile(member)
                if extracted is None:
                    message = "El snapshot de Qdrant está incompleto"
                    raise InvalidStateError(message)
                response = client.post(
                    f"{self._base_url}/collections/{collection}/snapshots/upload",
                    params={"priority": "snapshot"},
                    files={"snapshot": (snapshot_name, extracted.read())},
                )
                _raise(response)


def _collection_names(client: httpx.Client, base_url: str) -> tuple[str, ...]:
    response = client.get(f"{base_url}/collections")
    _raise(response)
    collections = response.json()["result"]["collections"]
    return tuple(str(item["name"]) for item in collections)


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = "Qdrant no aceptó el snapshot"
    raise InvalidStateError(message)
