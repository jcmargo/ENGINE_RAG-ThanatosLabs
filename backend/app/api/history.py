"""Dependencia del historial. Los tests herméticos pueden sustituirla."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from app.conversations.service import ConversationService
from app.conversations.sql import SqlAlchemyHistoryStore
from app.infrastructure.jwt_tokens import SystemClock
from app.questions.service import QuestionService


def get_conversation_service(request: Request) -> ConversationService:
    """Historial real. Si la suite sustituye las preguntas, usa ese doble."""
    from app.api.questions import get_question_service

    override = request.app.dependency_overrides.get(get_question_service)
    questions = cast(
        QuestionService,
        override() if override is not None else get_question_service(request),
    )
    clock = getattr(request.app.state, "clock", None)
    if clock is None:
        clock = SystemClock()
    settings = request.app.state.settings
    return ConversationService(
        SqlAlchemyHistoryStore(request.app.state.session_factory),
        questions,
        clock=clock,
        conversation_retention_days=settings.conversation_retention_days,
        trace_retention_days=settings.trace_retention_days,
        feedback_reason_max_chars=settings.feedback_reason_max_chars,
        max_question_chars=settings.max_question_chars,
    )
