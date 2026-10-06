"""Convierte el historial de dominio a los esquemas HTTP."""

from app.api.history_schemas import (
    ConversationDetailResponse,
    ConversationResponse,
    DailyUsageItem,
    DailyUsageResponse,
    FeedbackResponse,
    MessageResponse,
    UsageTotalsResponse,
)
from app.api.question_schemas import CitationResponse
from app.domain.history import (
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
    DailyUsage,
    FeedbackRecord,
    UsageTotals,
)


def conversation_summary(item: ConversationSummary) -> ConversationResponse:
    """Ficha sin mensajes."""
    return ConversationResponse(
        id=item.id,
        title=item.title,
        history_scope=item.history_scope,
        created_at=item.created_at,
        updated_at=item.updated_at,
        expires_at=item.expires_at,
    )


def conversation_detail(item: ConversationDetail) -> ConversationDetailResponse:
    """Conversación con los turnos visibles."""
    summary = conversation_summary(item.summary)
    return ConversationDetailResponse(
        **summary.model_dump(),
        messages=[_message(message) for message in item.messages],
    )


def feedback_response(item: FeedbackRecord) -> FeedbackResponse:
    """Valoración del autor."""
    return FeedbackResponse(
        message_id=item.message_id,
        rating=item.rating.value,
        reason=item.reason,
        updated_at=item.updated_at,
    )


def totals_response(item: UsageTotals) -> UsageTotalsResponse:
    """Agregado sin identificadores."""
    return UsageTotalsResponse(
        documents=item.documents,
        bytes=item.byte_size,
        chunks=item.chunks,
        questions=item.questions,
        abstentions=item.abstentions,
        errors=item.errors,
        p50_ms=item.p50_ms,
        p95_ms=item.p95_ms,
    )


def daily_response(items: tuple[DailyUsage, ...]) -> DailyUsageResponse:
    """Serie diaria de una empresa."""
    return DailyUsageResponse(items=[_day(item) for item in items])


def _day(item: DailyUsage) -> DailyUsageItem:
    totals = totals_response(item.totals)
    return DailyUsageItem(day=item.day, **totals.model_dump())


def _message(item: ConversationMessage) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        request_id=item.request_id,
        role=item.role.value,
        content=item.content,
        citations=[
            CitationResponse(
                document_id=citation.document_id,
                document_name=citation.document_name,
                page=citation.page,
                section=citation.section,
                excerpt=citation.excerpt,
            )
            for citation in item.citations
        ],
        abstained=item.abstained,
        pipeline_version=item.pipeline_version,
        created_at=item.created_at,
        feedback_rating=None if item.feedback_rating is None else item.feedback_rating.value,
    )
