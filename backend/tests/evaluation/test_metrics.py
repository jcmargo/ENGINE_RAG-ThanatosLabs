"""Fórmulas de Hit@K y MRR con valores calculados a mano."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.enums import (
    EvaluationCaseStatus,
    EvaluationSplit,
    FidelityLabel,
)
from app.domain.evaluation import (
    CalibrationHit,
    CountRatio,
    EvaluationCase,
    answer_sha256,
    compute_metrics,
    hit_at_k,
    mean_reciprocal_rank,
    score_case,
    suggest_fidelity,
)

_NOW = datetime(2026, 10, 5, tzinfo=UTC)


def test_hit_and_mrr_match_hand_calculation() -> None:
    ranks = (1, 3, None)

    hit = hit_at_k(ranks, k=5)
    mrr = mean_reciprocal_rank(ranks)

    assert hit == CountRatio(numerator=2, denominator=3)
    assert hit.value == 2 / 3
    assert mrr.reciprocal_numerator == 4
    assert mrr.reciprocal_denominator == 3
    assert mrr.count == 3
    assert mrr.value == (4 / 3) / 3


def test_hit_at_one_misses_a_later_rank() -> None:
    hit = hit_at_k((3,), k=1)

    assert hit.numerator == 0
    assert hit.denominator == 1


def test_human_label_overrides_a_supported_suggestion() -> None:
    fact = "La cooperativa otorga 18 días hábiles de vacaciones al año."
    case = _case(fact, label=FidelityLabel.UNSUPPORTED)
    score = score_case(
        case,
        (CalibrationHit(source="vacaciones.md", score=1.0, page=1),),
        abstained=False,
        answer_text=fact,
        citation_sources=("vacaciones.md",),
        citation_pages=(1,),
        retrieval_ms=3.0,
        suggest=True,
    )

    assert score.suggested is FidelityLabel.SUPPORTED
    assert score.fidelity is FidelityLabel.UNSUPPORTED
    metrics = compute_metrics((score,))
    assert metrics.fidelity_unsupported.numerator == 1
    assert metrics.fidelity_unsupported.denominator == 1
    assert suggest_fidelity(facts_present=True, citation_correct=True, abstained=False) is (
        FidelityLabel.SUPPORTED
    )


def _case(fact: str, *, label: FidelityLabel) -> EvaluationCase:
    return EvaluationCase(
        id=uuid4(),
        tenant_id=uuid4(),
        dataset_id=uuid4(),
        case_key="t-etiqueta",
        version="v1",
        status=EvaluationCaseStatus.ACTIVE,
        split=EvaluationSplit.TEST,
        question="¿Cuántos días hábiles otorga la cooperativa?",
        answerable=True,
        expected_sources=("vacaciones.md",),
        expected_pages=(1,),
        expected_facts=(fact,),
        expected_patterns=(),
        tags=("numero",),
        fidelity_label=label,
        label_version=1,
        labeled_answer_sha256=answer_sha256(fact),
    )
