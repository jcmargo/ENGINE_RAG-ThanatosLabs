"""Serialización auditable de métricas. No incluye preguntas ni documentos."""

from __future__ import annotations

from typing import TypeGuard

from app.domain.enums import FidelityLabel
from app.domain.errors import InvalidStateError
from app.domain.evaluation import (
    CountRatio,
    EvaluationConfig,
    EvaluationMetrics,
    FailedCase,
    ReciprocalMean,
)

_COUNT_KEYS = (
    "hit_at_5",
    "citation",
    "unanswerable_abstention",
    "expected_facts",
    "uncited_answers",
    "fidelity_unsupported",
)


def metrics_payload(metrics: EvaluationMetrics) -> dict[str, object]:
    """Numeradores y denominadores. El porcentaje no es la fuente de verdad."""
    payload: dict[str, object] = {key: _count_payload(getattr(metrics, key)) for key in _COUNT_KEYS}
    payload["mrr"] = {
        "reciprocal_numerator": metrics.mrr.reciprocal_numerator,
        "reciprocal_denominator": metrics.mrr.reciprocal_denominator,
        "count": metrics.mrr.count,
    }
    payload["retrieval_latency_ms"] = {
        "sum_ms": metrics.retrieval_latency_ms_sum,
        "count": metrics.retrieval_latency_count,
    }
    payload["labels_complete"] = metrics.labels_complete
    return payload


def metrics_from_payload(payload: object) -> EvaluationMetrics:
    """Reconstruye métricas. Rechaza un objeto que no trae los conteos."""
    raw = _object(payload)
    counts = {key: _count_from(raw.get(key)) for key in _COUNT_KEYS}
    mrr = _object(raw.get("mrr"))
    latency = _object(raw.get("retrieval_latency_ms"))
    labels = raw.get("labels_complete")
    if not isinstance(labels, bool):
        message = "Las métricas guardadas no son válidas"
        raise InvalidStateError(message)
    return EvaluationMetrics(
        hit_at_5=counts["hit_at_5"],
        mrr=ReciprocalMean(
            reciprocal_numerator=_int(mrr.get("reciprocal_numerator")),
            reciprocal_denominator=_int(mrr.get("reciprocal_denominator")),
            count=_int(mrr.get("count")),
        ),
        citation=counts["citation"],
        unanswerable_abstention=counts["unanswerable_abstention"],
        expected_facts=counts["expected_facts"],
        uncited_answers=counts["uncited_answers"],
        fidelity_unsupported=counts["fidelity_unsupported"],
        retrieval_latency_ms_sum=_float(latency.get("sum_ms")),
        retrieval_latency_count=_int(latency.get("count")),
        labels_complete=labels,
    )


def config_from_columns(
    *,
    corpus_sha256: str,
    parser_version: str,
    chunker_version: str,
    embedding_version: str,
    index_version: str,
    top_k: int,
    min_retrieval_score: float,
    prompt_version: str,
    llm_version: str,
    dataset_version: str,
) -> EvaluationConfig:
    """Arma la configuración desde columnas, no desde un blob opaco."""
    return EvaluationConfig(
        corpus_sha256=corpus_sha256,
        parser_version=parser_version,
        chunker_version=chunker_version,
        embedding_version=embedding_version,
        index_version=index_version,
        top_k=top_k,
        min_retrieval_score=min_retrieval_score,
        prompt_version=prompt_version,
        llm_version=llm_version,
        dataset_version=dataset_version,
    )


def failed_payload(cases: tuple[FailedCase, ...]) -> list[dict[str, object]]:
    """Ids y motivos. Sin pregunta ni extracto."""
    return [{"id": item.case_id, "reasons": list(item.reasons)} for item in cases]


def failed_from_payload(payload: object) -> tuple[FailedCase, ...]:
    """Reconstruye los casos fallidos guardados."""
    if not isinstance(payload, list):
        message = "Los casos fallidos guardados no son válidos"
        raise InvalidStateError(message)
    rows: list[FailedCase] = []
    for item in payload:
        raw = _object(item)
        case_id = raw.get("id")
        reasons = raw.get("reasons")
        if not isinstance(case_id, str) or not isinstance(reasons, list):
            message = "Los casos fallidos guardados no son válidos"
            raise InvalidStateError(message)
        if any(not isinstance(reason, str) for reason in reasons):
            message = "Los casos fallidos guardados no son válidos"
            raise InvalidStateError(message)
        rows.append(FailedCase(case_id=case_id, reasons=tuple(reasons)))
    return tuple(rows)


def string_tuple(payload: object, field: str) -> tuple[str, ...]:
    """Lista JSON de textos. No acepta otro tipo."""
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        message = f"El campo {field} guardado no es válido"
        raise InvalidStateError(message)
    return tuple(payload)


def int_tuple(payload: object, field: str) -> tuple[int, ...]:
    """Lista JSON de enteros."""
    if not isinstance(payload, list):
        message = f"El campo {field} guardado no es válido"
        raise InvalidStateError(message)
    pages: list[int] = []
    for item in payload:
        if not _plain_int(item):
            message = f"El campo {field} guardado no es válido"
            raise InvalidStateError(message)
        pages.append(item)
    return tuple(pages)


def fidelity_from_column(value: str | None) -> FidelityLabel | None:
    """Etiqueta humana guardada, o ninguna."""
    if value is None:
        return None
    try:
        return FidelityLabel(value)
    except ValueError as error:
        message = "La etiqueta humana guardada no es válida"
        raise InvalidStateError(message) from error


def _count_payload(ratio: CountRatio) -> dict[str, int]:
    return {"numerator": ratio.numerator, "denominator": ratio.denominator}


def _count_from(payload: object) -> CountRatio:
    raw = _object(payload)
    return CountRatio(
        numerator=_int(raw.get("numerator")),
        denominator=_int(raw.get("denominator")),
    )


def _object(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        message = "El objeto guardado de la evaluación no es válido"
        raise InvalidStateError(message)
    return payload


def _plain_int(value: object) -> TypeGuard[int]:
    return not isinstance(value, bool) and isinstance(value, int)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        message = "Un conteo guardado de la evaluación no es válido"
        raise InvalidStateError(message)
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "Una latencia guardada de la evaluación no es válida"
        raise InvalidStateError(message)
    return float(value)
