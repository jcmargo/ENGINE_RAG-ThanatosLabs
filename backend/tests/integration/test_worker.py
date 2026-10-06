"""Cola, worker y reconciliación contra PostgreSQL y Qdrant desechables."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import DeleteAlias, DeleteAliasOperation
from sqlalchemy import text

from app.config import Environment, LogLevel, Settings, get_settings
from app.documents.chunking import WordTokenCounter
from app.documents.versions import PIPELINE_VERSION, REINDEX_PIPELINE_VERSION
from app.domain.enums import SystemRole
from app.embeddings.fake import FAKE_EMBEDDING_VERSION, DeterministicFakeEmbeddingProvider
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.passwords import Argon2Policy, PasswordHasher
from app.infrastructure.qdrant_store import QdrantVectorStore
from app.infrastructure.vector_client import create_qdrant_client
from app.ingestion.context import context_for_job
from app.ingestion.failures import StopAfterUpsert
from app.ingestion.pipeline import PipelineHooks
from app.ingestion.queue import ClaimedJob
from app.main import create_app
from app.persistence.job_queue import SqlAlchemyIngestionQueue
from app.persistence.unit_of_work import UnitOfWork
from app.retrieval.vectors import VectorHit, VectorQuery, collection_name
from app.worker.runtime import IngestionWorker, build_worker
from tests.factories.identity import make_user
from tests.integration.db_helpers import (
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    truncate_identity_tables,
)

pytestmark = pytest.mark.integration

WORKER_DB = "engine_rag_worker_test"
_REVISION = "spec10-test"
_ALIAS = "engine_rag_spec10_test"
_DIMENSION = 8
_PASSWORD = "worker-password-not-logged"
_MARKER = "marcador-secreto-worker"
_HASHER = PasswordHasher(
    Argon2Policy(time_cost=2, memory_cost=8192, parallelism=1, hash_len=16, salt_len=16)
)
_PROVIDER = DeterministicFakeEmbeddingProvider(dimension=_DIMENSION)


@pytest.fixture(scope="module")
def worker_database() -> Iterator[None]:
    """Base y colección solo de esta spec. No toca `engine_rag` ni otros alias."""
    preserved = asyncio.run(_prepare_index())
    asyncio.run(drop_and_create_database(WORKER_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(WORKER_DB))
    try:
        yield
    finally:
        try:
            asyncio.run(drop_database(WORKER_DB))
        finally:
            asyncio.run(_finish_index(preserved))


def test_worker_promotes_markdown_to_ready_with_active_points(
    worker_database: None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = f"# Nota\n\n{_MARKER} listo para citar".encode()
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="ingesta", email="admin.ingesta@example.com")
        created = _upload(client, admin, "nota.md", content, "text/plain")
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "queued"
        assert body["progress"] == 0
        assert body["chunk_count"] == 0
        document_id = UUID(str(body["id"]))
        with caplog.at_level(logging.INFO):
            processed = asyncio.run(_drain(_worker(tmp_path)))
        assert processed == 1
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.status_code == 200, shown.text
        ready = shown.json()
        assert ready["status"] == "ready"
        assert ready["progress"] == 100
        assert ready["chunk_count"] > 0
        assert _MARKER not in caplog.text
    tenant_id = asyncio.run(_tenant_id(document_id))
    hits = asyncio.run(_search(tenant_id, document_id, _MARKER))
    assert len(hits) == ready["chunk_count"]
    assert all(hit.document_version_id == UUID(str(ready["version_id"])) for hit in hits)
    assert asyncio.run(_count(tenant_id)) == ready["chunk_count"]


def test_two_claims_cannot_lease_the_same_job(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="carrera", email="admin.carrera@example.com")
        created = _upload(client, admin, "nota.md", b"# Uno\n\ntexto", "text/plain")
        assert created.status_code == 201, created.text
        winners = asyncio.run(_race_claims())
    assert winners == 1


def test_expired_lease_is_recovered_without_counting_a_crash_as_attempt(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="reanuda", email="admin.reanuda@example.com")
        created = _upload(client, admin, "nota.md", b"# Caida\n\nse reanuda", "text/plain")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        claimed = asyncio.run(_claim("owner-a"))
        assert claimed is not None
        asyncio.run(_expire_leases())
        assert asyncio.run(_complete(claimed.id, "owner-a")) is False
        processed = asyncio.run(_drain(_worker(tmp_path)))
        assert processed == 1
        assert asyncio.run(_complete(claimed.id, "owner-a")) is False
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.status_code == 200, shown.text
        assert shown.json()["status"] == "ready"
        assert shown.json()["progress"] == 100
    job = asyncio.run(_job(claimed.id))
    assert job["status"] == "succeeded"
    assert job["attempts"] == 0


def test_repeating_a_finished_job_does_not_duplicate_points_or_versions(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="repite", email="admin.repite@example.com")
        created = _upload(client, admin, "nota.md", b"# Igual\n\nno duplicar", "text/plain")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        before_versions = asyncio.run(_version_count(document_id))
        tenant_id = asyncio.run(_tenant_id(document_id))
        before_points = asyncio.run(_count(tenant_id))
        assert asyncio.run(_usage_count(tenant_id, "upload")) == 1
        assert asyncio.run(_usage_count(tenant_id, "chunks")) == 1
        asyncio.run(_insert_job(document_id, "ingest"))
        asyncio.run(_drain(_worker(tmp_path)))
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.json()["status"] == "ready"
        assert shown.json()["version"] == 1
        statuses = asyncio.run(_job_statuses(document_id))
        assert statuses == ["succeeded", "succeeded"]
    assert asyncio.run(_version_count(document_id)) == before_versions
    assert asyncio.run(_count(tenant_id)) == before_points
    assert asyncio.run(_usage_count(tenant_id, "upload")) == 1
    assert asyncio.run(_usage_count(tenant_id, "chunks")) == 1


def test_new_version_stays_inactive_until_activation(
    worker_database: None,
    tmp_path: Path,
) -> None:
    first = b"# Uno\n\nalfa-version-servida"
    second = b"# Dos\n\nbeta-version-nueva"
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="versiones", email="admin.versiones@example.com")
        created = _upload(client, admin, "nota.md", first, "text/plain")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        replacement = client.post(
            f"/v1/documents/{document_id}/versions",
            headers=_auth(admin),
            files={"file": ("nota.md", second, "text/plain")},
        )
        assert replacement.status_code == 201, replacement.text
        assert replacement.json()["version"] == 2
        assert replacement.json()["status"] == "queued"
        stopped = asyncio.run(_drain(_worker(tmp_path, hooks=PipelineHooks(after_upsert=_stop))))
        assert stopped == 1
        tenant_id = asyncio.run(_tenant_id(document_id))
        visible = asyncio.run(_search(tenant_id, document_id, "alfa-version-servida"))
        assert visible
        assert all("alfa-version-servida" in hit.text for hit in visible)
        assert all("beta-version-nueva" not in hit.text for hit in visible)
        asyncio.run(_expire_leases())
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.json()["status"] == "ready"
        assert shown.json()["version"] == 2
        assert shown.json()["progress"] == 100
        current = asyncio.run(_search(tenant_id, document_id, "beta-version-nueva"))
        assert len(current) == shown.json()["chunk_count"]
        assert all(hit.text.find("beta-version-nueva") >= 0 for hit in current)
        assert all("alfa-version-servida" not in hit.text for hit in current)
        assert asyncio.run(_count(tenant_id)) == len(visible) + len(current)


def test_corrupt_pdf_is_dead_and_not_retried(
    worker_database: None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="corrupto", email="admin.corrupto@example.com")
        created = _upload(client, admin, "nota.pdf", _pdf_bytes(("Texto",)), "application/pdf")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        version_id = UUID(str(created.json()["version_id"]))
        asyncio.run(_corrupt(tmp_path, version_id))
        with caplog.at_level(logging.INFO):
            assert asyncio.run(_drain(_worker(tmp_path))) == 1
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.status_code == 200, shown.text
        assert shown.json()["status"] == "failed"
        assert shown.json()["public_error_code"] == "document_parse_error"
        assert "No se pudo leer" not in caplog.text
        assert asyncio.run(_drain(_worker(tmp_path))) == 0
    job = asyncio.run(_only_job(document_id))
    assert job["status"] == "dead"
    assert job["error_code"] == "document_parse_error"
    attempts = job["attempts"]
    assert isinstance(attempts, int)
    assert attempts >= 1
    assert asyncio.run(_only_job(document_id))["attempts"] == attempts


def test_crash_after_upsert_is_reconciled_without_new_points(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="crash", email="admin.crash@example.com")
        created = _upload(client, admin, "nota.md", b"# Corte\n\ndespues del upsert", "text/plain")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        assert asyncio.run(_drain(_worker(tmp_path, hooks=PipelineHooks(after_upsert=_stop)))) == 1
        tenant_id = asyncio.run(_tenant_id(document_id))
        written = asyncio.run(_count(tenant_id))
        assert written > 0
        assert asyncio.run(_indexed(document_id)) is False
        asyncio.run(_expire_leases())
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.json()["status"] == "ready"
        assert shown.json()["progress"] == 100
        assert shown.json()["chunk_count"] == written
    assert asyncio.run(_count(tenant_id)) == written


def test_delete_removes_one_tenant_without_touching_the_other(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin_a = _admin_token(client, slug="borra-a", email="admin.borra.a@example.com")
        created_b = _create_tenant(
            client,
            _provider_token(client),
            name="Empresa B",
            slug="borra-b",
            email="admin.borra.b@example.com",
        )
        _activate(client, str(created_b["activation_token"]))
        admin_b = _login(client, "admin.borra.b@example.com", slug="borra-b")
        own = _upload(client, admin_a, "propia.md", b"# A\n\nsolo empresa a", "text/plain")
        foreign = _upload(client, admin_b, "ajena.md", b"# B\n\nsolo empresa b", "text/plain")
        assert own.status_code == 201, own.text
        assert foreign.status_code == 201, foreign.text
        document_a = UUID(str(own.json()["id"]))
        document_b = UUID(str(foreign.json()["id"]))
        assert asyncio.run(_drain(_worker(tmp_path))) == 2
        tenant_a = asyncio.run(_tenant_id(document_a))
        tenant_b = asyncio.run(_tenant_id(document_b))
        points_b = asyncio.run(_count(tenant_b))
        key_a = asyncio.run(_storage_key(document_a))
        key_b = asyncio.run(_storage_key(document_b))
        assert (tmp_path / key_a).is_file()
        assert (tmp_path / key_b).is_file()
        deleted = client.delete(f"/v1/documents/{document_a}", headers=_auth(admin_a))
        assert deleted.status_code == 202, deleted.text
        assert deleted.json()["status"] == "deleting"
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        missing = client.get(f"/v1/documents/{document_a}", headers=_auth(admin_a))
        assert missing.status_code == 404
        kept = client.get(f"/v1/documents/{document_b}", headers=_auth(admin_b))
        assert kept.status_code == 200, kept.text
        assert kept.json()["status"] == "ready"
    assert not (tmp_path / key_a).exists()
    assert (tmp_path / key_b).is_file()
    assert asyncio.run(_count(tenant_a)) == 0
    assert asyncio.run(_count(tenant_b)) == points_b
    kept_hits = asyncio.run(_search(tenant_b, document_b, "solo empresa b"))
    assert kept_hits


def test_reindex_switches_only_after_the_replacement_is_ready(
    worker_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="reindex", email="admin.reindex@example.com")
        created = _upload(client, admin, "nota.md", b"# Base\n\ntexto reindexable", "text/plain")
        assert created.status_code == 201, created.text
        document_id = UUID(str(created.json()["id"]))
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        before = asyncio.run(_count(asyncio.run(_tenant_id(document_id))))
        asyncio.run(_insert_job(document_id, "reindex"))
        assert asyncio.run(_drain(_worker(tmp_path))) == 1
        shown = client.get(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert shown.json()["status"] == "ready"
        assert shown.json()["version"] == 2
        assert shown.json()["progress"] == 100
        tenant_id = asyncio.run(_tenant_id(document_id))
        hits = asyncio.run(_search(tenant_id, document_id, "texto reindexable"))
        assert len(hits) == shown.json()["chunk_count"]
        assert {hit.document_version_id for hit in hits} == {UUID(str(shown.json()["version_id"]))}
    rows = asyncio.run(_version_rows(document_id))
    assert [row["status"] for row in rows] == ["superseded", "ready"]
    assert rows[0]["pipeline_version"] == PIPELINE_VERSION
    assert rows[1]["pipeline_version"] == REINDEX_PIPELINE_VERSION
    assert asyncio.run(_count(tenant_id)) == before + shown.json()["chunk_count"]
    assert asyncio.run(_stored_bytes(document_id)) == rows[1]["byte_size"]


def _worker(
    root: Path,
    *,
    hooks: PipelineHooks | None = None,
    owner: str | None = None,
) -> IngestionWorker:
    return build_worker(
        _settings(root),
        embeddings=_PROVIDER,
        counter=WordTokenCounter(),
        index_revision=_REVISION,
        vector_alias=_ALIAS,
        hooks=hooks,
        owner=owner,
    )


async def _drain(worker: IngestionWorker) -> int:
    try:
        return await worker.run_available()
    finally:
        await worker.aclose()


async def _stop() -> None:
    raise StopAfterUpsert


def _settings(root: Path) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": database_url_for(WORKER_DB),
        "qdrant_url": get_settings().qdrant_url,
        "jwt_secret": "integration-test-jwt-secret-32b!!",
        "file_store_path": root,
        "worker_lease_seconds": 60,
        "worker_heartbeat_seconds": 15,
        "worker_max_attempts": 5,
        "worker_poll_seconds": 0.01,
        "embed_batch_size": 8,
        "index_revision": "v1",
        "vector_alias": "engine_rag_active",
    }
    return Settings(**values)  # type: ignore[arg-type]


def _client(root: Path) -> TestClient:
    asyncio.run(_reset())
    return TestClient(create_app(_settings(root), password_hasher=_HASHER))


async def _reset() -> None:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    try:
        await truncate_identity_tables(factory)
        provider = make_user(
            email="provider.worker@example.com",
            password_hash=_HASHER.hash(_PASSWORD),
            system_role=SystemRole.PROVIDER_ADMIN,
        )
        async with UnitOfWork(factory) as unit:
            await unit.users.add(provider)
            await unit.commit()
    finally:
        await engine.dispose()


async def _race_claims() -> int:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    queue = SqlAlchemyIngestionQueue(factory)
    now = datetime.now(UTC)
    ttl = timedelta(seconds=60)
    try:
        first, second = await asyncio.gather(
            queue.claim_next("owner-a", now, ttl),
            queue.claim_next("owner-b", now, ttl),
        )
    finally:
        await engine.dispose()
    return sum(item is not None for item in (first, second))


async def _claim(owner: str) -> ClaimedJob | None:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    queue = SqlAlchemyIngestionQueue(factory)
    try:
        return await queue.claim_next(owner, datetime.now(UTC), timedelta(seconds=60))
    finally:
        await engine.dispose()


async def _complete(job_id: UUID, owner: str) -> bool:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    queue = SqlAlchemyIngestionQueue(factory)
    try:
        return await queue.complete(job_id, owner, datetime.now(UTC))
    finally:
        await engine.dispose()


async def _expire_leases() -> None:
    await _execute(
        "UPDATE ingestion_jobs SET lease_expires_at = now() - interval '5 minutes' "
        "WHERE status = 'leased'"
    )


async def _insert_job(document_id: UUID, job_type: str) -> None:
    await _execute(
        """
        INSERT INTO ingestion_jobs (
            id, tenant_id, document_id, document_version_id, job_type, status,
            attempts, max_attempts, available_at, progress, created_at, updated_at
        )
        SELECT gen_random_uuid(), tenant_id, document_id, id, :job_type, 'pending',
               0, 5, now(), 0, now(), now()
        FROM document_versions
        WHERE document_id = :document_id AND status = 'ready'
        ORDER BY version_number DESC
        LIMIT 1
        """,
        {"document_id": document_id, "job_type": job_type},
    )


async def _corrupt(root: Path, version_id: UUID) -> None:
    key = await _scalar(
        "SELECT storage_key FROM document_versions WHERE id = :id",
        {"id": version_id},
    )
    assert isinstance(key, str)
    payload = b"not-a-pdf"
    (root / key).write_bytes(payload)
    await _execute(
        """
        UPDATE document_versions
        SET content_sha256 = :digest, byte_size = :size
        WHERE id = :id
        """,
        {
            "digest": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "id": version_id,
        },
    )


async def _tenant_id(document_id: UUID) -> UUID:
    value = await _scalar(
        "SELECT tenant_id FROM documents WHERE id = :id",
        {"id": document_id},
    )
    assert isinstance(value, UUID)
    return value


async def _version_count(document_id: UUID) -> int:
    value = await _scalar(
        "SELECT count(*) FROM document_versions WHERE document_id = :id",
        {"id": document_id},
    )
    return int(value or 0)


async def _indexed(document_id: UUID) -> bool:
    value = await _scalar(
        """
        SELECT indexed_at IS NOT NULL
        FROM document_versions
        WHERE document_id = :id
        ORDER BY version_number
        LIMIT 1
        """,
        {"id": document_id},
    )
    return bool(value)


async def _job(job_id: UUID) -> dict[str, object]:
    rows = await _rows(
        "SELECT status, attempts, error_code FROM ingestion_jobs WHERE id = :id",
        {"id": job_id},
    )
    assert len(rows) == 1
    return rows[0]


async def _job_statuses(document_id: UUID) -> list[object]:
    rows = await _rows(
        """
        SELECT status FROM ingestion_jobs
        WHERE document_id = :id
        ORDER BY created_at, id
        """,
        {"id": document_id},
    )
    return [row["status"] for row in rows]


async def _only_job(document_id: UUID) -> dict[str, object]:
    rows = await _rows(
        """
        SELECT status, attempts, error_code
        FROM ingestion_jobs
        WHERE document_id = :id
        """,
        {"id": document_id},
    )
    assert len(rows) == 1
    return rows[0]


async def _storage_key(document_id: UUID) -> str:
    value = await _scalar(
        """
        SELECT storage_key FROM document_versions
        WHERE document_id = :id
        ORDER BY version_number
        LIMIT 1
        """,
        {"id": document_id},
    )
    assert isinstance(value, str)
    return value


async def _version_rows(document_id: UUID) -> list[dict[str, object]]:
    return await _rows(
        """
        SELECT version_number, status, pipeline_version, byte_size
        FROM document_versions
        WHERE document_id = :id
        ORDER BY version_number
        """,
        {"id": document_id},
    )


async def _stored_bytes(document_id: UUID) -> int:
    value = await _scalar(
        """
        SELECT coalesce(sum(byte_size), 0)
        FROM document_versions
        WHERE document_id = :id
          AND status NOT IN ('deleted', 'superseded')
        """,
        {"id": document_id},
    )
    return int(value or 0)


async def _execute(statement: str, params: dict[str, object] | None = None) -> None:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(text(statement), params or {})
            await session.commit()
    finally:
        await engine.dispose()


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await session.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _rows(statement: str, params: dict[str, object]) -> list[dict[str, object]]:
    engine = create_database_engine(database_url_for(WORKER_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(text(statement), params)
            return [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()


async def _search(tenant_id: UUID, document_id: UUID, query: str) -> tuple[VectorHit, ...]:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    store = _store(client)
    try:
        return await store.search(
            context_for_job(tenant_id),
            VectorQuery(
                vector=tuple(_PROVIDER.embed_query(query)),
                limit=50,
                document_ids=(document_id,),
            ),
        )
    finally:
        await client.close()


async def _usage_count(tenant_id: UUID, kind: str) -> int:
    value = await _scalar(
        """
        SELECT count(*)
        FROM usage_events
        WHERE tenant_id = :tenant_id AND kind = :kind
        """,
        {"tenant_id": tenant_id, "kind": kind},
    )
    return int(value or 0)


async def _count(tenant_id: UUID) -> int:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    store = _store(client)
    try:
        return await store.count(context_for_job(tenant_id))
    finally:
        await client.close()


def _store(client: AsyncQdrantClient) -> QdrantVectorStore:
    return QdrantVectorStore(
        client,
        embedding_model=FAKE_EMBEDDING_VERSION,
        revision=_REVISION,
        dimension=_DIMENSION,
        alias_name=_ALIAS,
    )


def _expected_collection() -> str:
    return collection_name(
        embedding_model=FAKE_EMBEDDING_VERSION,
        revision=_REVISION,
        dimension=_DIMENSION,
    )


async def _prepare_index() -> set[str]:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    try:
        await _remove_index(client)
        return await _names(client)
    finally:
        await client.close()


async def _finish_index(preserved: set[str]) -> None:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    try:
        await _remove_index(client)
        remaining = await _names(client)
    finally:
        await client.close()
    missing = preserved - remaining
    if missing:
        message = f"La prueba borró colecciones ajenas: {sorted(missing)}"
        raise AssertionError(message)
    if _expected_collection() in remaining:
        message = "La colección de prueba sigue en Qdrant"
        raise AssertionError(message)


async def _remove_index(client: AsyncQdrantClient) -> None:
    aliases = await client.get_aliases()
    if any(item.alias_name == _ALIAS for item in aliases.aliases):
        await client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=_ALIAS))
            ]
        )
    collection = _expected_collection()
    if await client.collection_exists(collection):
        await client.delete_collection(collection)


async def _names(client: AsyncQdrantClient) -> set[str]:
    response = await client.get_collections()
    return {item.name for item in response.collections}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provider_token(client: TestClient) -> str:
    return _login(client, "provider.worker@example.com")


def _admin_token(client: TestClient, *, slug: str, email: str) -> str:
    created = _create_tenant(
        client,
        _provider_token(client),
        name=slug,
        slug=slug,
        email=email,
    )
    _activate(client, str(created["activation_token"]))
    return _login(client, email, slug=slug)


def _create_tenant(
    client: TestClient,
    provider: str,
    *,
    name: str,
    slug: str,
    email: str,
) -> dict[str, object]:
    response = client.post(
        "/v1/provider/tenants",
        headers=_auth(provider),
        json={"name": name, "slug": slug, "admin_email": email},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, object] = response.json()
    return payload


def _activate(client: TestClient, token: str) -> None:
    response = client.post("/v1/auth/activate", json={"token": token, "password": _PASSWORD})
    assert response.status_code == 204, response.text


def _login(client: TestClient, email: str, *, slug: str | None = None) -> str:
    body: dict[str, str] = {"email": email, "password": _PASSWORD}
    if slug is not None:
        body["tenant_slug"] = slug
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def _upload(
    client: TestClient,
    token: str,
    name: str,
    content: bytes,
    content_type: str,
) -> httpx.Response:
    return client.post(
        "/v1/documents",
        headers=_auth(token),
        files={"file": (name, content, content_type)},
    )


def _pdf_bytes(pages: tuple[str, ...]) -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    try:
        for page_text in pages:
            page = pdf.new_page()
            if page_text:
                page.insert_textbox(pymupdf.Rect(72, 72, 540, 770), page_text)
        return bytes(pdf.tobytes())
    finally:
        pdf.close()
