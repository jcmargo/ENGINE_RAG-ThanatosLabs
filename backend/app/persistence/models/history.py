"""Conversaciones, mensajes, trazas, feedback, uso e idempotencia."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_CONVERSATIONS_HISTORY_SCOPE,
    CK_CONVERSATIONS_OWNER_KIND,
    CK_FEEDBACK_AUTHOR_KIND,
    CK_FEEDBACK_RATING,
    CK_MESSAGES_ROLE,
    CK_QUERY_TRACES_RESULT,
    CK_QUESTION_IDEMPOTENCY_OWNER,
    CK_QUESTION_IDEMPOTENCY_STATUS,
    CK_USAGE_EVENTS_KIND,
    CK_USAGE_EVENTS_OUTCOME,
    FK_CONVERSATIONS_TENANT_ID,
    FK_FEEDBACK_MESSAGE_ID,
    FK_FEEDBACK_TENANT_ID,
    FK_MESSAGES_CONVERSATION_ID,
    FK_MESSAGES_TENANT_ID,
    FK_QUERY_TRACES_MESSAGE_ID,
    FK_QUERY_TRACES_TENANT_ID,
    FK_QUESTION_IDEMPOTENCY_TENANT_ID,
    FK_USAGE_EVENTS_TENANT_ID,
    IX_CONVERSATIONS_OWNER,
    IX_MESSAGES_CONVERSATION,
    IX_MESSAGES_TENANT_REQUEST,
    IX_QUERY_TRACES_EXPIRES,
    IX_USAGE_EVENTS_TENANT_OCCURRED,
    UQ_FEEDBACK_AUTHOR,
    UQ_QUERY_TRACES_REQUEST,
    UQ_QUESTION_IDEMPOTENCY_KEY,
    UQ_USAGE_EVENTS_SOURCE,
)
from app.persistence.models.base import Base

_OWNER_KIND = "owner_kind IN ('user', 'api_key')"
_AUTHOR_KIND = "author_kind IN ('user', 'api_key')"
_HISTORY_SCOPE = "history_scope IN ('visual', 'retrieval')"
_MESSAGE_ROLE = "role IN ('user', 'assistant')"
_TRACE_RESULT = "result_code IN ('answered', 'abstained', 'error')"
_FEEDBACK_RATING = "rating IN ('helpful', 'not_helpful')"
_USAGE_KIND = "kind IN ('upload', 'chunks', 'question')"
_USAGE_OUTCOME = "outcome IS NULL OR outcome IN ('answered', 'abstained', 'error')"
_IDEMPOTENCY_STATUS = "status IN ('pending', 'completed')"


class ConversationModel(Base):
    """Conversación visible de un principal. El borrado y el vencimiento son lógicos."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(_OWNER_KIND, name=CK_CONVERSATIONS_OWNER_KIND),
        CheckConstraint(_HISTORY_SCOPE, name=CK_CONVERSATIONS_HISTORY_SCOPE),
        Index(
            IX_CONVERSATIONS_OWNER,
            "tenant_id",
            "owner_kind",
            "owner_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_CONVERSATIONS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    history_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageModel(Base):
    """Texto de un turno. Las trazas y el uso no lo copian."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_MESSAGE_ROLE, name=CK_MESSAGES_ROLE),
        Index(IX_MESSAGES_CONVERSATION, "conversation_id", "created_at", "id"),
        Index(IX_MESSAGES_TENANT_REQUEST, "tenant_id", "request_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_MESSAGES_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", name=FK_MESSAGES_CONVERSATION_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[object] = mapped_column(JSONB, nullable=False)
    abstained: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QueryTraceModel(Base):
    """Traza técnica. No tiene pregunta, respuesta, prompt ni vector."""

    __tablename__ = "query_traces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name=UQ_QUERY_TRACES_REQUEST),
        CheckConstraint(_TRACE_RESULT, name=CK_QUERY_TRACES_RESULT),
        Index(IX_QUERY_TRACES_EXPIRES, "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_QUERY_TRACES_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", name=FK_QUERY_TRACES_MESSAGE_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    evidence: Mapped[object] = mapped_column(JSONB, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(255), nullable=False)
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(255), nullable=False)
    hits_retrieved: Mapped[int] = mapped_column(Integer, nullable=False)
    hits_after_validation: Mapped[int] = mapped_column(Integer, nullable=False)
    hits_after_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    returned: Mapped[int] = mapped_column(Integer, nullable=False)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    min_retrieval_score: Mapped[float] = mapped_column(Float, nullable=False)
    embed_ms: Mapped[float] = mapped_column(Float, nullable=False)
    search_ms: Mapped[float] = mapped_column(Float, nullable=False)
    validate_ms: Mapped[float] = mapped_column(Float, nullable=False)
    generation_ms: Mapped[float] = mapped_column(Float, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    result_code: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackModel(Base):
    """Una fila por autor y mensaje. Actualizar sustituye esa fila."""

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "author_kind",
            "author_id",
            name=UQ_FEEDBACK_AUTHOR,
        ),
        CheckConstraint(_FEEDBACK_RATING, name=CK_FEEDBACK_RATING),
        CheckConstraint(_AUTHOR_KIND, name=CK_FEEDBACK_AUTHOR_KIND),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_FEEDBACK_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", name=FK_FEEDBACK_MESSAGE_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageEventModel(Base):
    """Evento append-only. La clave única absorbe el reintento del worker."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", "kind", name=UQ_USAGE_EVENTS_SOURCE),
        CheckConstraint(_USAGE_KIND, name=CK_USAGE_EVENTS_KIND),
        CheckConstraint(_USAGE_OUTCOME, name=CK_USAGE_EVENTS_OUTCOME),
        Index(IX_USAGE_EVENTS_TENANT_OCCURRED, "tenant_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_USAGE_EVENTS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionIdempotencyModel(Base):
    """Hash de la clave y del payload. La respuesta se reconstruye del mensaje."""

    __tablename__ = "question_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "owner_kind",
            "owner_id",
            "key_hash",
            name=UQ_QUESTION_IDEMPOTENCY_KEY,
        ),
        CheckConstraint(_IDEMPOTENCY_STATUS, name=CK_QUESTION_IDEMPOTENCY_STATUS),
        CheckConstraint(_OWNER_KIND, name=CK_QUESTION_IDEMPOTENCY_OWNER),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_QUESTION_IDEMPOTENCY_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
