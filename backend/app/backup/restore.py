"""Restaura un snapshot y reconcilia tombstones antes de declarar el tráfico abierto."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.hashing import file_sha256
from app.backup.ports import (
    BackupPublisher,
    DatabaseRestorer,
    OriginalsArchiver,
    VectorSnapshotter,
)
from app.documents.store import FileStore
from app.domain.errors import InvalidStateError
from app.domain.lifecycle import (
    TOMBSTONE_TAG,
    BackupManifest,
    ManifestArtifact,
    Tombstone,
    build_manifest,
    parse_tombstone_jsonl,
    verify_checksums,
)
from app.lifecycle.deletion import TenantPointDeleter, reconcile_tombstones

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreResult:
    """Resumen sin texto documental."""

    snapshot_id: str
    tenants: int
    qdrant_restored: bool
    tombstones_applied: int


class RestoreCoordinator:
    """Repone datos y aplica los tombstones más recientes antes de abrir tráfico."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        restorer: DatabaseRestorer,
        snapshots: VectorSnapshotter,
        originals: OriginalsArchiver,
        publisher: BackupPublisher,
        *,
        pseudonym_secret: str,
        vectors: TenantPointDeleter,
        files: FileStore,
    ) -> None:
        self._session_factory = session_factory
        self._restorer = restorer
        self._snapshots = snapshots
        self._originals = originals
        self._publisher = publisher
        self._secret = pseudonym_secret
        self._vectors = vectors
        self._files = files

    async def run(
        self,
        staging: Path,
        *,
        snapshot_id: str,
        skip_qdrant: bool = False,
    ) -> RestoreResult:
        """Restaura y solo entonces informa que se puede abrir el tráfico."""
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._publisher.restore_snapshot, snapshot_id, staging)
        root = _snapshot_root(staging)
        manifest = _read_manifest(root / "manifest.json")
        verify_checksums(
            manifest,
            {artifact.name: file_sha256(root / artifact.name) for artifact in manifest.artifacts},
        )
        await asyncio.to_thread(self._restorer.restore, root / "postgres.dump")
        await asyncio.to_thread(self._originals.restore, root / "originals.tar")
        qdrant_restored = False
        if not skip_qdrant and (root / "qdrant.tar").is_file():
            try:
                await asyncio.to_thread(self._snapshots.restore, root / "qdrant.tar")
                qdrant_restored = True
            except Exception:
                logger.info("qdrant restore status=error")
        tombstones = await asyncio.to_thread(self._latest_tombstones, root)
        applied = await reconcile_tombstones(
            self._session_factory,
            secret=self._secret,
            rows=tombstones,
            vectors=self._vectors,
            files=self._files,
        )
        logger.info(
            "restore status=ok snapshot=%s qdrant=%s tombstones=%d",
            snapshot_id,
            qdrant_restored,
            applied,
        )
        return RestoreResult(
            snapshot_id=snapshot_id,
            tenants=int(manifest.counts.get("tenants", 0)),
            qdrant_restored=qdrant_restored,
            tombstones_applied=applied,
        )

    def _latest_tombstones(self, staging: Path) -> tuple[Tombstone, ...]:
        bundled = (staging / "tombstones.jsonl").read_text(encoding="utf-8")
        rows = list(parse_tombstone_jsonl(bundled))
        try:
            latest = self._publisher.latest_bytes("tombstones.jsonl", tag=TOMBSTONE_TAG)
        except Exception:
            latest = b""
        if latest:
            rows.extend(parse_tombstone_jsonl(latest.decode("utf-8")))
        unique: dict[str, Tombstone] = {}
        for row in rows:
            current = unique.get(row.tenant_hmac)
            if current is None or row.deleted_at >= current.deleted_at:
                unique[row.tenant_hmac] = row
        return tuple(unique.values())


def _snapshot_root(destination: Path) -> Path:
    """restic recrea la ruta original bajo el destino. El manifest marca la raíz."""
    direct = destination / "manifest.json"
    if direct.is_file():
        return destination
    matches = [path.parent for path in destination.rglob("manifest.json") if path.is_file()]
    if len(matches) != 1:
        message = "El snapshot no trae un manifest"
        raise InvalidStateError(message)
    return matches[0]


def _read_manifest(path: Path) -> BackupManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    created = payload["created_at"]
    moment = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    artifacts = tuple(
        ManifestArtifact(name=item["name"], size=int(item["size"]), sha256=str(item["sha256"]))
        for item in payload["artifacts"]
    )
    return build_manifest(
        application_version=str(payload["application_version"]),
        revision=str(payload["revision"]),
        alembic_revision=str(payload["alembic_revision"]),
        created_at=moment,
        collections=tuple(str(name) for name in payload["collections"]),
        counts={str(key): int(value) for key, value in payload["counts"].items()},
        artifacts=artifacts,
    )
