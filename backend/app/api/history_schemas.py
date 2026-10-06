"""Esquemas HTTP del historial, el feedback y el uso. Sin `tenant_id` ni texto técnico."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.question_schemas import CitationResponse


class _IgnoreExtra(BaseModel):
    """Ignora un `tenant_id` enviado por el cliente."""

    model_config = ConfigDict(extra="ignore")


class ConversationCreateBody(_IgnoreExtra):
    """Título opcional de una conversación nueva."""

    title: str | None = Field(default=None, max_length=120)


class ConversationUpdateBody(_IgnoreExtra):
    """Nuevo título visible."""

    title: str = Field(min_length=1, max_length=120)


class ConversationResponse(_IgnoreExtra):
    """Ficha de una conversación propia."""

    id: UUID
    title: str
    history_scope: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class MessageResponse(_IgnoreExtra):
    """Turno visible. No incluye score, prompt ni ruta."""

    id: UUID
    request_id: UUID
    role: str
    content: str
    citations: list[CitationResponse]
    abstained: bool | None
    pipeline_version: str | None
    created_at: datetime
    feedback_rating: str | None = Field(default=None, pattern="^(helpful|not_helpful)$")


class ConversationDetailResponse(ConversationResponse):
    """Conversación con sus mensajes en orden."""

    messages: list[MessageResponse]


class ConversationListResponse(_IgnoreExtra):
    """Página del listado del principal autenticado."""

    items: list[ConversationResponse]
    next_cursor: str | None
    total: int


class FeedbackBody(_IgnoreExtra):
    """Valoración del autor. La razón es opcional."""

    rating: str = Field(pattern="^(helpful|not_helpful)$")
    reason: str | None = Field(default=None, max_length=500)


class FeedbackResponse(_IgnoreExtra):
    """Valoración guardada. No identifica a otros usuarios."""

    message_id: UUID
    rating: str
    reason: str | None
    updated_at: datetime


class UsageTotalsResponse(_IgnoreExtra):
    """Agregado operativo. No lleva texto, usuarios ni empresa."""

    documents: int
    bytes: int
    chunks: int
    questions: int
    abstentions: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None


class DailyUsageItem(UsageTotalsResponse):
    """Un día UTC de la empresa autenticada."""

    day: date


class DailyUsageResponse(_IgnoreExtra):
    """Serie diaria. Los percentiles salen de PostgreSQL."""

    items: list[DailyUsageItem]
