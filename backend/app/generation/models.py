"""Respuesta fundamentada y citas construidas en código."""

from dataclasses import dataclass

from app.retrieval.sample_search import LocalSearchHit

PUBLIC_ABSTENTION = "No está en los documentos disponibles"
RETRIEVAL_VERSION = "retrieval-v2"
PROMPT_VERSION = "prompt-v4"


@dataclass(frozen=True)
class Citation:
    """Cita de un fragmento recuperado. El modelo no redacta esta referencia."""

    chunk_id: str
    source: str
    page_start: int
    page_end: int
    section: str | None
    excerpt: str


@dataclass(frozen=True)
class GroundedAnswer:
    """Respuesta en español, con abstención y versiones del proceso."""

    answer: str
    abstained: bool
    citations: tuple[Citation, ...]
    retrieval_version: str
    prompt_version: str
    model_version: str


@dataclass(frozen=True)
class GroundedRun:
    """Respuesta más las puntuaciones y latencias de la ejecución."""

    answer: GroundedAnswer
    retrieved: tuple[LocalSearchHit, ...]
    retrieval_ms: float
    generation_ms: float
    llm_called: bool
