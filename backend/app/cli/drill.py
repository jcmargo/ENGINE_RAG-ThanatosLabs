"""Simulacro aislado: `python -m app.cli.drill`. No toca el stack de producto."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.restore import RestoreCoordinator
from app.backup.service import BackupCoordinator
from app.backup.wiring import public_config
from app.config import Environment, Settings, repository_root
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.local_files import LocalFileStore
from app.infrastructure.originals_archive import OriginalsArchive
from app.infrastructure.pg_dump import ComposePostgres
from app.infrastructure.qdrant_snapshots import QdrantSnapshots
from app.infrastructure.restic_store import DockerResticRepository
from app.infrastructure.tenant_points import QdrantTenantPoints
from app.infrastructure.vector_client import create_qdrant_client
from app.lifecycle.rebuild import enqueue_ready_reindex
from app.persistence.models.document import DocumentModel
from app.persistence.models.document_version import DocumentVersionModel
from app.persistence.models.history import ConversationModel, MessageModel
from app.persistence.models.membership import MembershipModel
from app.persistence.models.tenant import TenantModel
from app.persistence.models.user import UserModel

_RESTIC_IMAGE = (
    "restic/restic:0.18.1@sha256:39d9072fb5651c80d75c7a811612eb60b4c06b32ffe87c2e9f3c7222e1797e76"
)
_COMPOSE = (
    "docker",
    "compose",
    "-p",
    "engine-rag-drill",
    "-f",
    str(repository_root() / "infra" / "compose.drill.yml"),
)
_DATABASE_URL = "postgresql+asyncpg://engine_rag:engine_rag_drill@127.0.0.1:55432/engine_rag"
_QDRANT_URL = "http://127.0.0.1:16333"
_COLLECTION = "engine_rag_drill"
_MARKER = "cita-fixture-vacaciones-18"
_LATER_EMAIL = "after-backup@example.com"
_RTO_LIMIT_SECONDS = 4 * 60 * 60


def main() -> int:
    """Mide la restauración en volúmenes vacíos y escribe un informe sin secretos."""
    started = time.perf_counter()
    report: dict[str, object] = {"result": "fallido"}
    try:
        _compose("up", "-d", "--wait", "--wait-timeout", "180")
        report = asyncio.run(_exercise())
    finally:
        _compose("down", "-v")
    duration = time.perf_counter() - started
    report["duration_seconds"] = round(duration, 3)
    report["rto_ok"] = duration < _RTO_LIMIT_SECONDS
    report["schedule"] = "daily"
    report["rpo_hours"] = 24
    restored = bool(report.get("restored"))
    enqueued = report.get("rebuild_enqueued")
    rebuilt = isinstance(enqueued, int) and enqueued >= 1
    vectors = bool(report.get("qdrant_restored")) or rebuilt
    sealed = (
        report.get("restic_unreadable_without_password") is True
        and report.get("repo_has_plaintext") is False
    )
    report["result"] = (
        "aprobado" if report["rto_ok"] and restored and vectors and sealed else "fallido"
    )
    destination = Path(tempfile.gettempdir()) / "engine-rag-drill-report.json"
    destination.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "aprobado" else 1


async def _exercise() -> dict[str, object]:
    uploads = Path(tempfile.mkdtemp(prefix="engine-rag-drill-uploads-"))
    repo = Path(tempfile.mkdtemp(prefix="engine-rag-drill-repo-"))
    password = Path(tempfile.mkdtemp(prefix="engine-rag-drill-secret-")) / "password"
    password.write_text(os.urandom(32).hex(), encoding="ascii")
    _alembic()
    tenant_id, user_id, document_id = await _seed(uploads)
    _create_collection()
    settings = _settings(uploads, repo, password)
    postgres = ComposePostgres(_COMPOSE, user="engine_rag", database="engine_rag")
    publisher = DockerResticRepository(
        repo,
        password,
        image=_RESTIC_IMAGE,
        protected_paths=(str(uploads),),
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        result = await _coordinator(settings, factory, postgres, publisher, uploads).run(
            Path(tempfile.mkdtemp(prefix="engine-rag-drill-staging-"))
        )
        await _insert_later_user(factory)
        later_before = await _count_email(factory, _LATER_EMAIL)
    finally:
        await engine.dispose()
    if later_before != 1:
        message = "El cambio posterior al backup no quedó registrado"
        raise RuntimeError(message)
    _empty_tree(uploads)
    _compose("down", "-v")
    _compose("up", "-d", "--wait", "--wait-timeout", "180")
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    client = create_qdrant_client(_QDRANT_URL, timeout_seconds=30)
    try:
        restored = await _restorer(factory, postgres, publisher, uploads, client).run(
            Path(tempfile.mkdtemp(prefix="engine-rag-drill-restore-")),
            snapshot_id=result.snapshot_id,
        )
        checks = await _assert_restored(factory, tenant_id, user_id, document_id, uploads)
        await asyncio.to_thread(
            httpx.delete,
            f"{_QDRANT_URL}/collections/{_COLLECTION}",
            timeout=30,
        )
        await _restorer(factory, postgres, publisher, uploads, client).run(
            Path(tempfile.mkdtemp(prefix="engine-rag-drill-rebuild-")),
            snapshot_id=result.snapshot_id,
            skip_qdrant=True,
        )
        enqueued = await enqueue_ready_reindex(
            factory,
            now=datetime.now(UTC),
            max_attempts=5,
        )
    finally:
        await client.close()
        await engine.dispose()
    return {
        "manifest_sha256": result.manifest.digest(),
        "artifacts": len(result.manifest.artifacts),
        "checksums_ok": True,
        "restored": checks,
        "qdrant_restored": restored.qdrant_restored,
        "tenants": restored.tenants,
        "rebuild_enqueued": enqueued,
        "post_backup_change_absent": checks,
        "rpo_ok": checks,
        "restic_unreadable_without_password": _restic_without_password(repo),
        "repo_has_plaintext": _repo_contains_marker(repo),
    }


def _settings(uploads: Path, repo: Path, password: Path) -> Settings:
    return Settings(
        environment=Environment.DEVELOPMENT,
        database_url=_DATABASE_URL,
        qdrant_url=_QDRANT_URL,
        file_store_path=uploads,
        restic_repository=str(repo),
        restic_password_file=password,
        pseudonym_secret="drill-pseudonym-secret-32bytes-min!!",
        jwt_secret="drill-jwt-secret-32bytes-minimum!!",
    )


def _coordinator(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    postgres: ComposePostgres,
    publisher: DockerResticRepository,
    uploads: Path,
) -> BackupCoordinator:
    return BackupCoordinator(
        factory,
        postgres,
        QdrantSnapshots(_QDRANT_URL),
        OriginalsArchive(uploads),
        publisher,
        lock_seconds=settings.backup_lock_seconds,
        application_version="0.1.0",
        revision="drill",
        public_config=public_config(settings),
    )


def _restorer(
    factory: async_sessionmaker[AsyncSession],
    postgres: ComposePostgres,
    publisher: DockerResticRepository,
    uploads: Path,
    client: object,
) -> RestoreCoordinator:
    from qdrant_client import AsyncQdrantClient

    if not isinstance(client, AsyncQdrantClient):
        message = "El cliente de Qdrant del simulacro no es válido"
        raise RuntimeError(message)
    return RestoreCoordinator(
        factory,
        postgres,
        QdrantSnapshots(_QDRANT_URL),
        OriginalsArchive(uploads),
        publisher,
        pseudonym_secret="drill-pseudonym-secret-32bytes-min!!",
        vectors=QdrantTenantPoints(client),
        files=LocalFileStore(uploads),
    )


def _compose(*args: str) -> None:
    completed = subprocess.run([*_COMPOSE, *args], check=False)
    if completed.returncode != 0:
        message = "El simulacro no pudo controlar Compose"
        raise RuntimeError(message)


def _alembic() -> None:
    env = os.environ.copy()
    env["RAG_DATABASE_URL"] = _DATABASE_URL
    completed = subprocess.run(
        ["uv", "run", "--project", "backend", "alembic", "upgrade", "head"],
        cwd=repository_root(),
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        message = "La migración del simulacro falló"
        raise RuntimeError(message)


async def _seed(uploads: Path) -> tuple[UUID, UUID, UUID]:
    now = datetime.now(UTC)
    tenant_id = uuid4()
    user_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    conversation_id = uuid4()
    key = f"{tenant_id}/{document_id}/{version_id}"
    target = uploads / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fixture")
    engine = create_database_engine(_DATABASE_URL)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                TenantModel(
                    id=tenant_id,
                    name="Fixture",
                    slug="fixture",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                UserModel(
                    id=user_id,
                    email="fixture@example.com",
                    password_hash="!",
                    status="active",
                    system_role=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                MembershipModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role="tenant_admin",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                DocumentModel(
                    id=document_id,
                    tenant_id=tenant_id,
                    display_name="politica-vacaciones.md",
                    status="ready",
                    current_version_id=version_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                DocumentVersionModel(
                    id=version_id,
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_number=1,
                    status="ready",
                    mime_type="text/markdown",
                    byte_size=7,
                    content_sha256="a" * 64,
                    storage_key=key,
                    pipeline_version="drill",
                    chunk_count=1,
                    progress=100,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                ConversationModel(
                    id=conversation_id,
                    tenant_id=tenant_id,
                    owner_kind="user",
                    owner_id=user_id,
                    title="Conversacion",
                    history_scope="visual",
                    created_at=now,
                    updated_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
            await session.flush()
            session.add(
                MessageModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    request_id=uuid4(),
                    role="assistant",
                    content=_MARKER,
                    citations=[{"document": "politica-vacaciones.md", "page": 1}],
                    abstained=False,
                    pipeline_version="drill",
                    created_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    return tenant_id, user_id, document_id


def _create_collection() -> None:
    response = httpx.put(
        f"{_QDRANT_URL}/collections/{_COLLECTION}",
        json={"vectors": {"size": 4, "distance": "Cosine"}},
        timeout=30,
    )
    response.raise_for_status()


async def _insert_later_user(factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            UserModel(
                id=uuid4(),
                email=_LATER_EMAIL,
                password_hash="!",
                status="active",
                system_role=None,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _count_email(factory: async_sessionmaker[AsyncSession], email: str) -> int:
    async with factory() as session:
        found = await session.scalar(
            select(func.count()).select_from(UserModel).where(UserModel.email == email)
        )
    return int(found or 0)


async def _assert_restored(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    user_id: UUID,
    document_id: UUID,
    uploads: Path,
) -> bool:
    async with factory() as session:
        tenant = await session.get(TenantModel, tenant_id)
        user = await session.get(UserModel, user_id)
        document = await session.get(DocumentModel, document_id)
        later = await session.scalar(
            select(func.count()).select_from(UserModel).where(UserModel.email == _LATER_EMAIL)
        )
        citation = await session.scalar(
            select(func.count()).select_from(MessageModel).where(MessageModel.content == _MARKER)
        )
        version = await session.scalar(
            select(DocumentVersionModel.storage_key).where(
                DocumentVersionModel.document_id == document_id
            )
        )
    original = isinstance(version, str) and (uploads / version).is_file()
    return bool(
        tenant is not None
        and user is not None
        and document is not None
        and later == 0
        and citation == 1
        and original
    )


def _empty_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()


def _restic_without_password(repo: Path) -> bool:
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo}:/repo",
            "-e",
            "RESTIC_REPOSITORY=/repo",
            _RESTIC_IMAGE,
            "snapshots",
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode != 0


def _repo_contains_marker(repo: Path) -> bool:
    needle = _MARKER.encode("utf-8")
    return any(path.is_file() and needle in path.read_bytes() for path in repo.rglob("*"))


if __name__ == "__main__":
    raise SystemExit(main())
