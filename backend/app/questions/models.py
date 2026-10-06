"""Resultado de una pregunta. La cita pública no lleva score ni ruta."""

from dataclasses import dataclass
from uuid import UUID

from app.retrieval.contracts import DEFAULT_TOP_K


@dataclass(frozen=True)
class QuestionRequest:
    """Pregunta ya separada de la empresa. El tenant llega por `TenantContext`."""

    question: str
    top_k: int = DEFAULT_TOP_K
    document_ids: tuple[UUID, ...] = ()
    request_id: UUID | None = None
    include_debug: bool = False
    conversation_id: UUID | None = None
    llm: str | None = None


@dataclass(frozen=True)
class PublicCitation:
    """Cita construida en código desde metadatos recuperados."""

    document_id: UUID
    document_name: str
    page: int
    section: str | None
    excerpt: str


@dataclass(frozen=True)
class DebugEvidence:
    """Puntuación visible solo en el bloque de depuración del administrador."""

    chunk_id: str
    document_id: UUID
    score: float
    position: int
    document_version_id: UUID | None = None


@dataclass(frozen=True)
class QuestionDebug:
    """Traza sin prompt, vector, extracto ni ruta."""

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
    generation_ms: float
    llm_called: bool
    token_estimate: int
    evidence: tuple[DebugEvidence, ...]


@dataclass(frozen=True)
class QuestionAnswer:
    """Respuesta validada. `debug` solo existe para un tenant admin que lo pidió."""

    request_id: UUID
    answer: str
    abstained: bool
    citations: tuple[PublicCitation, ...]
    pipeline_version: str
    debug: QuestionDebug | None = None
    technical: QuestionDebug | None = None
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    history_scope: str | None = None


@dataclass(frozen=True)
class QuestionFailure:
    """Fallo ya traducido a un código estable. No incluye la salida del modelo."""

    request_id: UUID
    status_code: int
    code: str
    detail: str
