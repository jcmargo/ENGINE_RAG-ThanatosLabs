"""Preguntas conocidas con Ollama y el corpus de referencia. Fuera de la suite por defecto."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.config import get_settings
from app.documents.chunking import chunk_document, document_content_hash
from app.documents.parsers.registry import MARKDOWN_MIME, parse_document
from app.domain.entities import AuthPrincipal, RetrievableVersion, TenantContext
from app.domain.enums import PrincipalType, TenantRole
from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.generation.base import GenerationRequest, GenerationResult
from app.generation.models import PUBLIC_ABSTENTION
from app.generation.ollama import OllamaLLMProvider
from app.questions.models import QuestionRequest
from app.questions.service import QuestionService
from app.retrieval.service import RetrievalService
from app.retrieval.vectors import VectorHit, VectorQuery, VectorRecord, point_id

pytestmark = pytest.mark.ollama

_CACHE = Path(__file__).resolve().parents[3] / "models" / "embeddings"
_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"
_THRESHOLD = 0.857
_KNOWN = (
    (
        "¿Cuántos días de descanso anual tengo?",
        "18 días hábiles",
        "politica-vacaciones.md",
    ),
    (
        "¿Cuál es el máximo reembolsable de taxi?",
        "40 USD",
        "politica-gastos.md",
    ),
    (
        "¿En cuánto tiempo se atienden los incidentes críticos?",
        "15 minutos",
        "procedimiento-soporte.md",
    ),
)
_OUT_OF_DOMAIN = "¿Cuál es la capital de Mongolia?"


class _Catalog:
    def __init__(self, versions: dict[UUID, RetrievableVersion]) -> None:
        self._versions = versions

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        ready = {row.document_id for row in self._versions.values()}
        return frozenset(document_id for document_id in document_ids if document_id in ready)

    async def find_retrievable_versions(
        self,
        version_ids: Sequence[UUID],
    ) -> tuple[RetrievableVersion, ...]:
        return tuple(
            self._versions[version_id]
            for version_id in dict.fromkeys(version_ids)
            if version_id in self._versions
        )


class _MemoryStore:
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


class _Opener:
    def __init__(self, catalog: _Catalog) -> None:
        self._catalog = catalog

    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[_Catalog]:
        del context
        yield self._catalog


class _CountingLLM:
    def __init__(self, inner: OllamaLLMProvider) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def model_version(self) -> str:
        return self._inner.model_version

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        return self._inner.generate(request)


def _context() -> TenantContext:
    tenant = uuid4()
    return TenantContext(
        tenant_id=tenant,
        principal=AuthPrincipal(
            type=PrincipalType.TENANT_ADMIN,
            user_id=uuid4(),
            tenant_id=tenant,
            role=TenantRole.TENANT_ADMIN,
            scopes=(),
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
def asked_corpus() -> tuple[QuestionService, TenantContext, _CountingLLM]:
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
    settings = get_settings()
    retrieval = RetrievalService(
        provider,
        _MemoryStore(records),
        min_retrieval_score=_THRESHOLD,
        index_version="v1",
    )
    llm = _CountingLLM(
        OllamaLLMProvider(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            temperature=0.0,
            connect_timeout_seconds=settings.llm_connect_timeout_seconds,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    )
    service = QuestionService(
        retrieval,
        llm,
        _Opener(_Catalog(versions)),
        max_context_chars=settings.max_context_chars,
        min_retrieval_score=_THRESHOLD,
        min_lexical_score=settings.min_lexical_score,
    )
    return service, context, llm


@pytest.mark.parametrize(("question", "fact", "source"), _KNOWN)
def test_known_questions_answer_the_fact_and_cite_only_that_document(
    asked_corpus: tuple[QuestionService, TenantContext, _CountingLLM],
    question: str,
    fact: str,
    source: str,
) -> None:
    service, context, _llm = asked_corpus
    result = asyncio.run(service.answer(context, QuestionRequest(question=question)))

    assert result.abstained is False
    assert fact in result.answer
    assert {item.document_name for item in result.citations} == {source}


def test_an_unknown_question_abstains_without_calling_the_model(
    asked_corpus: tuple[QuestionService, TenantContext, _CountingLLM],
) -> None:
    service, context, llm = asked_corpus
    before = llm.calls
    result = asyncio.run(service.answer(context, QuestionRequest(question=_OUT_OF_DOMAIN)))

    assert result.abstained is True
    assert result.answer == PUBLIC_ABSTENTION
    assert result.citations == ()
    assert llm.calls == before
