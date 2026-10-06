"""Pregunta, historial y mediciones. Un seguimiento busca con el tema reciente."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.conversations.payload import (
    canonical_payload_hash,
    clean_idempotency_key,
    clean_reason,
    clean_title,
    hash_key,
    owner_from,
    title_from_question,
)
from app.conversations.sql import SqlAlchemyHistoryStore
from app.domain.entities import TenantContext
from app.domain.enums import FeedbackRating, QuestionOutcome
from app.domain.errors import (
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
)
from app.domain.follow_up import RECENT_USER_QUESTIONS, FollowUpPlan, plan_follow_up, requested_part
from app.domain.history import (
    HISTORY_SCOPE,
    ConversationDetail,
    ConversationPage,
    ConversationSummary,
    DailyUsage,
    FeedbackRecord,
    OwnerRef,
    ReplaySnapshot,
    StoredCitation,
    StoredTraceHit,
    TraceDraft,
    TurnDraft,
    UsageTotals,
)
from app.domain.pagination import decode_page_cursor
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.questions.models import DebugEvidence, PublicCitation, QuestionAnswer, QuestionRequest
from app.questions.service import QuestionService
from app.retrieval.contracts import DEFAULT_MAX_QUESTION_CHARS
from app.retrieval.identifiers import named_label

logger = logging.getLogger(__name__)

_NO_USAGE = (InvalidStateError, NotFoundError, ConflictError, AuthorizationError)
_RANGE = "El rango de uso necesita inicio y fin"
_RANGE_ORDER = "El rango de uso es inválido"


class ConversationService:
    """Guarda el turno después de `QuestionService`. El prompt no recibe el historial."""

    def __init__(
        self,
        store: SqlAlchemyHistoryStore,
        questions: QuestionService,
        *,
        clock: Clock | None = None,
        conversation_retention_days: int = 30,
        trace_retention_days: int = 90,
        feedback_reason_max_chars: int = 500,
        max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
    ) -> None:
        self._store = store
        self._questions = questions
        self._clock = clock if clock is not None else SystemClock()
        self._conversation_days = conversation_retention_days
        self._trace_days = trace_retention_days
        self._reason_max = feedback_reason_max_chars
        self._max_question_chars = max_question_chars

    async def ask(
        self,
        context: TenantContext,
        request: QuestionRequest,
        *,
        idempotency_key: str | None,
    ) -> QuestionAnswer:
        """Responde una pregunta nueva y la deja en el historial."""
        owner = owner_from(context.principal)
        now = self._clock.now()
        request_id = request.request_id if request.request_id is not None else uuid4()
        request = replace(request, request_id=request_id)
        claim_id: UUID | None = None
        if idempotency_key is not None:
            claim = await self._store.claim(
                context,
                owner=owner,
                key_hash=hash_key(clean_idempotency_key(idempotency_key)),
                payload_hash=canonical_payload_hash(request),
                request_id=request_id,
                now=now,
            )
            if claim.replay is not None:
                logger.info("pregunta request_id=%s status=replay", claim.replay.request_id)
                return _answer_from_replay(claim.replay)
            claim_id = claim.id
        started = time.perf_counter()
        try:
            follow_up = await self._follow_up(context, request, owner, now)
            logger.info(
                "pregunta request_id=%s expanded=%s",
                request_id,
                "true" if follow_up.query != request.question else "false",
            )
            result = await self._questions.answer(
                context,
                request,
                retrieval_query=follow_up.query,
                part=follow_up.part,
                topics=follow_up.topics,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            outcome = QuestionOutcome.ABSTAINED if result.abstained else QuestionOutcome.ANSWERED
            saved = await self._store.save_turn(
                context,
                _turn(
                    request,
                    result,
                    owner=owner,
                    outcome=outcome,
                    latency_ms=latency_ms,
                    claim_id=claim_id,
                    now=now,
                    conversation_expires_at=now + timedelta(days=self._conversation_days),
                    trace_expires_at=now + timedelta(days=self._trace_days),
                ),
            )
        except Exception as error:
            await self._abandon(
                context,
                claim_id=claim_id,
                request_id=request_id,
                latency_ms=(time.perf_counter() - started) * 1000,
                occurred_at=now,
                error=error,
            )
            raise
        logger.info(
            "pregunta request_id=%s status=saved outcome=%s conversation_id=%s latency_ms=%.1f",
            result.request_id,
            outcome.value,
            saved.conversation_id,
            latency_ms,
        )
        return replace(
            result,
            conversation_id=saved.conversation_id,
            message_id=saved.message_id,
            history_scope=HISTORY_SCOPE,
        )

    async def create(self, context: TenantContext, title: str | None) -> ConversationSummary:
        """Crea una conversación vacía del principal autenticado."""
        now = self._clock.now()
        return await self._store.create_conversation(
            context,
            owner=owner_from(context.principal),
            title="Conversación" if title is None else clean_title(title),
            now=now,
            expires_at=now + timedelta(days=self._conversation_days),
        )

    async def list_conversations(
        self,
        context: TenantContext,
        *,
        cursor: str | None,
        limit: int,
    ) -> ConversationPage:
        """Lista las conversaciones vigentes de quien pregunta."""
        after = None if cursor is None else decode_page_cursor(cursor)
        return await self._store.list_conversations(
            context,
            owner=owner_from(context.principal),
            now=self._clock.now(),
            after=after,
            limit=limit,
        )

    async def get(self, context: TenantContext, conversation_id: UUID) -> ConversationDetail:
        """Devuelve una conversación propia con sus mensajes."""
        return await self._store.get_conversation(
            context,
            owner=owner_from(context.principal),
            conversation_id=conversation_id,
            now=self._clock.now(),
        )

    async def rename(
        self,
        context: TenantContext,
        conversation_id: UUID,
        title: str,
    ) -> ConversationSummary:
        """Cambia el título de una conversación propia."""
        return await self._store.rename_conversation(
            context,
            owner=owner_from(context.principal),
            conversation_id=conversation_id,
            title=clean_title(title),
            now=self._clock.now(),
        )

    async def delete(self, context: TenantContext, conversation_id: UUID) -> None:
        """Oculta la conversación. No borra la fila."""
        await self._store.delete_conversation(
            context,
            owner=owner_from(context.principal),
            conversation_id=conversation_id,
            now=self._clock.now(),
        )

    async def submit_feedback(
        self,
        context: TenantContext,
        message_id: UUID,
        rating: FeedbackRating,
        reason: str | None,
    ) -> FeedbackRecord:
        """El autor valora un mensaje del asistente de su conversación."""
        return await self._store.upsert_feedback(
            context,
            owner=owner_from(context.principal),
            message_id=message_id,
            rating=rating,
            reason=clean_reason(reason, max_chars=self._reason_max),
            now=self._clock.now(),
        )

    async def daily_usage(
        self,
        context: TenantContext,
        *,
        start: date | None,
        end: date | None,
    ) -> tuple[DailyUsage, ...]:
        """Agregado de la empresa autenticada. No incluye texto ni usuarios."""
        window_start, window_end = _window(start, end, self._clock.now())
        return await self._store.daily_usage(context, start=window_start, end=window_end)

    async def global_usage(self) -> UsageTotals:
        """Total operativo global. No identifica empresas, usuarios ni mensajes."""
        return await self._store.global_usage()

    async def _follow_up(
        self,
        context: TenantContext,
        request: QuestionRequest,
        owner: OwnerRef,
        now: datetime,
    ) -> FollowUpPlan:
        recent: tuple[str, ...] = ()
        if request.conversation_id is not None:
            await self._store.require_conversation(
                context,
                owner=owner,
                conversation_id=request.conversation_id,
                now=now,
            )
            if requested_part(request.question, topic_of=named_label) is None:
                previous = await self._store.previous_user_question(
                    context,
                    owner=owner,
                    conversation_id=request.conversation_id,
                    now=now,
                )
                recent = () if previous is None else (previous,)
            else:
                recent = await self._store.recent_user_questions(
                    context,
                    owner=owner,
                    conversation_id=request.conversation_id,
                    now=now,
                    limit=RECENT_USER_QUESTIONS,
                )
        return plan_follow_up(
            request.question,
            recent,
            max_chars=self._max_question_chars,
            topic_of=named_label,
        )

    async def _abandon(
        self,
        context: TenantContext,
        *,
        claim_id: UUID | None,
        request_id: UUID,
        latency_ms: float,
        occurred_at: datetime,
        error: Exception,
    ) -> None:
        record_usage = not isinstance(error, _NO_USAGE)
        if claim_id is None and not record_usage:
            return
        try:
            await self._store.abandon(
                context,
                claim_id=claim_id,
                request_id=request_id,
                latency_ms=latency_ms,
                occurred_at=occurred_at,
                record_usage=record_usage,
            )
        except Exception as abandon_error:
            logger.info(
                "pregunta request_id=%s status=error code=usage_record_failed error_type=%s",
                request_id,
                type(abandon_error).__name__,
            )


def _window(start: date | None, end: date | None, now: datetime) -> tuple[datetime, datetime]:
    if (start is None) != (end is None):
        raise InvalidStateError(_RANGE)
    if start is None or end is None:
        today = now.date()
        start = today - timedelta(days=29)
        end = today
    if end < start:
        raise InvalidStateError(_RANGE_ORDER)
    start_at = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_at = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return start_at, end_at


def _turn(
    request: QuestionRequest,
    result: QuestionAnswer,
    *,
    owner: OwnerRef,
    outcome: QuestionOutcome,
    latency_ms: float,
    claim_id: UUID | None,
    now: datetime,
    conversation_expires_at: datetime,
    trace_expires_at: datetime,
) -> TurnDraft:
    return TurnDraft(
        owner=owner,
        conversation_id=request.conversation_id,
        title=title_from_question(request.question),
        request_id=result.request_id,
        question=request.question,
        answer=result.answer,
        abstained=result.abstained,
        citations=tuple(_citation(item) for item in result.citations),
        pipeline_version=result.pipeline_version,
        trace=_trace(result, outcome),
        outcome=outcome,
        latency_ms=latency_ms,
        token_estimate=0 if result.technical is None else result.technical.token_estimate,
        claim_id=claim_id,
        now=now,
        conversation_expires_at=conversation_expires_at,
        trace_expires_at=trace_expires_at,
    )


def _trace(result: QuestionAnswer, outcome: QuestionOutcome) -> TraceDraft:
    technical = result.technical
    if technical is None:
        return TraceDraft(
            embedding_version="",
            index_version="",
            pipeline_version=result.pipeline_version,
            hits_retrieved=0,
            hits_after_validation=0,
            hits_after_threshold=0,
            returned=0,
            rounds=0,
            top_k=0,
            min_retrieval_score=0.0,
            embed_ms=0.0,
            search_ms=0.0,
            validate_ms=0.0,
            generation_ms=0.0,
            token_estimate=0,
            result_code=outcome,
            hits=(),
        )
    return TraceDraft(
        embedding_version=technical.embedding_version,
        index_version=technical.index_version,
        pipeline_version=result.pipeline_version,
        hits_retrieved=technical.hits_retrieved,
        hits_after_validation=technical.hits_after_validation,
        hits_after_threshold=technical.hits_after_threshold,
        returned=technical.returned,
        rounds=technical.rounds,
        top_k=technical.top_k,
        min_retrieval_score=technical.min_retrieval_score,
        embed_ms=technical.embed_ms,
        search_ms=technical.search_ms,
        validate_ms=technical.validate_ms,
        generation_ms=technical.generation_ms,
        token_estimate=technical.token_estimate,
        result_code=outcome,
        hits=tuple(_hit(item) for item in technical.evidence),
    )


def _hit(item: DebugEvidence) -> StoredTraceHit:
    return StoredTraceHit(
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        document_version_id=item.document_version_id,
        position=item.position,
        score=item.score,
    )


def _citation(item: PublicCitation) -> StoredCitation:
    return StoredCitation(
        document_id=item.document_id,
        document_name=item.document_name,
        page=item.page,
        section=item.section,
        excerpt=item.excerpt,
    )


def _answer_from_replay(replay: ReplaySnapshot) -> QuestionAnswer:
    return QuestionAnswer(
        request_id=replay.request_id,
        answer=replay.answer,
        abstained=replay.abstained,
        citations=tuple(
            PublicCitation(
                document_id=item.document_id,
                document_name=item.document_name,
                page=item.page,
                section=item.section,
                excerpt=item.excerpt,
            )
            for item in replay.citations
        ),
        pipeline_version=replay.pipeline_version,
        conversation_id=replay.conversation_id,
        message_id=replay.message_id,
        history_scope=HISTORY_SCOPE,
    )
