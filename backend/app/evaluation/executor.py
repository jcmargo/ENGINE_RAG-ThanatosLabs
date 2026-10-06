"""Ejecuta casos llamando a QuestionService. No reimplementa el RAG."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from app.domain.entities import TenantContext
from app.domain.evaluation import CalibrationHit
from app.domain.evidence_policy import DEFAULT_LEXICAL_FLOOR
from app.generation.base import LLMProvider
from app.generation.errors import GenerationError
from app.questions.models import QuestionRequest
from app.questions.service import CatalogOpener, QuestionRetriever, QuestionService
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.errors import RetrievalError

_REQUESTS = UUID("6d1f0c2a-7b4e-4a1d-9c33-0e5a6b7c8d90")
PROBE_QUESTION = "consulta de aislamiento entre empresas"
FOREIGN_SOURCE = "otra-empresa.md"


@dataclass(frozen=True)
class ScoreSnapshot:
    """Ranking con piso cero. Sin extracto."""

    hits: tuple[CalibrationHit, ...]
    retrieval_ms: float
    embedding_version: str
    index_version: str


@dataclass(frozen=True)
class GeneratedAnswer:
    """Respuesta ya separada del documento. El texto solo sirve para medir hechos."""

    abstained: bool
    answer_text: str
    citation_sources: tuple[str, ...]
    citation_pages: tuple[int, ...]


class ServiceExecutor:
    """Recupera con piso cero y responde con el umbral ya congelado."""

    def __init__(
        self,
        retrieval: QuestionRetriever,
        llm: LLMProvider,
        catalogs: CatalogOpener,
        *,
        llm_version: str,
        max_context_chars: int,
        min_lexical_score: float = DEFAULT_LEXICAL_FLOOR,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._catalogs = catalogs
        self._llm_version = llm_version
        self._max_context_chars = max_context_chars
        self._min_lexical_score = min_lexical_score

    @property
    def llm_version(self) -> str:
        """Versión del modelo que realmente responde."""
        return self._llm_version

    async def retrieve_scores(
        self,
        context: TenantContext,
        question: str,
        case_key: str,
        *,
        top_k: int,
    ) -> ScoreSnapshot:
        """Observa el ranking antes de congelar el umbral."""
        async with self._catalogs.open(context) as documents:
            result = await self._retrieval.retrieve(
                context,
                documents,
                RetrievalRequest(
                    question=question,
                    top_k=top_k,
                    request_id=_request_id(case_key),
                ),
                candidate_floor=0.0,
            )
        trace = result.trace
        hits = tuple(
            CalibrationHit(source=item.source, score=item.score, page=item.page_start)
            for item in result.evidence
        )
        return ScoreSnapshot(
            hits=hits,
            retrieval_ms=trace.embed_ms + trace.search_ms + trace.validate_ms,
            embedding_version=trace.embedding_version,
            index_version=trace.index_version,
        )

    async def answer(
        self,
        context: TenantContext,
        question: str,
        case_key: str,
        *,
        top_k: int,
        min_retrieval_score: float,
    ) -> GeneratedAnswer:
        """Responde con QuestionService y el umbral congelado."""
        service = QuestionService(
            self._retrieval,
            self._llm,
            self._catalogs,
            max_context_chars=self._max_context_chars,
            min_retrieval_score=min_retrieval_score,
            min_lexical_score=self._min_lexical_score,
        )
        try:
            result = await service.answer(
                context,
                QuestionRequest(
                    question=question,
                    top_k=top_k,
                    request_id=_request_id(f"answer-{case_key}"),
                ),
            )
        except (GenerationError, RetrievalError):
            return GeneratedAnswer(
                abstained=False,
                answer_text="",
                citation_sources=(),
                citation_pages=(),
            )
        return GeneratedAnswer(
            abstained=result.abstained,
            answer_text=result.answer,
            citation_sources=tuple(item.document_name for item in result.citations),
            citation_pages=tuple(item.page for item in result.citations),
        )

    async def probe_isolation(self, context: TenantContext, *, top_k: int) -> bool:
        """True si la sonda no devuelve el documento de otra empresa."""
        async with self._catalogs.open(context) as documents:
            result = await self._retrieval.retrieve(
                context,
                documents,
                RetrievalRequest(
                    question=PROBE_QUESTION,
                    top_k=top_k,
                    request_id=_request_id("isolation"),
                ),
                candidate_floor=0.0,
            )
        return all(item.source != FOREIGN_SOURCE for item in result.evidence)


def _request_id(key: str) -> UUID:
    return uuid5(_REQUESTS, key)
