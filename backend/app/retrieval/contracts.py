"""Contratos de la recuperación de negocio. La pregunta no nombra la empresa."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.entities import RetrievableVersion, TenantContext
from app.domain.errors import InvalidStateError
from app.retrieval.vectors import VectorHit

DEFAULT_TOP_K = 5
MIN_TOP_K = 1
MAX_TOP_K = 20
DEFAULT_MAX_QUESTION_CHARS = 2000
OVERSAMPLE_FACTOR = 3
MIN_CANDIDATES = 20
MAX_SEARCH_ROUNDS = 2
MAX_CANDIDATES = 200
# Un rótulo suelto, como «EJEMPLO 7.4.4», puede quedar delante del enunciado.
# La segunda ronda llega hasta aquí solo si la primera no vio ese rótulo.
IDENTIFIER_HEADING_LIMIT = 1000
# Si la ventana vectorial solo vio otro rótulo con el mismo número, se busca
# la sección por su texto. No depende del coseno del rótulo vacío.
SECTION_LOOKUP_LIMIT = 20


def candidate_limit(top_k: int, *, round_index: int) -> int:
    """Candidatos de una ronda. La segunda crece y no pasa del tope."""
    base = max(top_k * OVERSAMPLE_FACTOR, MIN_CANDIDATES)
    if round_index <= 0:
        return min(base, MAX_CANDIDATES)
    return min(base * 2, MAX_CANDIDATES)


@dataclass(frozen=True)
class RetrievalRequest:
    """Pregunta ya separada de la empresa. El tenant llega por `TenantContext`."""

    question: str
    top_k: int = DEFAULT_TOP_K
    document_ids: tuple[UUID, ...] = ()
    request_id: UUID | None = None


@dataclass(frozen=True)
class Evidence:
    """Fragmento citado. `score` es el coseno, no una probabilidad."""

    chunk_id: str
    document_id: UUID
    document_version_id: UUID
    source: str
    page_start: int
    page_end: int
    section: str | None
    excerpt: str
    score: float
    position: int

    def __post_init__(self) -> None:
        _require_text(self.chunk_id, "chunk_id")
        _require_text(self.source, "source")
        _require_text(self.excerpt, "excerpt")
        if self.section is not None:
            _require_text(self.section, "section")
        if isinstance(self.page_start, bool) or not isinstance(self.page_start, int):
            message = "La página inicial de la evidencia no es válida"
            raise InvalidStateError(message)
        if isinstance(self.page_end, bool) or not isinstance(self.page_end, int):
            message = "La página final de la evidencia no es válida"
            raise InvalidStateError(message)
        if self.page_start < 1 or self.page_end < self.page_start:
            message = "El rango de páginas de la evidencia no es válido"
            raise InvalidStateError(message)
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, int)
            or self.position < 1
        ):
            message = "La posición de la evidencia no es válida"
            raise InvalidStateError(message)
        if isinstance(self.score, bool) or not isinstance(self.score, int | float):
            message = "La puntuación de la evidencia no es válida"
            raise InvalidStateError(message)
        score = float(self.score)
        if score != score or score in (float("inf"), float("-inf")):
            message = "La puntuación de la evidencia no es válida"
            raise InvalidStateError(message)
        object.__setattr__(self, "score", score)


@dataclass(frozen=True)
class RetrievalTrace:
    """Huella reproducible. No guarda la pregunta, el extracto ni el vector."""

    request_id: UUID
    embedding_version: str
    index_version: str
    hits_retrieved: int
    hits_after_validation: int
    hits_after_threshold: int
    returned: int
    rounds: int
    top_k: int
    min_retrieval_score: float
    embed_ms: float
    search_ms: float
    validate_ms: float

    def __post_init__(self) -> None:
        _require_text(self.embedding_version, "embedding_version")
        _require_text(self.index_version, "index_version")
        _require_count(self.hits_retrieved, "hits_retrieved")
        _require_count(self.hits_after_validation, "hits_after_validation")
        _require_count(self.hits_after_threshold, "hits_after_threshold")
        _require_count(self.returned, "returned")
        if (
            isinstance(self.rounds, bool)
            or not isinstance(self.rounds, int)
            or not 1 <= self.rounds <= MAX_SEARCH_ROUNDS
        ):
            message = "Las rondas de búsqueda no son válidas"
            raise InvalidStateError(message)
        if (
            isinstance(self.top_k, bool)
            or not isinstance(self.top_k, int)
            or not MIN_TOP_K <= self.top_k <= MAX_TOP_K
        ):
            message = "top_k de la traza no es válido"
            raise InvalidStateError(message)
        _require_duration(self.embed_ms, "embed_ms")
        _require_duration(self.search_ms, "search_ms")
        _require_duration(self.validate_ms, "validate_ms")


@dataclass(frozen=True)
class RetrievalResult:
    """Evidencia recortada a `top_k` y la traza de esa ejecución."""

    evidence: tuple[Evidence, ...]
    has_sufficient_evidence: bool
    trace: RetrievalTrace

    def __post_init__(self) -> None:
        if not isinstance(self.has_sufficient_evidence, bool):
            message = "has_sufficient_evidence debe ser booleano"
            raise InvalidStateError(message)
        if self.has_sufficient_evidence != bool(self.evidence):
            message = "La evidencia suficiente no coincide con los fragmentos devueltos"
            raise InvalidStateError(message)


class DocumentReadCatalog(Protocol):
    """Lecturas de documentos que la recuperación necesita, ya acotadas a una empresa."""

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        """Ids del filtro que existen, son de esta empresa y están listos."""
        ...

    async def find_retrievable_versions(
        self, version_ids: Sequence[UUID]
    ) -> tuple[RetrievableVersion, ...]:
        """Versiones listas y actuales de esta empresa, en una sola consulta."""
        ...


class CandidateRetriever(Protocol):
    """Puerto de una etapa de recuperación. La implementación actual es solo densa."""

    async def retrieve(
        self,
        context: TenantContext,
        vector: tuple[float, ...],
        *,
        limit: int,
        document_ids: tuple[UUID, ...],
    ) -> tuple[VectorHit, ...]:
        """Devuelve candidatos ordenados. No decide si la evidencia alcanza."""
        ...


class CandidateReranker(Protocol):
    """Puerto opcional para reordenar candidatos ya validados. No hay implementación."""

    def rerank(
        self,
        question: str,
        hits: Sequence[VectorHit],
        *,
        limit: int,
    ) -> tuple[VectorHit, ...]:
        """Reordena sin convertir el coseno en una probabilidad."""
        ...


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        message = f"El campo {field} de la recuperación no es válido"
        raise InvalidStateError(message)


def _require_count(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = f"El conteo {field} de la traza no es válido"
        raise InvalidStateError(message)


def _require_duration(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"La duración {field} de la traza no es válida"
        raise InvalidStateError(message)
    duration = float(value)
    if duration != duration or duration < 0 or duration == float("inf"):
        message = f"La duración {field} de la traza no es válida"
        raise InvalidStateError(message)
