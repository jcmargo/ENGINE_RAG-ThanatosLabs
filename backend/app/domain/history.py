"""Conversaciones, trazas y uso. El texto solo vive en los mensajes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.enums import (
    ConversationOwnerKind,
    FeedbackRating,
    IdempotencyStatus,
    MessageRole,
    QuestionOutcome,
)

HISTORY_SCOPE = "retrieval"
TITLE_MAX_CHARS = 120
AUTO_TITLE_CHARS = 80


@dataclass(frozen=True)
class OwnerRef:
    """Dueño de la conversación: el usuario o la API key que pregunta."""

    kind: ConversationOwnerKind
    owner_id: UUID


@dataclass(frozen=True)
class StoredCitation:
    """Cita guardada con el mensaje. La traza no copia el extracto."""

    document_id: UUID
    document_name: str
    page: int
    section: str | None
    excerpt: str


@dataclass(frozen=True)
class StoredTraceHit:
    """Fragmento técnico: identificadores, posición y score. Sin texto."""

    chunk_id: str
    document_id: UUID
    document_version_id: UUID | None
    position: int
    score: float


@dataclass(frozen=True)
class TraceDraft:
    """Traza que se persiste en cada pregunta respondida o abstención."""

    embedding_version: str
    index_version: str
    pipeline_version: str
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
    token_estimate: int
    result_code: QuestionOutcome
    hits: tuple[StoredTraceHit, ...]


@dataclass(frozen=True)
class ConversationSummary:
    """Ficha de una conversación propia. No incluye mensajes."""

    id: UUID
    title: str
    history_scope: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ConversationMessage:
    """Turno visible. El contenido no sale de esta fila hacia logs ni métricas."""

    id: UUID
    request_id: UUID
    role: MessageRole
    content: str
    citations: tuple[StoredCitation, ...]
    abstained: bool | None
    pipeline_version: str | None
    created_at: datetime
    feedback_rating: FeedbackRating | None = None


@dataclass(frozen=True)
class ConversationDetail:
    """Conversación propia con sus mensajes en orden."""

    summary: ConversationSummary
    messages: tuple[ConversationMessage, ...]


@dataclass(frozen=True)
class ConversationPage:
    """Página del listado del dueño."""

    items: tuple[ConversationSummary, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class FeedbackRecord:
    """Valoración del autor sobre un mensaje del asistente."""

    message_id: UUID
    rating: FeedbackRating
    reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class UsageTotals:
    """Agregado sin texto, usuarios ni identificadores de empresa."""

    documents: int
    byte_size: int
    chunks: int
    questions: int
    abstentions: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None


@dataclass(frozen=True)
class DailyUsage:
    """Un día de uso de una empresa. `day` es la fecha UTC."""

    day: date
    totals: UsageTotals


@dataclass(frozen=True)
class ReplaySnapshot:
    """Respuesta reconstruida desde el mensaje. No hay una segunda copia del texto."""

    request_id: UUID
    conversation_id: UUID
    message_id: UUID
    answer: str
    abstained: bool
    citations: tuple[StoredCitation, ...]
    pipeline_version: str


@dataclass(frozen=True)
class ClaimedKey:
    """Reserva de idempotencia. `replay` viene lleno solo si ya se completó."""

    id: UUID
    request_id: UUID
    replay: ReplaySnapshot | None


@dataclass(frozen=True)
class SavedTurn:
    """Identificadores públicos del turno recién guardado."""

    conversation_id: UUID
    message_id: UUID


@dataclass(frozen=True)
class TurnDraft:
    """Turno listo para una transacción corta, después del modelo."""

    owner: OwnerRef
    conversation_id: UUID | None
    title: str
    request_id: UUID
    question: str
    answer: str
    abstained: bool
    citations: tuple[StoredCitation, ...]
    pipeline_version: str
    trace: TraceDraft
    outcome: QuestionOutcome
    latency_ms: float
    token_estimate: int
    claim_id: UUID | None
    now: datetime
    conversation_expires_at: datetime
    trace_expires_at: datetime


@dataclass(frozen=True)
class IdempotencyRow:
    """Clave ya reservada por este principal dentro de su empresa."""

    id: UUID
    payload_hash: str
    request_id: UUID
    status: IdempotencyStatus
