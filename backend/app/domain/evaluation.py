"""Métricas, calibración y gate. No leen archivos, reloj ni proveedores."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from uuid import UUID

from app.domain.enums import (
    CaseFailure,
    EvaluationCaseStatus,
    EvaluationDatasetStatus,
    EvaluationRunStatus,
    EvaluationSplit,
    FidelityLabel,
    GateFailure,
)
from app.domain.errors import EvaluationRejected, InvalidStateError

HIT_AT_K = 5
MIN_HIT_RATE = 0.90
MIN_CITATION_RATE = 0.90
MAX_UNSUPPORTED_RATE = 0.05
MIN_ABSTENTION_RATE = 0.90
DEFAULT_REGRESSION_TOLERANCE = 0.02
MIN_SPLIT_FACTOR = 5
_SHA256_WIDTH = 64
_MAX_QUESTION_CHARS = 2000
_EPSILON = 1e-12

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
)


def contains_secret(text: str) -> bool:
    """True si el texto trae un patrón de secreto. No devuelve el hallazgo."""
    return any(pattern.search(text) is not None for pattern in _SECRET_PATTERNS)


def answer_sha256(answer: str) -> str:
    """Huella de la respuesta revisada. No es una medida de corrección."""
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CountRatio:
    """Cociente auditable. El valor se deriva del numerador y el denominador."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _require_count(self.numerator, "numerator")
        _require_count(self.denominator, "denominator")
        if self.numerator > self.denominator:
            message = "El numerador no puede superar al denominador"
            raise InvalidStateError(message)

    @property
    def value(self) -> float | None:
        """Proporción, o None si no hay casos en el denominador."""
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True)
class ReciprocalMean:
    """Suma de recíprocos en fracción reducida, más el número de casos."""

    reciprocal_numerator: int
    reciprocal_denominator: int
    count: int

    def __post_init__(self) -> None:
        _require_count(self.reciprocal_numerator, "reciprocal_numerator")
        if (
            isinstance(self.reciprocal_denominator, bool)
            or not isinstance(self.reciprocal_denominator, int)
            or self.reciprocal_denominator < 1
        ):
            message = "El denominador del MRR no es válido"
            raise InvalidStateError(message)
        _require_count(self.count, "count")

    @property
    def value(self) -> float | None:
        """MRR, o None si no hay casos."""
        if self.count == 0:
            return None
        return (self.reciprocal_numerator / self.reciprocal_denominator) / self.count


@dataclass(frozen=True)
class CalibrationHit:
    """Fuente y puntuación de un hit. Sin extracto."""

    source: str
    score: float
    page: int

    def __post_init__(self) -> None:
        _require_text(self.source, "source")
        _require_unit_score(self.score)
        _require_page(self.page)


@dataclass(frozen=True)
class CalibrationObservation:
    """Un caso de calibración ya recuperado, sin texto de documento."""

    case_id: str
    answerable: bool
    expected_sources: tuple[str, ...]
    hits: tuple[CalibrationHit, ...]

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        if not isinstance(self.answerable, bool):
            message = "answerable debe ser booleano"
            raise InvalidStateError(message)


@dataclass(frozen=True)
class SplitObservation:
    """Observación marcada con su split. El test no entra en la calibración."""

    split: EvaluationSplit
    observation: CalibrationObservation


@dataclass(frozen=True)
class EvaluationConfig:
    """Huella reproducible de la corrida. El umbral ya está congelado."""

    corpus_sha256: str
    parser_version: str
    chunker_version: str
    embedding_version: str
    index_version: str
    top_k: int
    min_retrieval_score: float
    prompt_version: str
    llm_version: str
    dataset_version: str

    def __post_init__(self) -> None:
        _require_sha256(self.corpus_sha256, "corpus_sha256")
        _require_text(self.parser_version, "parser_version")
        _require_text(self.chunker_version, "chunker_version")
        _require_text(self.embedding_version, "embedding_version")
        _require_text(self.index_version, "index_version")
        _require_top_k(self.top_k)
        _require_unit_score(self.min_retrieval_score)
        _require_text(self.prompt_version, "prompt_version")
        _require_text(self.llm_version, "llm_version")
        _require_text(self.dataset_version, "dataset_version")


@dataclass(frozen=True)
class EvaluationMetrics:
    """Métricas del split de test. Recuperación y generación van separadas."""

    hit_at_5: CountRatio
    mrr: ReciprocalMean
    citation: CountRatio
    unanswerable_abstention: CountRatio
    expected_facts: CountRatio
    uncited_answers: CountRatio
    fidelity_unsupported: CountRatio
    retrieval_latency_ms_sum: float
    retrieval_latency_count: int
    labels_complete: bool

    def __post_init__(self) -> None:
        _require_duration(self.retrieval_latency_ms_sum)
        _require_count(self.retrieval_latency_count, "retrieval_latency_count")
        if not isinstance(self.labels_complete, bool):
            message = "labels_complete debe ser booleano"
            raise InvalidStateError(message)


@dataclass(frozen=True)
class FailedCase:
    """Caso que no cumplió. Solo el id y los motivos, sin el documento."""

    case_id: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        if not self.reasons:
            message = "Un caso fallido necesita un motivo"
            raise InvalidStateError(message)


@dataclass(frozen=True)
class CaseScore:
    """Resultado medido de un caso de test. No guarda la respuesta ni el extracto."""

    case_id: str
    answerable: bool
    abstained: bool
    relevant_rank: int | None
    citation_correct: bool
    facts_expected: bool
    facts_present: bool
    uncited: bool
    fidelity: FidelityLabel | None
    suggested: FidelityLabel | None
    retrieval_ms: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationDataset:
    """Conjunto versionado de una empresa."""

    id: UUID
    tenant_id: UUID
    name: str
    version: str
    status: EvaluationDatasetStatus
    content_sha256: str
    case_count: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.version, "version")
        _require_sha256(self.content_sha256, "content_sha256")
        _require_count(self.case_count, "case_count")
        if self.case_count < 1:
            message = "El conjunto necesita al menos un caso"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class EvaluationCase:
    """Caso de oro. La etiqueta humana no la escribe el modelo."""

    id: UUID
    tenant_id: UUID
    dataset_id: UUID
    case_key: str
    version: str
    status: EvaluationCaseStatus
    split: EvaluationSplit
    question: str
    answerable: bool
    expected_sources: tuple[str, ...]
    expected_pages: tuple[int, ...]
    expected_facts: tuple[str, ...]
    expected_patterns: tuple[str, ...]
    tags: tuple[str, ...]
    fidelity_label: FidelityLabel | None
    label_version: int
    labeled_answer_sha256: str | None

    def __post_init__(self) -> None:
        _require_text(self.case_key, "case_key")
        _require_text(self.version, "version")
        _require_text(self.question, "question")
        if len(self.question) > _MAX_QUESTION_CHARS:
            message = "La pregunta del caso supera el máximo"
            raise InvalidStateError(message)
        if not isinstance(self.answerable, bool):
            message = "answerable debe ser booleano"
            raise InvalidStateError(message)
        if not self.tags:
            message = "El caso necesita al menos un tag"
            raise InvalidStateError(message)
        if (
            isinstance(self.label_version, bool)
            or not isinstance(self.label_version, int)
            or self.label_version < 1
        ):
            message = "La versión de la etiqueta humana no es válida"
            raise InvalidStateError(message)
        labeled = self.fidelity_label is not None
        hashed = self.labeled_answer_sha256 is not None
        if labeled != hashed:
            message = "La etiqueta humana y su huella van juntas"
            raise InvalidStateError(message)
        if self.labeled_answer_sha256 is not None:
            _require_sha256(self.labeled_answer_sha256, "labeled_answer_sha256")
        for page in self.expected_pages:
            _require_page(page)


@dataclass(frozen=True)
class EvaluationRun:
    """Corrida con la configuración congelada y las métricas del test."""

    id: UUID
    tenant_id: UUID
    dataset_id: UUID
    version: str
    status: EvaluationRunStatus
    config: EvaluationConfig
    metrics: EvaluationMetrics
    failed_cases: tuple[FailedCase, ...]
    isolation_ok: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.version, "version")
        if not isinstance(self.isolation_ok, bool):
            message = "isolation_ok debe ser booleano"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class GateDecision:
    """Resultado del gate. `passed` solo si no hay motivos."""

    passed: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            message = "passed debe ser booleano"
            raise InvalidStateError(message)
        if self.passed != (not self.reasons):
            message = "El gate aprobado no puede traer motivos"
            raise InvalidStateError(message)


def require_split_balance(counts: Mapping[EvaluationSplit, int]) -> None:
    """Exige los dos splits y al menos un 20 % de los casos en cada uno."""
    total = sum(counts.values())
    if total < 1:
        message = "El conjunto no tiene casos"
        raise EvaluationRejected(message)
    for split in EvaluationSplit:
        count = counts.get(split, 0)
        if count * MIN_SPLIT_FACTOR < total:
            message = "La distribución de splits no alcanza el mínimo"
            raise EvaluationRejected(message)


def hit_at_k(ranks: Sequence[int | None], *, k: int) -> CountRatio:
    """Aciertos con el primer relevante dentro de las primeras k posiciones."""
    _require_top_k(k)
    hits = 0
    for rank in ranks:
        _require_rank(rank)
        if rank is not None and rank <= k:
            hits += 1
    return CountRatio(numerator=hits, denominator=len(ranks))


def mean_reciprocal_rank(ranks: Sequence[int | None]) -> ReciprocalMean:
    """MRR como fracción. Un caso sin relevante aporta cero y sí cuenta."""
    total = Fraction(0)
    for rank in ranks:
        _require_rank(rank)
        if rank is not None:
            total += Fraction(1, rank)
    if total.denominator == 0:
        message = "El MRR no es válido"
        raise InvalidStateError(message)
    return ReciprocalMean(
        reciprocal_numerator=total.numerator,
        reciprocal_denominator=total.denominator,
        count=len(ranks),
    )


def first_relevant_rank(sources: Sequence[str], expected: Sequence[str]) -> int | None:
    """Posición 1-based de la primera fuente esperada, o None si no está."""
    wanted = set(expected)
    for index, source in enumerate(sources, start=1):
        if source in wanted:
            return index
    return None


def calibration_observations(
    items: Sequence[SplitObservation],
) -> tuple[CalibrationObservation, ...]:
    """Deja fuera el split de test antes de mirar puntuaciones."""
    return tuple(item.observation for item in items if item.split is EvaluationSplit.CALIBRATION)


def freeze_threshold(items: Sequence[SplitObservation]) -> float:
    """Elige el umbral solo con calibración y lo deja fijo."""
    selected = calibration_observations(items)
    return calibrate_min_retrieval_score(selected)


def calibrate_min_retrieval_score(observations: Sequence[CalibrationObservation]) -> float:
    """Rejilla de centésimas. A igualdad de aciertos, se queda el umbral más alto."""
    if not observations:
        message = "La calibración no tiene casos"
        raise EvaluationRejected(message)
    best_count = -1
    best_hundredths = 0
    for hundredths in range(101):
        correct = sum(1 for item in observations if _decision_correct(item, hundredths))
        if correct > best_count or (correct == best_count and hundredths > best_hundredths):
            best_count = correct
            best_hundredths = hundredths
    return best_hundredths / 100


def suggest_fidelity(
    *,
    facts_present: bool,
    citation_correct: bool,
    abstained: bool,
) -> FidelityLabel:
    """Sugerencia local. El gate no la lee."""
    if abstained:
        return FidelityLabel.UNSUPPORTED
    if facts_present and citation_correct:
        return FidelityLabel.SUPPORTED
    if facts_present or citation_correct:
        return FidelityLabel.PARTIALLY_SUPPORTED
    return FidelityLabel.UNSUPPORTED


def score_case(
    case: EvaluationCase,
    hits: Sequence[CalibrationHit],
    *,
    abstained: bool,
    answer_text: str,
    citation_sources: Sequence[str],
    citation_pages: Sequence[int],
    retrieval_ms: float,
    k: int = HIT_AT_K,
    suggest: bool = False,
) -> CaseScore:
    """Mide un caso de test. El texto de la respuesta no sale en el resultado."""
    _require_duration(retrieval_ms)
    rank = first_relevant_rank(tuple(hit.source for hit in hits), case.expected_sources)
    citation_ok = _citation_correct(
        case,
        abstained=abstained,
        citation_sources=citation_sources,
        citation_pages=citation_pages,
    )
    expected = bool(case.expected_facts or case.expected_patterns)
    present = _facts_present(case, answer_text, abstained=abstained) if expected else False
    uncited = (not abstained) and not citation_sources
    fidelity = _effective_label(case, answer_text, abstained=abstained)
    suggested = (
        suggest_fidelity(
            facts_present=present,
            citation_correct=citation_ok,
            abstained=abstained,
        )
        if suggest
        else None
    )
    reasons = _reasons(
        case,
        rank=rank,
        k=k,
        citation_ok=citation_ok,
        expected=expected,
        present=present,
        uncited=uncited,
        abstained=abstained,
        fidelity=fidelity,
    )
    return CaseScore(
        case_id=case.case_key,
        answerable=case.answerable,
        abstained=abstained,
        relevant_rank=rank,
        citation_correct=citation_ok,
        facts_expected=expected,
        facts_present=present,
        uncited=uncited,
        fidelity=fidelity,
        suggested=suggested,
        retrieval_ms=retrieval_ms,
        reasons=reasons,
    )


def compute_metrics(scores: Sequence[CaseScore], *, k: int = HIT_AT_K) -> EvaluationMetrics:
    """Agrega solo casos que el llamador ya filtró al split de test."""
    answerable = tuple(item for item in scores if item.answerable)
    unanswerable = tuple(item for item in scores if not item.answerable)
    ranks = tuple(item.relevant_rank for item in answerable)
    citations = sum(1 for item in answerable if item.citation_correct)
    abstained = sum(1 for item in unanswerable if item.abstained)
    fact_cases = tuple(item for item in scores if item.facts_expected)
    facts = sum(1 for item in fact_cases if item.facts_present)
    answered = tuple(item for item in scores if not item.abstained)
    uncited = sum(1 for item in answered if item.uncited)
    labeled = tuple(item for item in answered if item.fidelity is not None)
    unsupported = sum(1 for item in labeled if item.fidelity is FidelityLabel.UNSUPPORTED)
    labels_complete = all(item.fidelity is not None for item in answered)
    latency = sum(item.retrieval_ms for item in scores)
    return EvaluationMetrics(
        hit_at_5=hit_at_k(ranks, k=k),
        mrr=mean_reciprocal_rank(ranks),
        citation=CountRatio(numerator=citations, denominator=len(answerable)),
        unanswerable_abstention=CountRatio(numerator=abstained, denominator=len(unanswerable)),
        expected_facts=CountRatio(numerator=facts, denominator=len(fact_cases)),
        uncited_answers=CountRatio(numerator=uncited, denominator=len(answered)),
        fidelity_unsupported=CountRatio(numerator=unsupported, denominator=len(labeled)),
        retrieval_latency_ms_sum=latency,
        retrieval_latency_count=len(scores),
        labels_complete=labels_complete,
    )


def failed_cases(scores: Sequence[CaseScore]) -> tuple[FailedCase, ...]:
    """Casos con algún motivo, en el orden recibido."""
    return tuple(
        FailedCase(case_id=item.case_id, reasons=item.reasons) for item in scores if item.reasons
    )


def config_fingerprint(config: EvaluationConfig) -> str:
    """SHA-256 de la configuración canónica, incluido el umbral congelado."""
    payload = json.dumps(_config_payload(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decide_gate(
    baseline: EvaluationMetrics,
    candidate: EvaluationMetrics,
    *,
    baseline_isolation_ok: bool,
    candidate_isolation_ok: bool,
    tolerance: float = DEFAULT_REGRESSION_TOLERANCE,
) -> GateDecision:
    """Aplica umbrales absolutos, tolerancia frente al baseline y aislamiento."""
    _require_tolerance(tolerance)
    reasons: list[str] = []
    if not candidate_isolation_ok or not baseline_isolation_ok:
        reasons.append(GateFailure.ISOLATION.value)
    if not candidate.labels_complete or not baseline.labels_complete:
        reasons.append(GateFailure.LABELS_INCOMPLETE.value)
    _absolute(candidate, reasons)
    _regression(baseline, candidate, tolerance=tolerance, reasons=reasons)
    unique = tuple(dict.fromkeys(reasons))
    return GateDecision(passed=not unique, reasons=unique)


def _absolute(metrics: EvaluationMetrics, reasons: list[str]) -> None:
    hit = metrics.hit_at_5.value
    citation = metrics.citation.value
    abstention = metrics.unanswerable_abstention.value
    if hit is None or citation is None or abstention is None:
        reasons.append(GateFailure.METRIC_UNDEFINED.value)
    if hit is not None and hit < MIN_HIT_RATE - _EPSILON:
        reasons.append(GateFailure.HIT_ABSOLUTE.value)
    if citation is not None and citation < MIN_CITATION_RATE - _EPSILON:
        reasons.append(GateFailure.CITATION_ABSOLUTE.value)
    if abstention is not None and abstention < MIN_ABSTENTION_RATE - _EPSILON:
        reasons.append(GateFailure.ABSTENTION_ABSOLUTE.value)
    unsupported = _unsupported_value(metrics)
    if unsupported is None:
        reasons.append(GateFailure.METRIC_UNDEFINED.value)
    elif unsupported >= MAX_UNSUPPORTED_RATE - _EPSILON:
        reasons.append(GateFailure.UNSUPPORTED_ABSOLUTE.value)


def _regression(
    baseline: EvaluationMetrics,
    candidate: EvaluationMetrics,
    *,
    tolerance: float,
    reasons: list[str],
) -> None:
    pairs = (
        (baseline.hit_at_5.value, candidate.hit_at_5.value, GateFailure.HIT_REGRESSION, False),
        (
            baseline.citation.value,
            candidate.citation.value,
            GateFailure.CITATION_REGRESSION,
            False,
        ),
        (
            baseline.unanswerable_abstention.value,
            candidate.unanswerable_abstention.value,
            GateFailure.ABSTENTION_REGRESSION,
            False,
        ),
    )
    for base, current, code, rises in pairs:
        if base is None or current is None:
            continue
        if _moved(base, current, tolerance=tolerance, rises=rises):
            reasons.append(code.value)
    base_unsupported = _unsupported_value(baseline)
    current_unsupported = _unsupported_value(candidate)
    if base_unsupported is None or current_unsupported is None:
        return
    if _moved(base_unsupported, current_unsupported, tolerance=tolerance, rises=True):
        reasons.append(GateFailure.UNSUPPORTED_REGRESSION.value)


def _unsupported_value(metrics: EvaluationMetrics) -> float | None:
    if not metrics.labels_complete:
        return None
    if metrics.fidelity_unsupported.denominator == 0:
        return 0.0
    return metrics.fidelity_unsupported.value


def _moved(baseline: float, candidate: float, *, tolerance: float, rises: bool) -> bool:
    if rises:
        return candidate > baseline + tolerance + _EPSILON
    return candidate < baseline - tolerance - _EPSILON


def _decision_correct(observation: CalibrationObservation, hundredths: int) -> bool:
    kept = tuple(hit for hit in observation.hits if _keeps(hit.score, hundredths))
    if observation.answerable:
        wanted = set(observation.expected_sources)
        return any(hit.source in wanted for hit in kept)
    return not kept


def _keeps(score: float, hundredths: int) -> bool:
    return score >= (hundredths / 100) - _EPSILON


def _citation_correct(
    case: EvaluationCase,
    *,
    abstained: bool,
    citation_sources: Sequence[str],
    citation_pages: Sequence[int],
) -> bool:
    if not case.answerable:
        return True
    if abstained:
        return False
    if not set(citation_sources).intersection(case.expected_sources):
        return False
    pages_ok = not case.expected_pages or bool(
        set(citation_pages).intersection(case.expected_pages)
    )
    return pages_ok


def _facts_present(case: EvaluationCase, answer_text: str, *, abstained: bool) -> bool:
    if abstained:
        return False
    folded = answer_text.casefold()
    if any(fact.casefold() not in folded for fact in case.expected_facts):
        return False
    for pattern in case.expected_patterns:
        try:
            matched = re.search(pattern, answer_text)
        except re.error as error:
            message = "El patrón esperado no es válido"
            raise InvalidStateError(message) from error
        if matched is None:
            return False
    return True


def _effective_label(
    case: EvaluationCase,
    answer_text: str,
    *,
    abstained: bool,
) -> FidelityLabel | None:
    if abstained or case.fidelity_label is None or case.labeled_answer_sha256 is None:
        return None
    if answer_sha256(answer_text) != case.labeled_answer_sha256:
        return None
    return case.fidelity_label


def _reasons(
    case: EvaluationCase,
    *,
    rank: int | None,
    k: int,
    citation_ok: bool,
    expected: bool,
    present: bool,
    uncited: bool,
    abstained: bool,
    fidelity: FidelityLabel | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if case.answerable and (rank is None or rank > k):
        reasons.append(CaseFailure.HIT.value)
    if case.answerable and not citation_ok:
        reasons.append(CaseFailure.CITATION.value)
    if not case.answerable and not abstained:
        reasons.append(CaseFailure.ABSTENTION.value)
    if expected and not present:
        reasons.append(CaseFailure.FACTS.value)
    if uncited:
        reasons.append(CaseFailure.UNCITED.value)
    if not abstained and fidelity is None:
        reasons.append(CaseFailure.MISSING_LABEL.value)
    if fidelity is FidelityLabel.UNSUPPORTED:
        reasons.append(CaseFailure.FIDELITY.value)
    return tuple(reasons)


def _config_payload(config: EvaluationConfig) -> dict[str, str | int]:
    return {
        "corpus_sha256": config.corpus_sha256,
        "parser_version": config.parser_version,
        "chunker_version": config.chunker_version,
        "embedding_version": config.embedding_version,
        "index_version": config.index_version,
        "top_k": config.top_k,
        "min_retrieval_score": f"{config.min_retrieval_score:.2f}",
        "prompt_version": config.prompt_version,
        "llm_version": config.llm_version,
        "dataset_version": config.dataset_version,
    }


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        message = f"El campo {field} de la evaluación no es válido"
        raise InvalidStateError(message)


def _require_count(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = f"El conteo {field} de la evaluación no es válido"
        raise InvalidStateError(message)


def _require_page(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = "La página esperada no es válida"
        raise InvalidStateError(message)


def _require_rank(rank: int | None) -> None:
    if rank is None:
        return
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        message = "El rango relevante no es válido"
        raise InvalidStateError(message)


def _require_top_k(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        message = "top_k de la evaluación no es válido"
        raise InvalidStateError(message)


def _require_unit_score(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "La puntuación de la evaluación no es válida"
        raise InvalidStateError(message)
    score = float(value)
    if score != score or not 0.0 <= score <= 1.0:
        message = "La puntuación de la evaluación no es válida"
        raise InvalidStateError(message)


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != _SHA256_WIDTH:
        message = f"La huella {field} no es válida"
        raise InvalidStateError(message)
    if any(character not in "0123456789abcdef" for character in value):
        message = f"La huella {field} no es válida"
        raise InvalidStateError(message)


def _require_duration(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "La latencia de la evaluación no es válida"
        raise InvalidStateError(message)
    duration = float(value)
    if duration != duration or duration < 0 or duration == float("inf"):
        message = "La latencia de la evaluación no es válida"
        raise InvalidStateError(message)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{field_name} debe ser timezone-aware en UTC"
        raise InvalidStateError(message)
    if value.utcoffset() != timedelta(0):
        message = f"{field_name} debe tener offset UTC (0)"
        raise InvalidStateError(message)


def _require_tolerance(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "La tolerancia del gate no es válida"
        raise EvaluationRejected(message)
    tolerance = float(value)
    if tolerance != tolerance or not 0.0 <= tolerance <= 1.0:
        message = "La tolerancia del gate no es válida"
        raise EvaluationRejected(message)
