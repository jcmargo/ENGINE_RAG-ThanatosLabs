"""Corre calibración y después el split de test. No registra preguntas."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.entities import TenantContext
from app.domain.enums import EvaluationCaseStatus, EvaluationSplit
from app.domain.errors import EvaluationRejected
from app.domain.evaluation import (
    CalibrationObservation,
    CaseScore,
    EvaluationCase,
    EvaluationConfig,
    EvaluationMetrics,
    FailedCase,
    SplitObservation,
    compute_metrics,
    config_fingerprint,
    failed_cases,
    freeze_threshold,
    score_case,
)
from app.evaluation.executor import ScoreSnapshot, ServiceExecutor

logger = logging.getLogger(__name__)

PROFILE_REFERENCE = "deterministic-reference"


@dataclass(frozen=True)
class EvaluationReport:
    """Artefacto de una corrida. No contiene documentos ni preguntas."""

    profile: str
    dataset_version: str
    run_version: str
    config: EvaluationConfig
    metrics: EvaluationMetrics
    failed_cases: tuple[FailedCase, ...]
    isolation_ok: bool


async def run_evaluation(
    cases: Sequence[EvaluationCase],
    executor: ServiceExecutor,
    context: TenantContext,
    *,
    corpus_sha256: str,
    parser_version: str,
    chunker_version: str,
    prompt_version: str,
    dataset_version: str,
    top_k: int,
    profile: str = PROFILE_REFERENCE,
    suggest_judge: bool = False,
) -> EvaluationReport:
    """Calibra con un split y mide el otro. El umbral no vuelve a moverse."""
    if not 1 <= top_k <= 20:
        message = "top_k de la evaluación no es válido"
        raise EvaluationRejected(message)
    active = tuple(case for case in cases if case.status is EvaluationCaseStatus.ACTIVE)
    observations: list[SplitObservation] = []
    snapshots: dict[str, ScoreSnapshot] = {}
    for case in active:
        snapshot = await executor.retrieve_scores(
            context,
            case.question,
            case.case_key,
            top_k=top_k,
        )
        snapshots[case.case_key] = snapshot
        logger.info("evaluacion case_id=%s split=%s", case.case_key, case.split.value)
        observations.append(
            SplitObservation(
                split=case.split,
                observation=CalibrationObservation(
                    case_id=case.case_key,
                    answerable=case.answerable,
                    expected_sources=case.expected_sources,
                    hits=snapshot.hits,
                ),
            )
        )
    threshold = freeze_threshold(tuple(observations))
    embedding_versions = {item.embedding_version for item in snapshots.values()}
    index_versions = {item.index_version for item in snapshots.values()}
    if len(embedding_versions) != 1 or len(index_versions) != 1:
        message = "La corrida no tiene una sola versión de índice"
        raise EvaluationRejected(message)
    config = EvaluationConfig(
        corpus_sha256=corpus_sha256,
        parser_version=parser_version,
        chunker_version=chunker_version,
        embedding_version=next(iter(embedding_versions)),
        index_version=next(iter(index_versions)),
        top_k=top_k,
        min_retrieval_score=threshold,
        prompt_version=prompt_version,
        llm_version=executor.llm_version,
        dataset_version=dataset_version,
    )
    scores = await _score_test(
        active,
        snapshots,
        executor,
        context,
        top_k=top_k,
        threshold=threshold,
        suggest_judge=suggest_judge,
    )
    isolation_ok = await executor.probe_isolation(context, top_k=top_k)
    metrics = compute_metrics(scores)
    return EvaluationReport(
        profile=profile,
        dataset_version=dataset_version,
        run_version=config_fingerprint(config),
        config=config,
        metrics=metrics,
        failed_cases=failed_cases(scores),
        isolation_ok=isolation_ok,
    )


async def _score_test(
    cases: Sequence[EvaluationCase],
    snapshots: dict[str, ScoreSnapshot],
    executor: ServiceExecutor,
    context: TenantContext,
    *,
    top_k: int,
    threshold: float,
    suggest_judge: bool,
) -> tuple[CaseScore, ...]:
    selected = sorted(
        (case for case in cases if case.split is EvaluationSplit.TEST),
        key=lambda case: case.case_key,
    )
    scores: list[CaseScore] = []
    for case in selected:
        generated = await executor.answer(
            context,
            case.question,
            case.case_key,
            top_k=top_k,
            min_retrieval_score=threshold,
        )
        snapshot = snapshots[case.case_key]
        logger.info("evaluacion case_id=%s split=%s", case.case_key, case.split.value)
        scores.append(
            score_case(
                case,
                snapshot.hits,
                abstained=generated.abstained,
                answer_text=generated.answer_text,
                citation_sources=generated.citation_sources,
                citation_pages=generated.citation_pages,
                retrieval_ms=snapshot.retrieval_ms,
                k=top_k,
                suggest=suggest_judge,
            )
        )
    return tuple(scores)
