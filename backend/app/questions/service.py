"""Orquesta recuperación, compuerta de evidencia, LLM y citas reales."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Protocol
from uuid import UUID, uuid4

from app.documents.models import Chunk
from app.domain.entities import TenantContext
from app.domain.enums import PrincipalType
from app.domain.evidence_policy import DEFAULT_LEXICAL_FLOOR, EvidenceCandidate, select_evidence
from app.domain.follow_up import FOLLOW_UP_PARTS
from app.generation.base import GenerationRequest, LLMProvider
from app.generation.errors import GenerationError, GenerationFormatError, UngroundedAnswerError
from app.generation.models import PROMPT_VERSION, PUBLIC_ABSTENTION, Citation
from app.generation.prompt import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from app.generation.validate import ModelOutput, ground_output, parse_model_output
from app.questions.models import (
    DebugEvidence,
    PublicCitation,
    QuestionAnswer,
    QuestionDebug,
    QuestionRequest,
)
from app.retrieval.contracts import (
    DocumentReadCatalog,
    Evidence,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)
from app.retrieval.identifiers import (
    is_label_only,
    label_heading,
    named_label,
    opens_with_kind,
    required_texts,
    statement_span,
    statement_text,
)
from app.retrieval.models import SearchResult
from app.retrieval.sample_search import LocalSearchHit

logger = logging.getLogger(__name__)

_CHUNKER_VERSION = "question-context"
_UNGROUNDED = "La respuesta no se acepta porque no cita un fragmento recuperado"


class CatalogOpener(Protocol):
    """Abre el catálogo de la empresa del contexto y lo cierra al salir."""

    def open(self, context: TenantContext) -> AbstractAsyncContextManager[DocumentReadCatalog]:
        """Devuelve un contexto async con el catálogo ya acotado a la empresa."""
        ...


class QuestionRetriever(Protocol):
    """Recuperación que la pregunta y la evaluación pueden sustituir en pruebas."""

    async def retrieve(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        candidate_floor: float | None = None,
        containing: tuple[str, ...] = (),
    ) -> RetrievalResult:
        """Devuelve evidencia ordenada. No decide la respuesta."""
        ...

    async def retrieve_part(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        part: str,
        topics: tuple[tuple[str, str], ...],
    ) -> RetrievalResult:
        """Trae una parte ya resuelta. La evaluación de esta etapa no la usa."""
        ...


class QuestionService:
    """Recupera y responde un turno. HTTP y SSE llaman a `answer`."""

    def __init__(
        self,
        retrieval: QuestionRetriever,
        llm: LLMProvider,
        catalogs: CatalogOpener,
        *,
        max_context_chars: int,
        min_retrieval_score: float = 0.857,
        min_lexical_score: float = DEFAULT_LEXICAL_FLOOR,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._catalogs = catalogs
        self._max_context_chars = _require_max_context_chars(max_context_chars)
        self._min_retrieval_score = _unit_score(min_retrieval_score, name="min_retrieval_score")
        self._min_lexical_score = _unit_score(min_lexical_score, name="min_lexical_score")
        self._system_prompt = system_prompt

    async def answer(
        self,
        context: TenantContext,
        request: QuestionRequest,
        *,
        retrieval_query: str | None = None,
        part: str | None = None,
        topics: tuple[tuple[str, str], ...] = (),
    ) -> QuestionAnswer:
        """Recupera, se abstiene sin evidencia y solo publica citas recuperadas.

        `retrieval_query` solo alimenta la búsqueda. Las agujas, el recorte del
        enunciado y el prompt siguen la pregunta que escribió el usuario.
        Una parte ya resuelta (`part` y `topics`) publica esos fragmentos y no
        llama al modelo.
        """
        request_id = request.request_id if request.request_id is not None else uuid4()
        search_text = request.question if retrieval_query is None else retrieval_query
        if part in FOLLOW_UP_PARTS and topics:
            return await self._answer_part(
                context,
                request,
                request_id,
                search_text,
                part,
                topics,
            )
        needles = required_texts(request.question)
        async with self._catalogs.open(context) as documents:
            retrieved = await self._retrieval.retrieve(
                context,
                documents,
                RetrievalRequest(
                    question=search_text,
                    top_k=request.top_k,
                    document_ids=request.document_ids,
                    request_id=request_id,
                ),
                candidate_floor=None if needles else self._min_lexical_score,
                containing=needles,
            )
        evidence = _gated_evidence(
            request.question,
            retrieved.evidence,
            needles=needles,
            semantic_floor=self._min_retrieval_score,
            lexical_floor=self._min_lexical_score,
        )
        copies = _context_copies(evidence, self._max_context_chars)
        if not copies:
            result = _assemble(
                context,
                request_id=request_id,
                answer=PUBLIC_ABSTENTION,
                abstained=True,
                citations=(),
                trace=retrieved.trace,
                model_version=self._llm.model_version,
                generation_ms=0.0,
                llm_called=False,
                token_estimate=0,
                debug_evidence=(),
                include_debug=request.include_debug,
            )
            _log(
                result,
                trace=retrieved.trace,
                generation_ms=0.0,
                token_estimate=0,
                llm_called=False,
            )
            return result
        direct = _direct_statement(request.question, copies)
        if direct is not None:
            statement, item = direct
            result = _assemble(
                context,
                request_id=request_id,
                answer=statement,
                abstained=False,
                citations=(
                    PublicCitation(
                        document_id=item.document_id,
                        document_name=item.source,
                        page=item.page_start,
                        section=item.section,
                        excerpt=statement,
                    ),
                ),
                trace=retrieved.trace,
                model_version=self._llm.model_version,
                generation_ms=0.0,
                llm_called=False,
                token_estimate=0,
                debug_evidence=(
                    DebugEvidence(
                        chunk_id=item.chunk_id,
                        document_id=item.document_id,
                        score=item.score,
                        position=item.position,
                        document_version_id=item.document_version_id,
                    ),
                ),
                include_debug=request.include_debug,
            )
            _log(
                result,
                trace=retrieved.trace,
                generation_ms=0.0,
                token_estimate=0,
                llm_called=False,
            )
            return result
        hits = tuple(_as_hit(item, text) for item, text in copies)
        sent = [self._system_prompt, build_user_prompt(request.question, hits)]
        started = time.perf_counter()
        try:
            parsed = await self._complete(sent)
            answer, abstained, grounded = ground_output(parsed, hits)
            citations = _public_citations(grounded, copies)
            if not abstained and not citations:
                raise UngroundedAnswerError(_UNGROUNDED)
        except GenerationError as error:
            generation_ms = (time.perf_counter() - started) * 1000
            _log_failure(
                request_id=request_id,
                code=error.code,
                trace=retrieved.trace,
                generation_ms=generation_ms,
                token_estimate=_estimate_tokens(sent),
                pipeline_version=_pipeline_version(retrieved.trace, self._llm.model_version),
            )
            raise
        generation_ms = (time.perf_counter() - started) * 1000
        token_estimate = _estimate_tokens(sent)
        result = _assemble(
            context,
            request_id=request_id,
            answer=answer,
            abstained=abstained,
            citations=citations,
            trace=retrieved.trace,
            model_version=self._llm.model_version,
            generation_ms=generation_ms,
            llm_called=True,
            token_estimate=token_estimate,
            debug_evidence=tuple(
                DebugEvidence(
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    score=item.score,
                    position=item.position,
                    document_version_id=item.document_version_id,
                )
                for item, _text in copies
            ),
            include_debug=request.include_debug,
        )
        _log(
            result,
            trace=retrieved.trace,
            generation_ms=generation_ms,
            token_estimate=token_estimate,
            llm_called=True,
        )
        return result

    async def _answer_part(
        self,
        context: TenantContext,
        request: QuestionRequest,
        request_id: UUID,
        search_text: str,
        part: str,
        topics: tuple[tuple[str, str], ...],
    ) -> QuestionAnswer:
        """Publica la parte recuperada. No recorta el enunciado ni llama al modelo."""
        async with self._catalogs.open(context) as documents:
            retrieved = await self._retrieval.retrieve_part(
                context,
                documents,
                RetrievalRequest(
                    question=search_text,
                    top_k=request.top_k,
                    document_ids=request.document_ids,
                    request_id=request_id,
                ),
                part=part,
                topics=topics,
            )
        copies = tuple(
            (item, text)
            for item, text in _context_copies(retrieved.evidence, self._max_context_chars)
            if "/" not in item.source and "\\" not in item.source
        )
        if not copies:
            result = _assemble(
                context,
                request_id=request_id,
                answer=PUBLIC_ABSTENTION,
                abstained=True,
                citations=(),
                trace=retrieved.trace,
                model_version=self._llm.model_version,
                generation_ms=0.0,
                llm_called=False,
                token_estimate=0,
                debug_evidence=(),
                include_debug=request.include_debug,
            )
            _log(
                result,
                trace=retrieved.trace,
                generation_ms=0.0,
                token_estimate=0,
                llm_called=False,
            )
            return result
        result = _assemble(
            context,
            request_id=request_id,
            answer="\n\n".join(text for _item, text in copies),
            abstained=False,
            citations=tuple(
                PublicCitation(
                    document_id=item.document_id,
                    document_name=item.source,
                    page=item.page_start,
                    section=item.section,
                    excerpt=text,
                )
                for item, text in copies
            ),
            trace=retrieved.trace,
            model_version=self._llm.model_version,
            generation_ms=0.0,
            llm_called=False,
            token_estimate=0,
            debug_evidence=tuple(
                DebugEvidence(
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    score=item.score,
                    position=item.position,
                    document_version_id=item.document_version_id,
                )
                for item, _text in copies
            ),
            include_debug=request.include_debug,
        )
        _log(
            result,
            trace=retrieved.trace,
            generation_ms=0.0,
            token_estimate=0,
            llm_called=False,
        )
        return result

    async def _complete(self, sent: list[str]) -> ModelOutput:
        user_prompt = sent[-1]
        first = await asyncio.to_thread(
            self._llm.generate,
            GenerationRequest(system_prompt=self._system_prompt, user_prompt=user_prompt),
        )
        try:
            return parse_model_output(first.text)
        except GenerationFormatError:
            repair_prompt = build_repair_prompt(user_prompt, first.text)
            sent.append(self._system_prompt)
            sent.append(repair_prompt)
            second = await asyncio.to_thread(
                self._llm.generate,
                GenerationRequest(system_prompt=self._system_prompt, user_prompt=repair_prompt),
            )
            return parse_model_output(second.text)


def _assemble(
    context: TenantContext,
    *,
    request_id: UUID,
    answer: str,
    abstained: bool,
    citations: tuple[PublicCitation, ...],
    trace: RetrievalTrace,
    model_version: str,
    generation_ms: float,
    llm_called: bool,
    token_estimate: int,
    debug_evidence: tuple[DebugEvidence, ...],
    include_debug: bool,
) -> QuestionAnswer:
    technical = QuestionDebug(
        request_id=request_id,
        embedding_version=trace.embedding_version,
        index_version=trace.index_version,
        hits_retrieved=trace.hits_retrieved,
        hits_after_validation=trace.hits_after_validation,
        hits_after_threshold=trace.hits_after_threshold,
        returned=trace.returned,
        rounds=trace.rounds,
        top_k=trace.top_k,
        min_retrieval_score=trace.min_retrieval_score,
        embed_ms=trace.embed_ms,
        search_ms=trace.search_ms,
        validate_ms=trace.validate_ms,
        generation_ms=generation_ms,
        llm_called=llm_called,
        token_estimate=token_estimate,
        evidence=debug_evidence,
    )
    admin_debug = include_debug and context.principal.type is PrincipalType.TENANT_ADMIN
    debug = technical if admin_debug else None
    return QuestionAnswer(
        request_id=request_id,
        answer=answer,
        abstained=abstained,
        citations=citations,
        pipeline_version=_pipeline_version(trace, model_version),
        debug=debug,
        technical=technical,
    )


def _pipeline_version(trace: RetrievalTrace, model_version: str) -> str:
    return f"{trace.embedding_version}|{trace.index_version}|{PROMPT_VERSION}|{model_version}"


def _gated_evidence(
    question: str,
    evidence: tuple[Evidence, ...],
    *,
    needles: tuple[str, ...],
    semantic_floor: float,
    lexical_floor: float,
) -> tuple[Evidence, ...]:
    """Misma compuerta que el CLI, ya con los fragmentos validados en PostgreSQL."""
    if not evidence:
        return ()
    if needles:
        return _labeled_evidence(question, evidence, needles)
    candidates = tuple(
        EvidenceCandidate(source=item.source, score=item.score, text=item.excerpt)
        for item in evidence
    )
    chosen = select_evidence(
        question,
        candidates,
        semantic_floor=semantic_floor,
        lexical_floor=lexical_floor,
    )
    selected: list[Evidence] = []
    start = 0
    for item in chosen:
        for index in range(start, len(evidence)):
            current = evidence[index]
            if current.source != item.source or current.score != item.score:
                continue
            selected.append(_with_excerpt(current, item.text))
            start = index + 1
            break
    return tuple(selected)


def _labeled_evidence(
    question: str,
    evidence: tuple[Evidence, ...],
    needles: tuple[str, ...],
) -> tuple[Evidence, ...]:
    named = named_label(question)
    if named is not None:
        headed = tuple(
            item for item in evidence if opens_with_kind(item.excerpt, named[0], named[1])
        )
        if not headed:
            return ()
        chosen = headed[:1]
    else:
        headed = tuple(item for item in evidence if label_heading(item.excerpt, needles))
        chosen = headed[:1] if headed else evidence[:1]
    return tuple(_with_excerpt(item, statement_span(item.excerpt, needles)) for item in chosen)


def _direct_statement(
    question: str,
    copies: tuple[tuple[Evidence, str], ...],
) -> tuple[str, Evidence] | None:
    """Enunciado ya recuperado cuando la pregunta es solo el rótulo."""
    if not is_label_only(question):
        return None
    named = named_label(question)
    if named is None:
        return None
    kind, number = named
    for item, text in copies:
        if "/" in item.source or "\\" in item.source:
            continue
        if not opens_with_kind(text, kind, number):
            continue
        statement = statement_text(text).strip()
        if not statement or is_label_only(statement):
            continue
        return statement, item
    return None


def _with_excerpt(item: Evidence, text: str) -> Evidence:
    if text == item.excerpt:
        return item
    return replace(item, excerpt=text)


def _context_copies(
    evidence: tuple[Evidence, ...],
    max_chars: int,
) -> tuple[tuple[Evidence, str], ...]:
    """Recorta copias del contexto. El extracto guardado en la evidencia no cambia."""
    remaining = max_chars
    copies: list[tuple[Evidence, str]] = []
    for item in evidence:
        if remaining <= 0:
            break
        cleaned = _clean_copy(item.excerpt)
        if not cleaned.strip():
            continue
        text = cleaned if len(cleaned) <= remaining else cleaned[:remaining]
        if not text.strip():
            break
        copies.append((item, text))
        remaining -= len(text)
    return tuple(copies)


def _clean_copy(text: str) -> str:
    return "".join(
        character for character in text if character.isprintable() or character in "\n\t"
    )


def _as_hit(item: Evidence, text: str) -> LocalSearchHit:
    chunk = Chunk(
        id=item.chunk_id,
        text=text,
        page_start=item.page_start,
        page_end=item.page_end,
        section=item.section,
        position=item.position,
        chunker_version=_CHUNKER_VERSION,
    )
    return LocalSearchHit(
        result=SearchResult(chunk=chunk, score=item.score, position=item.position),
        source=item.source,
    )


def _public_citations(
    citations: tuple[Citation, ...],
    copies: tuple[tuple[Evidence, str], ...],
) -> tuple[PublicCitation, ...]:
    by_chunk = {item.chunk_id: item for item, _text in copies}
    public: list[PublicCitation] = []
    for citation in citations:
        item = by_chunk.get(citation.chunk_id)
        if item is None or "/" in item.source or "\\" in item.source:
            continue
        public.append(
            PublicCitation(
                document_id=item.document_id,
                document_name=item.source,
                page=citation.page_start,
                section=citation.section,
                excerpt=citation.excerpt,
            )
        )
    return tuple(public)


def _estimate_tokens(parts: list[str]) -> int:
    characters = sum(len(part) for part in parts)
    if characters <= 0:
        return 0
    return max(1, characters // 4)


def _unit_score(value: float, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} debe ser un número entre 0 y 1"
        raise ValueError(message)
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        message = f"{name} debe ser un número entre 0 y 1"
        raise ValueError(message)
    return score


def _require_max_context_chars(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = "El máximo de contexto debe ser un entero positivo"
        raise ValueError(message)
    return value


def _log(
    result: QuestionAnswer,
    *,
    trace: RetrievalTrace,
    generation_ms: float,
    token_estimate: int,
    llm_called: bool,
) -> None:
    logger.info(
        "pregunta request_id=%s abstained=%s llm_called=%s citations=%d pipeline=%s "
        "embed_ms=%.3f search_ms=%.3f validate_ms=%.3f generation_ms=%.3f tokens_estimate=%d",
        result.request_id,
        result.abstained,
        llm_called,
        len(result.citations),
        result.pipeline_version,
        trace.embed_ms,
        trace.search_ms,
        trace.validate_ms,
        generation_ms,
        token_estimate,
    )


def _log_failure(
    *,
    request_id: UUID,
    code: str,
    trace: RetrievalTrace,
    generation_ms: float,
    token_estimate: int,
    pipeline_version: str,
) -> None:
    logger.info(
        "pregunta request_id=%s status=error code=%s pipeline=%s embed_ms=%.3f "
        "search_ms=%.3f validate_ms=%.3f generation_ms=%.3f tokens_estimate=%d",
        request_id,
        code,
        pipeline_version,
        trace.embed_ms,
        trace.search_ms,
        trace.validate_ms,
        generation_ms,
        token_estimate,
    )
