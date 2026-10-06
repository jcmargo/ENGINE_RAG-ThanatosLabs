"""Perfil determinista. Usa QuestionService con recuperación y LLM de guion."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from enum import StrEnum
from uuid import UUID, uuid5

from app.domain.entities import RetrievableVersion, TenantContext
from app.domain.evaluation import EvaluationCase
from app.embeddings.fake import FAKE_EMBEDDING_VERSION
from app.evaluation.executor import FOREIGN_SOURCE, PROBE_QUESTION, ServiceExecutor
from app.generation.base import GenerationRequest, GenerationResult, streaming_not_available
from app.generation.models import PUBLIC_ABSTENTION
from app.retrieval.contracts import (
    DocumentReadCatalog,
    Evidence,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)

_DOCUMENTS = UUID("91c5e0a4-2b7d-4e6f-8a11-3c9d5e7f1024")
_SCORE = 1.0
_LATENCY_MS = 1.0
INDEX_VERSION = "eval-reference-v1"
LLM_VERSION = "deterministic-fake-llm-v1"
_MARKER = "MARCA-AISLAMIENTO-NO-REPORTAR"
MAX_CONTEXT_CHARS = 12_000


class ReferenceMode(StrEnum):
    """Comportamiento del guion. La fuga y la pérdida de hits son regresiones."""

    REFERENCE = "reference"
    DROP_HITS = "drop_hits"
    LEAK = "leak"


class _ClosedCatalog:
    """El perfil de referencia no consulta PostgreSQL."""

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        del document_ids
        return frozenset()

    async def find_retrievable_versions(
        self,
        version_ids: Sequence[UUID],
    ) -> tuple[RetrievableVersion, ...]:
        del version_ids
        return ()


class _Opener:
    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[DocumentReadCatalog]:
        """El perfil de referencia no abre PostgreSQL."""
        del context
        yield _ClosedCatalog()


class ReferenceRetrieval:
    """Devuelve la fuente esperada con puntuación fija, o nada si no hay respuesta."""

    def __init__(self, cases: Sequence[EvaluationCase], *, mode: ReferenceMode) -> None:
        self._cases = {case.question.casefold(): case for case in cases}
        self._mode = mode

    async def retrieve(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        candidate_floor: float | None = None,
        containing: tuple[str, ...] = (),
    ) -> RetrievalResult:
        del context, documents, candidate_floor, containing
        evidence = self._evidence(request.question)
        request_id = request.request_id
        if request_id is None:
            message = "La evaluación necesita un request_id"
            raise ValueError(message)
        return RetrievalResult(
            evidence=evidence,
            has_sufficient_evidence=bool(evidence),
            trace=_trace(request_id, request.top_k, returned=len(evidence)),
        )

    async def retrieve_part(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        part: str,
        topics: tuple[tuple[str, str], ...],
    ) -> RetrievalResult:
        del context, documents, part, topics
        request_id = request.request_id
        if request_id is None:
            message = "La evaluación necesita un request_id"
            raise ValueError(message)
        return RetrievalResult(
            evidence=(),
            has_sufficient_evidence=False,
            trace=_trace(request_id, request.top_k, returned=0),
        )

    def _evidence(self, question: str) -> tuple[Evidence, ...]:
        if question.casefold() == PROBE_QUESTION.casefold():
            if self._mode is ReferenceMode.LEAK:
                return (_hit(FOREIGN_SOURCE, _MARKER, page=1),)
            return ()
        if self._mode is ReferenceMode.DROP_HITS:
            return ()
        case = self._cases.get(question.casefold())
        if case is None or not case.answerable or not case.expected_facts:
            return ()
        page = case.expected_pages[0] if case.expected_pages else 1
        return (_hit(case.expected_sources[0], case.expected_facts[0], page=page),)


class FactLLM:
    """Cita c1 con el hecho del caso. No inventa una respuesta nueva."""

    def __init__(self, cases: Sequence[EvaluationCase]) -> None:
        self._facts = tuple(
            (case.question, case.expected_facts[0])
            for case in sorted(cases, key=lambda item: len(item.question), reverse=True)
            if case.answerable and case.expected_facts
        )

    @property
    def model_version(self) -> str:
        return LLM_VERSION

    def generate(self, request: GenerationRequest) -> GenerationResult:
        for question, fact in self._facts:
            if question in request.user_prompt:
                payload = {"answer": fact, "abstained": False, "cited_chunk_ids": ["c1"]}
                return GenerationResult(text=json.dumps(payload, ensure_ascii=False))
        payload = {
            "answer": PUBLIC_ABSTENTION,
            "abstained": True,
            "cited_chunk_ids": [],
        }
        return GenerationResult(text=json.dumps(payload, ensure_ascii=False))

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        return streaming_not_available(request)


def reference_executor(
    cases: Sequence[EvaluationCase],
    *,
    mode: ReferenceMode = ReferenceMode.REFERENCE,
) -> ServiceExecutor:
    """Compone el servicio real de preguntas con proveedores deterministas."""
    retrieval = ReferenceRetrieval(cases, mode=mode)
    return ServiceExecutor(
        retrieval,
        FactLLM(cases),
        _Opener(),
        llm_version=LLM_VERSION,
        max_context_chars=MAX_CONTEXT_CHARS,
    )


def _hit(source: str, excerpt: str, *, page: int) -> Evidence:
    return Evidence(
        chunk_id=f"chunk-{source}",
        document_id=uuid5(_DOCUMENTS, source),
        document_version_id=uuid5(_DOCUMENTS, f"{source}-v1"),
        source=source,
        page_start=page,
        page_end=page,
        section=None,
        excerpt=excerpt,
        score=_SCORE,
        position=1,
    )


def _trace(request_id: UUID, top_k: int, *, returned: int) -> RetrievalTrace:
    return RetrievalTrace(
        request_id=request_id,
        embedding_version=FAKE_EMBEDDING_VERSION,
        index_version=INDEX_VERSION,
        hits_retrieved=returned,
        hits_after_validation=returned,
        hits_after_threshold=returned,
        returned=returned,
        rounds=1,
        top_k=top_k,
        min_retrieval_score=0.0,
        embed_ms=_LATENCY_MS,
        search_ms=_LATENCY_MS,
        validate_ms=_LATENCY_MS,
    )
