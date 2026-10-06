"""Métricas Prometheus de baja cardinalidad. Sin ids de usuario, documento o empresa."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_HTTP_REQUESTS = Counter(
    "engine_rag_http_requests_total",
    "Solicitudes HTTP terminadas.",
    ("method", "route", "status_class"),
)
_HTTP_LATENCY = Histogram(
    "engine_rag_http_request_duration_seconds",
    "Latencia HTTP. El histograma permite calcular p95.",
    ("method", "route"),
)
_HTTP_ERRORS = Counter(
    "engine_rag_http_errors_total",
    "Respuestas HTTP de error.",
    ("method", "route", "status_class"),
)
_JOBS = Counter(
    "engine_rag_ingestion_jobs_total",
    "Trabajos de ingesta terminados.",
    ("job_type", "result"),
)
_QUEUE = Gauge(
    "engine_rag_ingestion_queue",
    "Trabajos en cola por estado acotado.",
    ("status",),
)
_INGESTION = Counter(
    "engine_rag_ingestion_stage_total",
    "Etapas de ingesta.",
    ("stage",),
)
_RETRIEVAL = Counter(
    "engine_rag_retrieval_total",
    "Recuperaciones terminadas.",
    ("result",),
)
_RETRIEVAL_LATENCY = Histogram(
    "engine_rag_retrieval_duration_seconds",
    "Latencia de recuperación.",
)
_GENERATION = Counter(
    "engine_rag_generation_total",
    "Generaciones terminadas.",
    ("result",),
)
_GENERATION_LATENCY = Histogram(
    "engine_rag_generation_duration_seconds",
    "Latencia de generación.",
)
_TOKENS = Counter(
    "engine_rag_tokens_estimated_total",
    "Tokens estimados como caracteres divididos por cuatro.",
    ("stage",),
)
_DEPENDENCY = Gauge(
    "engine_rag_dependency_up",
    "Dependencia disponible (1) o no (0).",
    ("dependency",),
)

_QUEUE_STATUSES = ("pending", "leased", "succeeded", "failed", "dead")


def observe_http(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    """Cuenta la solicitud y su latencia. La ruta ya viene templada."""
    status_class = f"{status_code // 100}xx"
    _HTTP_REQUESTS.labels(method=method, route=route, status_class=status_class).inc()
    _HTTP_LATENCY.labels(method=method, route=route).observe(max(duration_seconds, 0.0))
    if status_code >= 400:
        _HTTP_ERRORS.labels(method=method, route=route, status_class=status_class).inc()


def observe_job(job_type: str, result: str) -> None:
    """Resultado de un trabajo. El tipo y el resultado son conjuntos cerrados."""
    _JOBS.labels(job_type=job_type, result=result).inc()


def set_queue_counts(counts: dict[str, int]) -> None:
    """Publica la cola. Los estados que no vienen quedan en cero."""
    for status in _QUEUE_STATUSES:
        _QUEUE.labels(status=status).set(counts.get(status, 0))


def observe_ingestion(stage: str) -> None:
    """Cuenta una etapa de ingesta."""
    _INGESTION.labels(stage=stage).inc()


def observe_retrieval(*, kept: int, duration_ms: float) -> None:
    """Cuenta la recuperación sin identificar el documento."""
    _RETRIEVAL.labels(result="hit" if kept else "miss").inc()
    _RETRIEVAL_LATENCY.observe(max(duration_ms, 0.0) / 1000)


def observe_generation(*, abstained: bool, duration_ms: float, characters: int) -> None:
    """Cuenta la generación y una estimación de tokens del texto producido."""
    _GENERATION.labels(result="abstained" if abstained else "answered").inc()
    _GENERATION_LATENCY.observe(max(duration_ms, 0.0) / 1000)
    observe_tokens("generation", characters)


def observe_tokens(stage: str, characters: int) -> None:
    """Estimación `len(texto) // 4`. No guarda el texto."""
    if characters > 0:
        _TOKENS.labels(stage=stage).inc(characters // 4)


def observe_dependency(name: str, up: bool) -> None:
    """Disponibilidad de Postgres, Qdrant u Ollama."""
    _DEPENDENCY.labels(dependency=name).set(1 if up else 0)
