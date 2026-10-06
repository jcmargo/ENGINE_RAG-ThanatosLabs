"""Reportes JSON y Markdown. No copian preguntas ni documentos."""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.domain.errors import EvaluationRejected, InvalidStateError
from app.domain.evaluation import (
    EvaluationConfig,
    EvaluationMetrics,
    GateDecision,
    decide_gate,
)
from app.domain.evaluation_codec import (
    failed_from_payload,
    failed_payload,
    metrics_from_payload,
    metrics_payload,
)
from app.evaluation.runner import EvaluationReport


def report_payload(report: EvaluationReport) -> dict[str, object]:
    """JSON de la corrida. Solo versiones, conteos e ids fallidos."""
    return {
        "profile": report.profile,
        "dataset_version": report.dataset_version,
        "run_version": report.run_version,
        "config": _config_payload(report.config),
        "metrics": metrics_payload(report.metrics),
        "failed_cases": failed_payload(report.failed_cases),
        "isolation_ok": report.isolation_ok,
    }


def report_text(report: EvaluationReport) -> str:
    """Markdown legible de la misma corrida."""
    return _markdown(
        title=f"Evaluación {report.dataset_version}",
        report=report,
        decision=None,
    )


def comparison_payload(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    decision: GateDecision,
) -> dict[str, object]:
    """Diferencia entre dos corridas, sin el texto de los casos."""
    return {
        "passed": decision.passed,
        "reasons": list(decision.reasons),
        "baseline_version": baseline.run_version,
        "candidate_version": candidate.run_version,
        "baseline_config": _config_payload(baseline.config),
        "candidate_config": _config_payload(candidate.config),
        "failed_cases": failed_payload(candidate.failed_cases),
        "deltas": _deltas(baseline.metrics, candidate.metrics),
        "isolation_ok": candidate.isolation_ok,
    }


def comparison_text(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    decision: GateDecision,
) -> str:
    """Markdown de la comparación."""
    lines = [
        f"# Comparación {candidate.dataset_version}",
        "",
        f"Baseline: {baseline.run_version}",
        f"Candidata: {candidate.run_version}",
        f"Resultado: {'aprobada' if decision.passed else 'rechazada'}",
        "",
        "## Motivos",
        *([f"- {reason}" for reason in decision.reasons] or ["- ninguno"]),
        "",
        "## Casos fallidos",
        *_failed_lines(candidate),
        "",
        "## Versiones",
        *_version_lines(candidate.config),
        "",
    ]
    return "\n".join(lines)


def dumps(payload: Mapping[str, object]) -> str:
    """JSON estable, sin texto de documentos."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_report(text: str) -> EvaluationReport:
    """Lee un reporte propio. Ignora claves que no forman parte del contrato."""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as error:
        message = "El reporte no es válido"
        raise EvaluationRejected(message) from error
    if not isinstance(loaded, dict):
        message = "El reporte no es válido"
        raise EvaluationRejected(message)
    try:
        return _report_from(loaded)
    except (InvalidStateError, EvaluationRejected, TypeError, ValueError) as error:
        message = "El reporte no es válido"
        raise EvaluationRejected(message) from error


def gate_decision(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    *,
    tolerance: float,
) -> GateDecision:
    """Aplica el gate a dos reportes ya cargados."""
    return decide_gate(
        baseline.metrics,
        candidate.metrics,
        baseline_isolation_ok=baseline.isolation_ok,
        candidate_isolation_ok=candidate.isolation_ok,
        tolerance=tolerance,
    )


def _report_from(raw: dict[str, object]) -> EvaluationReport:
    profile = _text(raw.get("profile"))
    dataset_version = _text(raw.get("dataset_version"))
    run_version = _text(raw.get("run_version"))
    isolation = raw.get("isolation_ok")
    if not isinstance(isolation, bool):
        message = "El reporte no es válido"
        raise EvaluationRejected(message)
    config_raw = raw.get("config")
    if not isinstance(config_raw, dict):
        message = "El reporte no es válido"
        raise EvaluationRejected(message)
    return EvaluationReport(
        profile=profile,
        dataset_version=dataset_version,
        run_version=run_version,
        config=_config_from(config_raw),
        metrics=metrics_from_payload(raw.get("metrics")),
        failed_cases=failed_from_payload(raw.get("failed_cases")),
        isolation_ok=isolation,
    )


def _config_payload(config: EvaluationConfig) -> dict[str, object]:
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


def _config_from(raw: dict[str, object]) -> EvaluationConfig:
    score = _text(raw.get("min_retrieval_score"))
    return EvaluationConfig(
        corpus_sha256=_text(raw.get("corpus_sha256")),
        parser_version=_text(raw.get("parser_version")),
        chunker_version=_text(raw.get("chunker_version")),
        embedding_version=_text(raw.get("embedding_version")),
        index_version=_text(raw.get("index_version")),
        top_k=_int(raw.get("top_k")),
        min_retrieval_score=float(score),
        prompt_version=_text(raw.get("prompt_version")),
        llm_version=_text(raw.get("llm_version")),
        dataset_version=_text(raw.get("dataset_version")),
    )


def _markdown(
    *,
    title: str,
    report: EvaluationReport,
    decision: GateDecision | None,
) -> str:
    metrics = report.metrics
    lines = [
        f"# {title}",
        "",
        f"Perfil: {report.profile}",
        f"Versión de corrida: {report.run_version}",
        "",
        "## Métricas",
        f"- Hit@5: {_ratio(metrics.hit_at_5.numerator, metrics.hit_at_5.denominator)}",
        (
            "- MRR: "
            f"{metrics.mrr.reciprocal_numerator}/{metrics.mrr.reciprocal_denominator}"
            f" sobre {metrics.mrr.count}"
        ),
        f"- Citas: {_ratio(metrics.citation.numerator, metrics.citation.denominator)}",
        "- Abstención sin respuesta: "
        + _ratio(
            metrics.unanswerable_abstention.numerator,
            metrics.unanswerable_abstention.denominator,
        ),
        "- Sin soporte: "
        + _ratio(
            metrics.fidelity_unsupported.numerator,
            metrics.fidelity_unsupported.denominator,
        ),
        (
            "- Hechos: "
            f"{_ratio(metrics.expected_facts.numerator, metrics.expected_facts.denominator)}"
        ),
        (
            "- Sin cita: "
            f"{_ratio(metrics.uncited_answers.numerator, metrics.uncited_answers.denominator)}"
        ),
        "",
        "## Casos fallidos",
        *_failed_lines(report),
        "",
        "## Versiones",
        *_version_lines(report.config),
        "",
        f"Aislamiento: {'correcto' if report.isolation_ok else 'fallido'}",
        f"Etiquetas humanas completas: {'sí' if metrics.labels_complete else 'no'}",
        "",
    ]
    if decision is not None:
        lines.extend(
            [
                "## Gate",
                *([f"- {reason}" for reason in decision.reasons] or ["- ninguno"]),
                "",
            ]
        )
    return "\n".join(lines)


def _failed_lines(report: EvaluationReport) -> tuple[str, ...]:
    if not report.failed_cases:
        return ("- ninguno",)
    return tuple(f"- {item.case_id}: {', '.join(item.reasons)}" for item in report.failed_cases)


def _version_lines(config: EvaluationConfig) -> tuple[str, ...]:
    return (
        f"- parser: {config.parser_version}",
        f"- chunker: {config.chunker_version}",
        f"- embeddings: {config.embedding_version}",
        f"- índice: {config.index_version}",
        f"- prompt: {config.prompt_version}",
        f"- llm: {config.llm_version}",
        f"- umbral: {config.min_retrieval_score:.2f}",
        f"- top_k: {config.top_k}",
    )


def _deltas(baseline: EvaluationMetrics, candidate: EvaluationMetrics) -> dict[str, object]:
    return {
        "hit_at_5": _count_delta(baseline.hit_at_5.numerator, candidate.hit_at_5.numerator),
        "citation": _count_delta(baseline.citation.numerator, candidate.citation.numerator),
        "unanswerable_abstention": _count_delta(
            baseline.unanswerable_abstention.numerator,
            candidate.unanswerable_abstention.numerator,
        ),
        "fidelity_unsupported": _count_delta(
            baseline.fidelity_unsupported.numerator,
            candidate.fidelity_unsupported.numerator,
        ),
    }


def _count_delta(baseline: int, candidate: int) -> dict[str, int]:
    return {"baseline": baseline, "candidate": candidate}


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        message = "El reporte no es válido"
        raise EvaluationRejected(message)
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        message = "El reporte no es válido"
        raise EvaluationRejected(message)
    return value
