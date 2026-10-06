"""Doble en memoria del historial. No abre PostgreSQL."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from uuid import UUID, uuid4

from app.domain.entities import TenantContext
from app.domain.enums import FeedbackRating, IdempotencyStatus, MessageRole, UsageKind
from app.domain.errors import ConflictError, NotFoundError
from app.domain.follow_up import RECENT_USER_QUESTIONS
from app.domain.history import (
    HISTORY_SCOPE,
    ClaimedKey,
    ConversationDetail,
    ConversationMessage,
    ConversationPage,
    ConversationSummary,
    DailyUsage,
    FeedbackRecord,
    IdempotencyRow,
    OwnerRef,
    ReplaySnapshot,
    SavedTurn,
    TraceDraft,
    TurnDraft,
    UsageTotals,
)
from app.domain.pagination import PageCursor, encode_page_cursor

_IN_PROGRESS = "La solicitud con esta clave todavía está en curso"
_DIFFERENT = "La clave de idempotencia ya se usó con otra pregunta"
_CONVERSATION_MISSING = "Conversación no encontrada"
_MESSAGE_MISSING = "Mensaje no encontrado"


@dataclass
class _Conversation:
    id: UUID
    tenant_id: UUID
    owner: OwnerRef
    title: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    deleted_at: datetime | None = None
    messages: list[ConversationMessage] = field(default_factory=list)


@dataclass
class _Usage:
    tenant_id: UUID
    kind: UsageKind
    source_id: UUID
    outcome: str | None
    byte_size: int | None
    quantity: int
    latency_ms: float | None
    token_estimate: int | None
    occurred_at: datetime


@dataclass
class _Feedback:
    tenant_id: UUID
    message_id: UUID
    owner: OwnerRef
    rating: FeedbackRating
    reason: str | None
    updated_at: datetime


class MemoryHistoryStore:
    """Misma secuencia que PostgreSQL, para pruebas que no deben abrir una base."""

    def __init__(self) -> None:
        self.conversations: dict[UUID, _Conversation] = {}
        self.keys: dict[tuple[UUID, str, UUID, str], IdempotencyRow] = {}
        self.usage: list[_Usage] = []
        self.feedback: dict[tuple[UUID, str, UUID], _Feedback] = {}
        self.traces: list[tuple[UUID, TraceDraft]] = []

    async def claim(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        key_hash: str,
        payload_hash: str,
        request_id: UUID,
        now: datetime,
    ) -> ClaimedKey:
        slot = (context.tenant_id, owner.kind.value, owner.owner_id, key_hash)
        existing = self.keys.get(slot)
        if existing is None:
            row = IdempotencyRow(
                id=uuid4(),
                payload_hash=payload_hash,
                request_id=request_id,
                status=IdempotencyStatus.PENDING,
            )
            self.keys[slot] = row
            return ClaimedKey(id=row.id, request_id=request_id, replay=None)
        if existing.payload_hash != payload_hash:
            raise ConflictError(_DIFFERENT)
        if existing.status is not IdempotencyStatus.COMPLETED:
            raise ConflictError(_IN_PROGRESS)
        replay = self._replay(context.tenant_id, owner, existing.request_id, now)
        if replay is None:
            raise NotFoundError(_CONVERSATION_MISSING)
        return ClaimedKey(id=existing.id, request_id=existing.request_id, replay=replay)

    async def abandon(
        self,
        context: TenantContext,
        *,
        claim_id: UUID | None,
        request_id: UUID,
        latency_ms: float,
        occurred_at: datetime,
        record_usage: bool,
    ) -> None:
        if claim_id is not None:
            for slot, row in list(self.keys.items()):
                pending = row.id == claim_id and slot[0] == context.tenant_id
                if pending and row.status is IdempotencyStatus.PENDING:
                    del self.keys[slot]
        if record_usage:
            self._add_usage(
                _Usage(
                    tenant_id=context.tenant_id,
                    kind=UsageKind.QUESTION,
                    source_id=request_id,
                    outcome="error",
                    byte_size=None,
                    quantity=1,
                    latency_ms=latency_ms,
                    token_estimate=None,
                    occurred_at=occurred_at,
                )
            )

    async def require_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        self._visible(context, owner, conversation_id, now)

    async def previous_user_question(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> str | None:
        conversation = self._visible(context, owner, conversation_id, now)
        for message in reversed(conversation.messages):
            if message.role is MessageRole.USER:
                return message.content
        return None

    async def recent_user_questions(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
        limit: int,
    ) -> tuple[str, ...]:
        conversation = self._visible(context, owner, conversation_id, now)
        found = [
            message.content for message in conversation.messages if message.role is MessageRole.USER
        ]
        bounded = (
            limit
            if isinstance(limit, int)
            and not isinstance(limit, bool)
            and 1 <= limit <= RECENT_USER_QUESTIONS
            else RECENT_USER_QUESTIONS
        )
        return tuple(reversed(found[-bounded:]))

    async def save_turn(self, context: TenantContext, turn: TurnDraft) -> SavedTurn:
        if turn.conversation_id is None:
            conversation = _Conversation(
                id=uuid4(),
                tenant_id=context.tenant_id,
                owner=turn.owner,
                title=turn.title,
                created_at=turn.now,
                updated_at=turn.now,
                expires_at=turn.conversation_expires_at,
            )
            self.conversations[conversation.id] = conversation
        else:
            conversation = self._visible(context, turn.owner, turn.conversation_id, turn.now)
            conversation.updated_at = turn.now
        user = ConversationMessage(
            id=uuid4(),
            request_id=turn.request_id,
            role=MessageRole.USER,
            content=turn.question,
            citations=(),
            abstained=None,
            pipeline_version=None,
            created_at=turn.now,
        )
        assistant = ConversationMessage(
            id=uuid4(),
            request_id=turn.request_id,
            role=MessageRole.ASSISTANT,
            content=turn.answer,
            citations=turn.citations,
            abstained=turn.abstained,
            pipeline_version=turn.pipeline_version,
            created_at=turn.now,
        )
        conversation.messages.extend((user, assistant))
        self.traces.append((context.tenant_id, turn.trace))
        self._add_usage(
            _Usage(
                tenant_id=context.tenant_id,
                kind=UsageKind.QUESTION,
                source_id=turn.request_id,
                outcome=turn.outcome.value,
                byte_size=None,
                quantity=1,
                latency_ms=turn.latency_ms,
                token_estimate=turn.token_estimate,
                occurred_at=turn.now,
            )
        )
        if turn.claim_id is not None:
            for row in self.keys.values():
                if row.id == turn.claim_id:
                    self.keys[self._slot_of(row, context.tenant_id)] = IdempotencyRow(
                        id=row.id,
                        payload_hash=row.payload_hash,
                        request_id=row.request_id,
                        status=IdempotencyStatus.COMPLETED,
                    )
        return SavedTurn(conversation_id=conversation.id, message_id=assistant.id)

    async def create_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        title: str,
        now: datetime,
        expires_at: datetime,
    ) -> ConversationSummary:
        conversation = _Conversation(
            id=uuid4(),
            tenant_id=context.tenant_id,
            owner=owner,
            title=title,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self.conversations[conversation.id] = conversation
        return _summary(conversation)

    async def list_conversations(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        now: datetime,
        after: PageCursor | None,
        limit: int,
    ) -> ConversationPage:
        rows = [
            item for item in self.conversations.values() if self._owns(item, context, owner, now)
        ]
        rows.sort(key=lambda item: (item.created_at, item.id))
        total = len(rows)
        if after is not None:
            rows = [
                item
                for item in rows
                if (item.created_at, item.id) > (after.created_at, after.entity_id)
            ]
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = encode_page_cursor(last.created_at, last.id)
        return ConversationPage(
            items=tuple(_summary(item) for item in page),
            next_cursor=next_cursor,
            total=total,
        )

    async def get_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> ConversationDetail:
        conversation = self._visible(context, owner, conversation_id, now)
        messages = tuple(
            replace(message, feedback_rating=saved.rating) if saved is not None else message
            for message in conversation.messages
            for saved in (self.feedback.get((message.id, owner.kind.value, owner.owner_id)),)
        )
        return ConversationDetail(summary=_summary(conversation), messages=messages)

    async def rename_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        title: str,
        now: datetime,
    ) -> ConversationSummary:
        conversation = self._visible(context, owner, conversation_id, now)
        conversation.title = title
        conversation.updated_at = now
        return _summary(conversation)

    async def delete_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        conversation = self._visible(context, owner, conversation_id, now)
        conversation.deleted_at = now
        conversation.updated_at = now

    async def upsert_feedback(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        message_id: UUID,
        rating: FeedbackRating,
        reason: str | None,
        now: datetime,
    ) -> FeedbackRecord:
        if self._assistant(context, owner, message_id, now) is None:
            raise NotFoundError(_MESSAGE_MISSING)
        slot = (message_id, owner.kind.value, owner.owner_id)
        self.feedback[slot] = _Feedback(
            tenant_id=context.tenant_id,
            message_id=message_id,
            owner=owner,
            rating=rating,
            reason=reason,
            updated_at=now,
        )
        saved = self.feedback[slot]
        return FeedbackRecord(
            message_id=saved.message_id,
            rating=saved.rating,
            reason=saved.reason,
            updated_at=saved.updated_at,
        )

    async def daily_usage(
        self,
        context: TenantContext,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[DailyUsage, ...]:
        grouped: dict[date, list[_Usage]] = {}
        for item in self.usage:
            if item.tenant_id != context.tenant_id:
                continue
            if item.occurred_at < start or item.occurred_at >= end:
                continue
            grouped.setdefault(item.occurred_at.date(), []).append(item)
        return tuple(DailyUsage(day=day, totals=_totals(grouped[day])) for day in sorted(grouped))

    async def global_usage(self) -> UsageTotals:
        return _totals(self.usage)

    def _visible(
        self,
        context: TenantContext,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> _Conversation:
        conversation = self.conversations.get(conversation_id)
        if conversation is None or not self._owns(conversation, context, owner, now):
            raise NotFoundError(_CONVERSATION_MISSING)
        return conversation

    def _owns(
        self,
        conversation: _Conversation,
        context: TenantContext,
        owner: OwnerRef,
        now: datetime,
    ) -> bool:
        return (
            conversation.tenant_id == context.tenant_id
            and conversation.owner == owner
            and conversation.deleted_at is None
            and conversation.expires_at > now
        )

    def _assistant(
        self,
        context: TenantContext,
        owner: OwnerRef,
        message_id: UUID,
        now: datetime,
    ) -> ConversationMessage | None:
        for conversation in self.conversations.values():
            if not self._owns(conversation, context, owner, now):
                continue
            for message in conversation.messages:
                if message.id == message_id and message.role is MessageRole.ASSISTANT:
                    return message
        return None

    def _replay(
        self,
        tenant_id: UUID,
        owner: OwnerRef,
        request_id: UUID,
        now: datetime,
    ) -> ReplaySnapshot | None:
        for conversation in self.conversations.values():
            if conversation.tenant_id != tenant_id or conversation.owner != owner:
                continue
            if conversation.deleted_at is not None or conversation.expires_at <= now:
                continue
            for message in conversation.messages:
                if message.request_id != request_id or message.role is not MessageRole.ASSISTANT:
                    continue
                if message.pipeline_version is None or message.abstained is None:
                    return None
                return ReplaySnapshot(
                    request_id=message.request_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                    answer=message.content,
                    abstained=message.abstained,
                    citations=message.citations,
                    pipeline_version=message.pipeline_version,
                )
        return None

    def _add_usage(self, item: _Usage) -> None:
        for current in self.usage:
            if (
                current.tenant_id == item.tenant_id
                and current.source_id == item.source_id
                and current.kind is item.kind
            ):
                return
        self.usage.append(item)

    def _slot_of(self, row: IdempotencyRow, tenant_id: UUID) -> tuple[UUID, str, UUID, str]:
        for slot, current in self.keys.items():
            if current.id == row.id and slot[0] == tenant_id:
                return slot
        message = "La clave de idempotencia no está reservada"
        raise ConflictError(message)


def _summary(conversation: _Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        history_scope=HISTORY_SCOPE,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        expires_at=conversation.expires_at,
    )


def _totals(items: list[_Usage]) -> UsageTotals:
    questions = [item for item in items if item.kind is UsageKind.QUESTION]
    latencies = [item.latency_ms for item in questions if item.latency_ms is not None]
    return UsageTotals(
        documents=sum(1 for item in items if item.kind is UsageKind.UPLOAD),
        byte_size=sum(item.byte_size or 0 for item in items if item.kind is UsageKind.UPLOAD),
        chunks=sum(item.quantity for item in items if item.kind is UsageKind.CHUNKS),
        questions=len(questions),
        abstentions=sum(1 for item in questions if item.outcome == "abstained"),
        errors=sum(1 for item in questions if item.outcome == "error"),
        p50_ms=_percentile(latencies, 0.5),
        p95_ms=_percentile(latencies, 0.95),
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
