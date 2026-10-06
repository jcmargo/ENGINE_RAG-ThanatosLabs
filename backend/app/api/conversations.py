"""Rutas de conversaciones, feedback y uso. El tenant sale de la identidad."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.conversations_mapping import (
    conversation_detail,
    conversation_summary,
    daily_response,
    feedback_response,
    totals_response,
)
from app.api.deps import require_provider_admin, require_query_context, require_tenant_context
from app.api.history import get_conversation_service
from app.api.history_schemas import (
    ConversationCreateBody,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdateBody,
    DailyUsageResponse,
    FeedbackBody,
    FeedbackResponse,
    UsageTotalsResponse,
)
from app.conversations.service import ConversationService
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import FeedbackRating

router = APIRouter(tags=["conversations"])


@router.post("/v1/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreateBody,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationResponse:
    """Crea una conversación vacía del principal autenticado."""
    return conversation_summary(await history.create(context, body.title))


@router.get("/v1/conversations", response_model=ConversationListResponse)
async def list_conversations(
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConversationListResponse:
    """Lista las conversaciones vigentes de quien pregunta."""
    page = await history.list_conversations(context, cursor=cursor, limit=limit)
    return ConversationListResponse(
        items=[conversation_summary(item) for item in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: UUID,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationDetailResponse:
    """Conversación propia. Ajena, borrada o vencida responde como ausente."""
    return conversation_detail(await history.get(context, conversation_id))


@router.patch("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: UUID,
    body: ConversationUpdateBody,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationResponse:
    """Cambia el título de una conversación propia."""
    return conversation_summary(await history.rename(context, conversation_id, body.title))


@router.delete("/v1/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> Response:
    """Oculta la conversación. La fila permanece hasta la purga física."""
    await history.delete(context, conversation_id)
    return Response(status_code=204)


@router.put("/v1/messages/{message_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    message_id: UUID,
    body: FeedbackBody,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> FeedbackResponse:
    """El autor valora un mensaje del asistente de su conversación."""
    saved = await history.submit_feedback(
        context,
        message_id,
        FeedbackRating(body.rating),
        body.reason,
    )
    return feedback_response(saved)


@router.get("/v1/usage/daily", response_model=DailyUsageResponse)
async def daily_usage(
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> DailyUsageResponse:
    """Agregado diario de la empresa. No incluye texto ni usuarios."""
    return daily_response(await history.daily_usage(context, start=start, end=end))


@router.get("/v1/provider/usage", response_model=UsageTotalsResponse)
async def provider_usage(
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
) -> UsageTotalsResponse:
    """Total operativo global, sin empresas, usuarios ni mensajes."""
    return totals_response(await history.global_usage())
