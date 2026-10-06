"""Recuperación contra PostgreSQL y Qdrant: dos empresas, versiones y umbral."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import DeleteAlias, DeleteAliasOperation
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.documents.versions import PIPELINE_VERSION
from app.domain.entities import (
    AuthPrincipal,
    Document,
    DocumentVersion,
    IngestionJob,
    TenantContext,
)
from app.domain.enums import (
    ApiKeyScope,
    DocumentVersionStatus,
    IngestionJobStatus,
    IngestionJobType,
    PrincipalType,
)
from app.domain.errors import NotFoundError
from app.embeddings.fake import FAKE_EMBEDDING_VERSION, DeterministicFakeEmbeddingProvider
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.qdrant_store import QdrantVectorStore
from app.infrastructure.vector_client import create_qdrant_client
from app.persistence.repositories.documents import SqlAlchemyDocumentRepository
from app.persistence.unit_of_work import UnitOfWork
from app.retrieval.contracts import RetrievalRequest, RetrievalResult
from app.retrieval.service import RetrievalService
from app.retrieval.vectors import VectorRecord, collection_name
from tests.factories.identity import make_tenant
from tests.integration.db_helpers import (
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
)

pytestmark = pytest.mark.integration

RETRIEVAL_DB = "engine_rag_retrieval_test"
_REVISION = "spec11-test"
_ALIAS = "engine_rag_spec11_test"
_DIMENSION = 8
_PROVIDER = DeterministicFakeEmbeddingProvider(dimension=_DIMENSION)
_FACT = "18 dias habiles de vacaciones"
_EXTRA = "otro documento listo de la misma empresa"
_SOURCE = "politica-vacaciones.md"
_NOT_FOUND = "Documento no encontrado"


@dataclass(frozen=True)
class _Corpus:
    tenant_a: UUID
    tenant_b: UUID
    current_document: UUID
    current_version: UUID
    old_version: UUID
    deleted_document: UUID
    extra_document: UUID
    other_document: UUID
    unrelated: str


@pytest.fixture(scope="module")
def corpus() -> Iterator[_Corpus]:
    """Base y colección solo de esta spec. No toca `engine_rag` ni otros alias."""
    preserved = asyncio.run(_prepare_index())
    asyncio.run(drop_and_create_database(RETRIEVAL_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(RETRIEVAL_DB))
    try:
        yield asyncio.run(_seed())
    finally:
        try:
            asyncio.run(drop_database(RETRIEVAL_DB))
        finally:
            asyncio.run(_finish_index(preserved))


def test_known_question_returns_source_and_page(corpus: _Corpus) -> None:
    result = asyncio.run(_retrieve(corpus, RetrievalRequest(question=_FACT, top_k=3), score=0.99))

    assert result.has_sufficient_evidence is True
    assert any(
        item.source == _SOURCE and item.page_start == 1 and item.position <= 3
        for item in result.evidence
    )
    assert all(item.document_version_id == corpus.current_version for item in result.evidence[:1])
    assert corpus.old_version not in {item.document_version_id for item in result.evidence}
    assert corpus.deleted_document not in {item.document_id for item in result.evidence}
    assert corpus.other_document not in {item.document_id for item in result.evidence}
    assert _FACT not in " ".join(str(value) for value in asdict(result.trace).values())


def test_stale_qdrant_version_and_other_tenant_do_not_survive(
    corpus: _Corpus,
) -> None:
    result = asyncio.run(_retrieve(corpus, RetrievalRequest(question=_FACT, top_k=20), score=0.0))

    versions = {item.document_version_id for item in result.evidence}
    documents = {item.document_id for item in result.evidence}
    assert corpus.current_version in versions
    assert corpus.old_version not in versions
    assert corpus.deleted_document not in documents
    assert corpus.other_document not in documents


def test_filter_from_another_tenant_is_not_found(corpus: _Corpus) -> None:
    async def _run() -> None:
        async with _runtime() as (engine, service):
            context = _context(corpus.tenant_a)
            async with UnitOfWork(create_session_factory(engine)) as unit:
                documents = SqlAlchemyDocumentRepository(unit, context)
                with pytest.raises(NotFoundError, match=r"^Documento no encontrado$") as error:
                    await service.retrieve(
                        context,
                        documents,
                        RetrievalRequest(question=_FACT, document_ids=(corpus.other_document,)),
                    )
                assert error.value.message == _NOT_FOUND
                assert "tenant" not in error.value.message.lower()
                with pytest.raises(NotFoundError, match=r"^Documento no encontrado$"):
                    await service.retrieve(
                        context,
                        documents,
                        RetrievalRequest(question=_FACT, document_ids=(corpus.deleted_document,)),
                    )

    asyncio.run(_run())


def test_own_filter_returns_only_that_document(corpus: _Corpus) -> None:
    result = asyncio.run(
        _retrieve(
            corpus,
            RetrievalRequest(question=_FACT, top_k=5, document_ids=(corpus.current_document,)),
            score=0.99,
        )
    )

    assert result.evidence
    assert {item.document_id for item in result.evidence} == {corpus.current_document}
    assert result.evidence[0].source == _SOURCE
    assert result.evidence[0].page_start == 1


def test_question_below_threshold_has_no_sufficient_evidence(
    corpus: _Corpus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.retrieval.service"):
        result = asyncio.run(
            _retrieve(corpus, RetrievalRequest(question=corpus.unrelated, top_k=5), score=0.99)
        )

    assert result.evidence == ()
    assert result.has_sufficient_evidence is False
    assert result.trace.hits_after_threshold == 0
    assert result.trace.returned == 0
    assert result.trace.embedding_version == FAKE_EMBEDDING_VERSION
    assert result.trace.index_version == _REVISION
    assert result.trace.embed_ms >= 0
    assert "confidence" not in result.trace.__dataclass_fields__
    assert corpus.unrelated not in caplog.text
    assert _FACT not in caplog.text


def test_postgres_validates_every_hit_in_one_query(corpus: _Corpus) -> None:
    captured: list[tuple[str, object]] = []

    def capture(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, context, executemany
        captured.append((statement, parameters))

    async def _run() -> None:
        async with _runtime(score=0.0) as (engine, service):
            event.listen(engine.sync_engine, "before_cursor_execute", capture)
            try:
                context = _context(corpus.tenant_a)
                async with UnitOfWork(create_session_factory(engine)) as unit:
                    documents = SqlAlchemyDocumentRepository(unit, context)
                    result = await service.retrieve(
                        context,
                        documents,
                        RetrievalRequest(question=_FACT, top_k=20),
                    )
                assert corpus.current_version in {
                    item.document_version_id for item in result.evidence
                }
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", capture)

    asyncio.run(_run())
    selects = [
        (statement, parameters)
        for statement, parameters in captured
        if "document_versions" in statement.lower()
        and statement.lstrip().lower().startswith("select")
    ]
    assert len(selects) == 1
    identifiers = _uuids(selects[0][0]) | _uuids(selects[0][1])
    assert corpus.current_version in identifiers
    assert corpus.old_version in identifiers


def _uuids(value: object) -> set[UUID]:
    found: set[UUID] = set()
    if isinstance(value, UUID):
        found.add(value)
        return found
    if isinstance(value, str):
        try:
            found.add(UUID(value))
        except ValueError:
            return found
        return found
    if isinstance(value, dict):
        for item in value.values():
            found.update(_uuids(item))
        return found
    if isinstance(value, list | tuple):
        for item in value:
            found.update(_uuids(item))
    return found


async def _retrieve(
    corpus: _Corpus,
    request: RetrievalRequest,
    *,
    score: float,
) -> RetrievalResult:
    async with _runtime(score=score) as (engine, service):
        context = _context(corpus.tenant_a)
        async with UnitOfWork(create_session_factory(engine)) as unit:
            documents = SqlAlchemyDocumentRepository(unit, context)
            return await service.retrieve(context, documents, request)


@asynccontextmanager
async def _runtime(score: float = 0.99) -> AsyncIterator[tuple[AsyncEngine, RetrievalService]]:
    engine = create_database_engine(database_url_for(RETRIEVAL_DB))
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    service = RetrievalService(
        _PROVIDER,
        _store(client),
        min_retrieval_score=score,
        index_version=_REVISION,
    )
    try:
        yield engine, service
    finally:
        await client.close()
        await engine.dispose()


def _context(tenant_id: UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        principal=AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant_id,
            role=None,
            scopes=(ApiKeyScope.QUERY,),
        ),
    )


async def _seed() -> _Corpus:
    fact_vector = tuple(_PROVIDER.embed_query(_FACT))
    extra_vector = tuple(_PROVIDER.embed_query(_EXTRA))
    if _cosine(fact_vector, extra_vector) >= 0.99:
        message = "El texto extra se parece demasiado al hecho de prueba"
        raise RuntimeError(message)
    unrelated = _unrelated(fact_vector)
    now = datetime.now(UTC)
    tenant_a = make_tenant(slug="retrieval-alfa", name="Retrieval Alfa")
    tenant_b = make_tenant(slug="retrieval-beta", name="Retrieval Beta")
    engine = create_database_engine(database_url_for(RETRIEVAL_DB))
    factory = create_session_factory(engine)
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    store = _store(client)
    try:
        async with UnitOfWork(factory) as unit:
            await unit.tenants.add(tenant_a)
            await unit.tenants.add(tenant_b)
            await unit.commit()
        context_a = _context(tenant_a.id)
        context_b = _context(tenant_b.id)
        current_document = uuid4()
        current_version = uuid4()
        old_version = uuid4()
        deleted_document = uuid4()
        deleted_version = uuid4()
        extra_document = uuid4()
        extra_version = uuid4()
        other_document = uuid4()
        other_version = uuid4()
        async with UnitOfWork(factory) as unit:
            repo_a = SqlAlchemyDocumentRepository(unit, context_a)
            await _add_ready(
                repo_a,
                context_a,
                document_id=current_document,
                version_id=old_version,
                name=_SOURCE,
                content="version vieja",
                now=now,
            )
            await repo_a.add_version(
                document_id=current_document,
                display_name=_SOURCE,
                version=_document_version(
                    context_a,
                    document_id=current_document,
                    version_id=current_version,
                    version_number=2,
                    content=_FACT,
                    now=now,
                ),
                job=_job(context_a, current_document, current_version, now),
                updated_at=now,
            )
            await repo_a.mark_ready(
                document_id=current_document,
                version_id=current_version,
                chunk_count=1,
                updated_at=now,
            )
            await _add_ready(
                repo_a,
                context_a,
                document_id=deleted_document,
                version_id=deleted_version,
                name="borrador.md",
                content="documento eliminado",
                now=now,
            )
            await repo_a.mark_document_deleted(deleted_document, now)
            await _add_ready(
                repo_a,
                context_a,
                document_id=extra_document,
                version_id=extra_version,
                name="extra.md",
                content=_EXTRA,
                now=now,
            )
            repo_b = SqlAlchemyDocumentRepository(unit, context_b)
            await _add_ready(
                repo_b,
                context_b,
                document_id=other_document,
                version_id=other_version,
                name="ajena.md",
                content=_FACT,
                now=now,
            )
            await unit.commit()
        await store.ensure_index()
        await store.upsert(
            context_a,
            (
                _record(
                    context_a,
                    document_id=current_document,
                    version_id=old_version,
                    chunk_id="vieja",
                    text=_FACT,
                    vector=fact_vector,
                    page_start=9,
                ),
                _record(
                    context_a,
                    document_id=current_document,
                    version_id=current_version,
                    chunk_id="actual",
                    text=_FACT,
                    vector=fact_vector,
                    page_start=1,
                    section="Vacaciones",
                ),
                _record(
                    context_a,
                    document_id=deleted_document,
                    version_id=deleted_version,
                    chunk_id="borrado",
                    text=_FACT,
                    vector=fact_vector,
                    page_start=4,
                ),
                _record(
                    context_a,
                    document_id=extra_document,
                    version_id=extra_version,
                    chunk_id="extra",
                    text=_EXTRA,
                    vector=extra_vector,
                    page_start=2,
                    source="extra.md",
                ),
            ),
        )
        await store.upsert(
            context_b,
            (
                _record(
                    context_b,
                    document_id=other_document,
                    version_id=other_version,
                    chunk_id="ajena",
                    text=_FACT,
                    vector=fact_vector,
                    page_start=7,
                    source="ajena.md",
                ),
            ),
        )
    finally:
        await client.close()
        await engine.dispose()
    return _Corpus(
        tenant_a=tenant_a.id,
        tenant_b=tenant_b.id,
        current_document=current_document,
        current_version=current_version,
        old_version=old_version,
        deleted_document=deleted_document,
        extra_document=extra_document,
        other_document=other_document,
        unrelated=unrelated,
    )


async def _add_ready(
    repository: SqlAlchemyDocumentRepository,
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    name: str,
    content: str,
    now: datetime,
) -> None:
    await repository.add_document(
        _document(context, document_id, version_id, name, now),
        _document_version(
            context,
            document_id=document_id,
            version_id=version_id,
            version_number=1,
            content=content,
            now=now,
        ),
        _job(context, document_id, version_id, now),
    )
    await repository.mark_ready(
        document_id=document_id,
        version_id=version_id,
        chunk_count=1,
        updated_at=now,
    )


def _document(
    context: TenantContext,
    document_id: UUID,
    version_id: UUID,
    name: str,
    now: datetime,
) -> Document:
    return Document(
        id=document_id,
        tenant_id=context.tenant_id,
        display_name=name,
        status=DocumentVersionStatus.QUEUED,
        current_version_id=version_id,
        created_at=now,
        updated_at=now,
    )


def _document_version(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    version_number: int,
    content: str,
    now: datetime,
) -> DocumentVersion:
    encoded = content.encode()
    return DocumentVersion(
        id=version_id,
        tenant_id=context.tenant_id,
        document_id=document_id,
        version_number=version_number,
        status=DocumentVersionStatus.QUEUED,
        mime_type="text/markdown",
        byte_size=max(len(encoded), 1),
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        storage_key=f"{context.tenant_id}/{document_id}/{version_id}",
        pipeline_version=PIPELINE_VERSION,
        chunk_count=0,
        created_at=now,
        updated_at=now,
    )


def _job(
    context: TenantContext,
    document_id: UUID,
    version_id: UUID,
    now: datetime,
) -> IngestionJob:
    return IngestionJob(
        id=uuid4(),
        tenant_id=context.tenant_id,
        document_id=document_id,
        document_version_id=version_id,
        job_type=IngestionJobType.INGEST,
        status=IngestionJobStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


def _record(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    chunk_id: str,
    text: str,
    vector: tuple[float, ...],
    page_start: int,
    source: str = "nombre-en-qdrant.md",
    section: str | None = None,
) -> VectorRecord:
    return VectorRecord(
        tenant_id=context.tenant_id,
        document_id=document_id,
        document_version_id=version_id,
        chunk_id=chunk_id,
        source=source,
        page_start=page_start,
        page_end=page_start,
        section=section,
        position=0,
        text=text,
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version=FAKE_EMBEDDING_VERSION,
        vector=vector,
        is_active=True,
    )


def _unrelated(fact_vector: Sequence[float]) -> str:
    for index in range(20):
        text = f"pregunta fuera de dominio {index}"
        if _cosine(fact_vector, tuple(_PROVIDER.embed_query(text))) < 0.99:
            return text
    message = "No hubo una pregunta bajo el umbral de prueba"
    raise RuntimeError(message)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(item * other for item, other in zip(left, right, strict=True))


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
