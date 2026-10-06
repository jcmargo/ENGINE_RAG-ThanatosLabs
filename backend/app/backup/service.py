"""Coordina la captura sin mantener una transacción durante la copia de archivos."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.hashing import file_sha256
from app.backup.ports import (
    BackupPublisher,
    DatabaseDumper,
    OriginalsArchiver,
    VectorSnapshotter,
)
from app.domain.errors import ConflictError
from app.domain.lifecycle import (
    BACKUP_TAG,
    TOMBSTONE_TAG,
    BackupManifest,
    ManifestArtifact,
    assert_public_mapping,
    build_manifest,
    manifest_json,
    tombstone_jsonl,
)
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.persistence.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

_DELETE_WAIT_SECONDS = 30


@dataclass(frozen=True)
class BackupResult:
    """Resultado público. El manifest no incluye texto documental."""

    run_id: UUID
    manifest: BackupManifest
    snapshot_id: str


class BackupCoordinator:
    """Dump, archivos, snapshot, manifest y restic, en ese orden."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        dumper: DatabaseDumper,
        snapshots: VectorSnapshotter,
        originals: OriginalsArchiver,
        publisher: BackupPublisher,
        *,
        lock_seconds: int,
        application_version: str,
        revision: str,
        public_config: dict[str, object],
        clock: Clock | None = None,
    ) -> None:
        assert_public_mapping(public_config)
        self._session_factory = session_factory
        self._dumper = dumper
        self._snapshots = snapshots
        self._originals = originals
        self._publisher = publisher
        self._lock_seconds = lock_seconds
        self._application_version = application_version
        self._revision = revision
        self._public_config = public_config
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def run(self, staging: Path) -> BackupResult:
        """Ejecuta una captura. Un fallo cierra la corrida y libera el bloqueo."""
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
        now = self._clock.now()
        run_id = uuid4()
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyLifecycleRepository(unit.session)
            await repository.begin_run(run_id=run_id, now=now, lock_seconds=self._lock_seconds)
            counts = await repository.counts()
            alembic_revision = await repository.alembic_revision()
            tombstones = tombstone_jsonl(await repository.list_tombstones())
            await unit.commit()
        try:
            await self._wait_for_deletes()
            manifest, snapshot_id = await asyncio.to_thread(
                self._capture,
                staging,
                counts,
                alembic_revision,
                tombstones,
                now,
            )
        except Exception:
            await self._finish(run_id, "failed", None)
            logger.info("backup status=error run=%s", run_id)
            raise
        await self._finish(run_id, "succeeded", manifest.digest())
        logger.info(
            "backup status=ok run=%s tenants=%d sha256=%s",
            run_id,
            counts["tenants"],
            manifest.digest(),
        )
        return BackupResult(run_id=run_id, manifest=manifest, snapshot_id=snapshot_id)

    def _capture(
        self,
        staging: Path,
        counts: dict[str, int],
        alembic_revision: str,
        tombstones: str,
        now: datetime,
    ) -> tuple[BackupManifest, str]:
        dump_path = staging / "postgres.dump"
        originals_path = staging / "originals.tar"
        qdrant_path = staging / "qdrant.tar"
        config_path = staging / "config.json"
        tombstone_path = staging / "tombstones.jsonl"
        self._dumper.dump(dump_path)
        self._originals.archive(originals_path)
        collections = self._snapshots.capture(qdrant_path)
        config_path.write_text(
            json.dumps(self._public_config, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        tombstone_path.write_text(tombstones, encoding="utf-8")
        artifacts = tuple(
            _artifact(path)
            for path in (dump_path, originals_path, qdrant_path, config_path, tombstone_path)
        )
        manifest = build_manifest(
            application_version=self._application_version,
            revision=self._revision,
            alembic_revision=alembic_revision,
            created_at=now,
            collections=collections,
            counts=counts,
            artifacts=artifacts,
        )
        (staging / "manifest.json").write_text(manifest_json(manifest), encoding="utf-8")
        snapshot_id = self._publisher.publish(staging, tags=(BACKUP_TAG,))
        self._publisher.verify()
        self._publisher.apply_retention()
        return manifest, snapshot_id

    async def _wait_for_deletes(self) -> None:
        for _ in range(_DELETE_WAIT_SECONDS):
            async with UnitOfWork(self._session_factory) as unit:
                pending = await SqlAlchemyLifecycleRepository(unit.session).leased_delete_count()
            if pending == 0:
                return
            await asyncio.sleep(1)
        message = "Hay un borrado de documento en curso"
        raise ConflictError(message)

    async def _finish(self, run_id: UUID, status: str, digest: str | None) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            await SqlAlchemyLifecycleRepository(unit.session).finish_run(
                run_id=run_id,
                status=status,
                finished_at=self._clock.now(),
                manifest_sha256=digest,
            )
            await unit.commit()


def _artifact(path: Path) -> ManifestArtifact:
    size, digest = file_sha256(path)
    return ManifestArtifact(name=path.name, size=size, sha256=digest)


async def publish_tombstones(
    session_factory: async_sessionmaker[AsyncSession],
    publisher: BackupPublisher | None,
) -> bool:
    """Sube el conjunto de tombstones. Sin repositorio, no finge que lo hizo."""
    if publisher is None:
        logger.info("tombstone restic status=skipped")
        return False
    async with UnitOfWork(session_factory) as unit:
        payload = tombstone_jsonl(
            await SqlAlchemyLifecycleRepository(unit.session).list_tombstones()
        )
    await asyncio.to_thread(
        publisher.publish_bytes,
        "tombstones.jsonl",
        payload.encode("utf-8"),
        tags=(TOMBSTONE_TAG,),
    )
    logger.info("tombstone restic status=ok bytes=%d", len(payload))
    return True
