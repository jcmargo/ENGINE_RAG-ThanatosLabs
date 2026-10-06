"""Corpus de referencia con el modelo real. Fuera de `uv run pytest`."""

import asyncio
import math
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.documents.chunking import chunk_document, document_content_hash
from app.documents.parsers.registry import MARKDOWN_MIME, parse_document
from app.domain.entities import AuthPrincipal, RetrievableVersion, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.service import RetrievalService
from app.retrieval.vectors import VectorHit, VectorQuery, VectorRecord, point_id

_CACHE = Path(__file__).resolve().parents[3] / "models" / "embeddings"
_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"
_VACATION = "¿Cuántos días de descanso anual tengo?"
_OUT_OF_DOMAIN = "¿Cuál es la capital de Mongolia?"
_THRESHOLD = 0.857
_RUNS = 20


class _Catalog:
    def __init__(self, versions: dict[UUID, RetrievableVersion]) -> None:
        self._versions = versions

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        ready = {row.document_id for row in self._versions.values()}
        return frozenset(document_id for document_id in document_ids if document_id in ready)

    async def find_retrievable_versions(
        self, version_ids: Sequence[UUID]
    ) -> tuple[RetrievableVersion, ...]:
        return tuple(
            self._versions[version_id]
            for version_id in dict.fromkeys(version_ids)
            if version_id in self._versions
        )


class _MemoryStore:
    """Índice denso de la prueba. No abre Qdrant."""

    def __init__(self, records: Sequence[VectorRecord]) -> None:
        self._records = tuple(records)

    async def search(self, context: TenantContext, query: VectorQuery) -> tuple[VectorHit, ...]:
        allowed = set(query.document_ids)
        scored = [
            (_cosine(query.vector, record.vector), record)
            for record in self._records
            if record.tenant_id == context.tenant_id
            and record.is_active
            and (not allowed or record.document_id in allowed)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return tuple(_hit(score, record) for score, record in scored[: query.limit])


def _context() -> TenantContext:
    tenant = uuid4()
    return TenantContext(
        tenant_id=tenant,
        principal=AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant,
            role=None,
            scopes=(ApiKeyScope.QUERY,),
        ),
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(item * other for item, other in zip(left, right, strict=True))


def _hit(score: float, record: VectorRecord) -> VectorHit:
    return VectorHit(
        point_id=point_id(
            tenant_id=record.tenant_id,
            document_id=record.document_id,
            document_version_id=record.document_version_id,
            chunk_id=record.chunk_id,
        ),
        score=score,
        tenant_id=record.tenant_id,
        document_id=record.document_id,
        document_version_id=record.document_version_id,
        chunk_id=record.chunk_id,
        source=record.source,
        page_start=record.page_start,
        page_end=record.page_end,
        section=record.section,
        position=record.position,
        text=record.text,
        parser_version=record.parser_version,
        chunker_version=record.chunker_version,
        embedding_version=record.embedding_version,
        is_active=record.is_active,
    )


@pytest.fixture(scope="module")
def corpus_retrieval() -> tuple[RetrievalService, TenantContext, _Catalog]:
    provider = SentenceTransformerEmbeddingProvider(cache_dir=_CACHE)
    counter = HuggingFaceTokenCounter(cache_dir=_CACHE)
    context = _context()
    records: list[VectorRecord] = []
    versions: dict[UUID, RetrievableVersion] = {}
    for path in sorted(_SAMPLE.glob("*.md")):
        content = path.read_bytes()
        extracted = parse_document(path.name, content, MARKDOWN_MIME)
        chunks = chunk_document(
            extracted,
            document_hash=document_content_hash(content),
            document_version="sample-v1",
            counter=counter,
        )
        vectors = provider.embed_documents([chunk.text for chunk in chunks])
        document_id = uuid4()
        version_id = uuid4()
        versions[version_id] = RetrievableVersion(
            document_id=document_id,
            version_id=version_id,
            display_name=path.name,
        )
        for chunk, vector in zip(chunks, vectors, strict=True):
            records.append(
                VectorRecord(
                    tenant_id=context.tenant_id,
                    document_id=document_id,
                    document_version_id=version_id,
                    chunk_id=chunk.id,
                    source=path.name,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section=chunk.section,
                    position=chunk.position,
                    text=chunk.text,
                    parser_version=extracted.parser_version,
                    chunker_version=chunk.chunker_version,
                    embedding_version=provider.model_version,
                    vector=tuple(vector),
                    is_active=True,
                )
            )
    if not records:
        message = "El corpus de referencia no produjo fragmentos"
        raise RuntimeError(message)
    service = RetrievalService(
        provider,
        _MemoryStore(records),
        min_retrieval_score=_THRESHOLD,
        index_version="v1",
    )
    return service, context, _Catalog(versions)


@pytest.mark.model
def test_vacation_question_returns_the_expected_document_and_page(
    corpus_retrieval: tuple[RetrievalService, TenantContext, _Catalog],
) -> None:
    service, context, catalog = corpus_retrieval

    result = asyncio.run(
        service.retrieve(context, catalog, RetrievalRequest(question=_VACATION, top_k=3))
    )

    assert result.has_sufficient_evidence is True
    assert any(
        item.source == "politica-vacaciones.md" and item.page_start == 1 and item.position <= 3
        for item in result.evidence
    )
    assert "confidence" not in result.evidence[0].__dataclass_fields__


@pytest.mark.model
def test_out_of_domain_question_has_no_sufficient_evidence(
    corpus_retrieval: tuple[RetrievalService, TenantContext, _Catalog],
) -> None:
    service, context, catalog = corpus_retrieval

    result = asyncio.run(
        service.retrieve(context, catalog, RetrievalRequest(question=_OUT_OF_DOMAIN, top_k=5))
    )

    assert result.evidence == ()
    assert result.has_sufficient_evidence is False
    assert result.trace.hits_after_threshold == 0
    assert "confidence" not in result.trace.__dataclass_fields__


@pytest.mark.load
def test_reference_corpus_retrieval_p95_is_under_two_seconds(
    corpus_retrieval: tuple[RetrievalService, TenantContext, _Catalog],
) -> None:
    service, context, catalog = corpus_retrieval

    async def _measure() -> float:
        await service.retrieve(context, catalog, RetrievalRequest(question=_VACATION, top_k=5))
        samples: list[float] = []
        for _index in range(_RUNS):
            started = time.perf_counter()
            await service.retrieve(context, catalog, RetrievalRequest(question=_VACATION, top_k=5))
            samples.append(time.perf_counter() - started)
        ordered = sorted(samples)
        return ordered[math.ceil(0.95 * len(ordered)) - 1]

    p95 = asyncio.run(_measure())

    assert p95 < 2.0, f"p95={p95:.3f}s"
