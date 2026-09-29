"""Búsqueda, umbral y generación. Sin evidencia no llama al modelo."""

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from app.generation.base import GenerationRequest, LLMProvider
from app.generation.errors import GenerationError, GenerationFormatError
from app.generation.models import (
    PROMPT_VERSION,
    PUBLIC_ABSTENTION,
    RETRIEVAL_VERSION,
    Citation,
    GroundedAnswer,
    GroundedRun,
)
from app.generation.prompt import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from app.generation.validate import ground_output, parse_model_output
from app.retrieval.identifiers import label_heading, required_texts, statement_span
from app.retrieval.sample_search import LocalSearchHit

logger = logging.getLogger(__name__)


class EvidenceSearch(Protocol):
    """Búsqueda inyectable. La implementa el índice local de ejemplos."""

    def search(
        self,
        question: str,
        top_k: int,
        *,
        containing: Sequence[str] = (),
    ) -> tuple[LocalSearchHit, ...]: ...


class GroundedRAGService:
    """Compone recuperación, umbral, generación y citas reales."""

    def __init__(
        self,
        search: EvidenceSearch,
        llm: LLMProvider,
        *,
        min_retrieval_score: float,
        retrieval_version: str = RETRIEVAL_VERSION,
    ) -> None:
        if not retrieval_version.strip():
            message = "La versión de recuperación no puede estar vacía"
            raise ValueError(message)
        self._search = search
        self._llm = llm
        self._min_retrieval_score = _unit_score(min_retrieval_score)
        self._retrieval_version = retrieval_version

    def answer(self, question: str, *, top_k: int = 5) -> GroundedRun:
        """Responde una pregunta independiente. No conserva turnos anteriores."""
        if not question.strip():
            message = "La pregunta no puede estar vacía"
            raise ValueError(message)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            message = "top_k debe ser un entero mayor que cero"
            raise ValueError(message)
        started = time.perf_counter()
        needles = required_texts(question)
        if needles:
            retrieved = self._search.search(question, top_k, containing=needles)
            evidence = _labeled_evidence(retrieved, needles)
        else:
            retrieved = self._search.search(question, top_k)
            evidence = tuple(
                hit for hit in retrieved if hit.result.score >= self._min_retrieval_score
            )
        retrieval_ms = (time.perf_counter() - started) * 1000
        if not evidence:
            run = self._abstain(retrieved, retrieval_ms)
            _log_run(run, len(evidence), identifier_count=len(needles))
            return run
        generation_started = time.perf_counter()
        try:
            answer_text, abstained, citations = self._generate(question, evidence)
        except GenerationError:
            logger.info("grounded status=error llm_called=true")
            raise
        generation_ms = (time.perf_counter() - generation_started) * 1000
        run = GroundedRun(
            answer=GroundedAnswer(
                answer=answer_text,
                abstained=abstained,
                citations=citations,
                retrieval_version=self._retrieval_version,
                prompt_version=PROMPT_VERSION,
                model_version=self._llm.model_version,
            ),
            retrieved=retrieved,
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            llm_called=True,
        )
        _log_run(run, len(evidence), identifier_count=len(needles))
        return run

    def _generate(
        self,
        question: str,
        evidence: tuple[LocalSearchHit, ...],
    ) -> tuple[str, bool, tuple[Citation, ...]]:
        request = GenerationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(question, evidence),
        )
        first = self._llm.generate(request)
        try:
            parsed = parse_model_output(first.text)
        except GenerationFormatError:
            repair = GenerationRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_repair_prompt(request.user_prompt, first.text),
            )
            second = self._llm.generate(repair)
            parsed = parse_model_output(second.text)
        answer, abstained, citations = ground_output(parsed, evidence)
        return answer, abstained, citations

    def _abstain(
        self,
        retrieved: tuple[LocalSearchHit, ...],
        retrieval_ms: float,
    ) -> GroundedRun:
        return GroundedRun(
            answer=GroundedAnswer(
                answer=PUBLIC_ABSTENTION,
                abstained=True,
                citations=(),
                retrieval_version=self._retrieval_version,
                prompt_version=PROMPT_VERSION,
                model_version=self._llm.model_version,
            ),
            retrieved=retrieved,
            retrieval_ms=retrieval_ms,
            generation_ms=0.0,
            llm_called=False,
        )


def _labeled_evidence(
    retrieved: tuple[LocalSearchHit, ...],
    needles: Sequence[str],
) -> tuple[LocalSearchHit, ...]:
    headed = tuple(hit for hit in retrieved if label_heading(hit.result.chunk.text, needles))
    chosen = headed[:1] if headed else retrieved[:1]
    return tuple(_focus_statement(hit, needles) for hit in chosen)


def _focus_statement(hit: LocalSearchHit, needles: Sequence[str]) -> LocalSearchHit:
    span = statement_span(hit.result.chunk.text, needles)
    if span == hit.result.chunk.text:
        return hit
    return replace(hit, result=replace(hit.result, chunk=replace(hit.result.chunk, text=span)))


def _log_run(run: GroundedRun, evidence_count: int, *, identifier_count: int) -> None:
    logger.info(
        "grounded evidence=%d retrieved=%d identifiers=%d llm_called=%s "
        "retrieval_ms=%.3f generation_ms=%.3f",
        evidence_count,
        len(run.retrieved),
        identifier_count,
        run.llm_called,
        run.retrieval_ms,
        run.generation_ms,
    )


def _unit_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "min_retrieval_score debe ser un número entre 0 y 1"
        raise ValueError(message)
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        message = "min_retrieval_score debe ser un número entre 0 y 1"
        raise ValueError(message)
    return score
