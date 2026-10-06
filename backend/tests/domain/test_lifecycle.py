"""Reglas puras de backup, tombstones y retención."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.ports import BackupPublisher, DatabaseDumper, OriginalsArchiver, VectorSnapshotter
from app.backup.service import BackupCoordinator
from app.domain.errors import InvalidStateError
from app.domain.lifecycle import (
    ManifestArtifact,
    RetentionOutcome,
    assert_public_mapping,
    assert_repository_location,
    backup_blocks_mutations,
    build_manifest,
    parse_tombstone_jsonl,
    tenant_tombstone_hmac,
    tombstone_jsonl,
    verify_checksums,
)
from app.domain.lifecycle import Tombstone as TombstoneRow
from app.infrastructure.restic_store import ResticRepository

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
_PHRASE = "frase-documental-que-no-puede-salir"


def test_tombstone_hmac_has_no_email() -> None:
    tenant_id = uuid4()
    digest = tenant_tombstone_hmac("pseudonym-secret", tenant_id)

    assert len(digest) == 64
    assert "@" not in digest
    assert "ana.ejemplo@example.com" not in digest


def test_repository_inside_uploads_is_rejected(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    nested = uploads / "restic"
    with pytest.raises(InvalidStateError):
        assert_repository_location(str(nested), (str(uploads),))
    with pytest.raises(InvalidStateError):
        ResticRepository(str(nested), tmp_path / "password", protected_paths=(str(uploads),))

    assert_repository_location("s3:bucket/engine-rag", (str(uploads),))


def test_manifest_rejects_secrets_and_bad_checksums() -> None:
    with pytest.raises(InvalidStateError):
        assert_public_mapping({"api_key": "valor"})
    manifest = build_manifest(
        application_version="0.1.0",
        revision="dev",
        alembic_revision="0010_lifecycle",
        created_at=_NOW,
        collections=("coleccion",),
        counts={"tenants": 1},
        artifacts=(ManifestArtifact(name="config.json", size=2, sha256="ab" * 32),),
    )
    with pytest.raises(InvalidStateError):
        verify_checksums(manifest, {"config.json": (2, "cd" * 32)})


def test_tombstone_json_rejects_extra_fields() -> None:
    payload = tombstone_jsonl((TombstoneRow(tenant_hmac="ab" * 32, deleted_at=_NOW),))
    assert "email" not in payload
    assert parse_tombstone_jsonl(payload)[0].tenant_hmac == "ab" * 32
    with pytest.raises(InvalidStateError):
        parse_tombstone_jsonl(
            '{"tenant_hmac":"ab","deleted_at":"2026-10-05T12:00:00Z","email":"a@b.c"}\n'
        )


def test_dry_run_does_not_ask_for_another_batch() -> None:
    outcome = RetentionOutcome(conversations=3, traces=0, audit=0, exports=0, dry_run=True)
    assert outcome.more is False
    assert backup_blocks_mutations(status="running", expires_at=_NOW, now=_NOW) is False


def test_capture_manifest_omits_document_text(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    class _Dump:
        def dump(self, destination: Path) -> None:
            destination.write_bytes(_PHRASE.encode("utf-8"))

    class _Files:
        def archive(self, destination: Path) -> None:
            destination.write_bytes(b"files")

        def restore(self, source: Path) -> None:
            return None

    class _Vectors:
        def capture(self, destination: Path) -> tuple[str, ...]:
            destination.write_bytes(b"qdrant")
            return ("coleccion",)

        def restore(self, source: Path) -> None:
            return None

    class _Publisher:
        def publish(self, directory: Path, *, tags: tuple[str, ...]) -> str:
            return "snapshot"

        def verify(self) -> None:
            return None

        def apply_retention(self) -> None:
            return None

        def publish_bytes(self, name: str, payload: bytes, *, tags: tuple[str, ...]) -> None:
            return None

        def latest_bytes(self, name: str, *, tag: str) -> bytes:
            return b""

        def restore_snapshot(self, snapshot_id: str, destination: Path) -> None:
            return None

    coordinator = BackupCoordinator(
        cast(async_sessionmaker[AsyncSession], object()),
        cast(DatabaseDumper, _Dump()),
        cast(VectorSnapshotter, _Vectors()),
        cast(OriginalsArchiver, _Files()),
        cast(BackupPublisher, _Publisher()),
        lock_seconds=3600,
        application_version="0.1.0",
        revision="dev",
        public_config={"embedding_model": "fixture"},
    )
    manifest, snapshot_id = coordinator._capture(
        staging, {"tenants": 1}, "0010_lifecycle", "", _NOW
    )
    body = (staging / "manifest.json").read_text(encoding="utf-8")

    assert snapshot_id == "snapshot"
    assert manifest.digest()
    assert _PHRASE not in body
    assert "password" not in body
