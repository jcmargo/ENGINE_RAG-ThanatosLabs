"""Historial de una empresa. El tenant sale solo de `TenantContext`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.sql.elements import ColumnElement

from app.domain.entities import TenantContext
from app.domain.enums import (
    FeedbackRating,
    IdempotencyStatus,
    MessageRole,
    QuestionOutcome,
    UsageKind,
)
from app.domain.errors import ConflictError, InvalidStateError, NotFoundError
from app.domain.follow_up import RECENT_USER_QUESTIONS
from app.domain.history import (
    HISTORY_SCOPE,
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
    StoredCitation,
    StoredTraceHit,
    TurnDraft,
)
from app.domain.pagination import PageCursor, encode_page_cursor
from app.persistence.constraints import UQ_FEEDBACK_AUTHOR
from app.persistence.models.history import (
    ConversationModel,
    FeedbackModel,
    MessageModel,
    QueryTraceModel,
    QuestionIdempotencyModel,
)
from app.persistence.scoped import TenantScopedRepository
from app.persistence.usage import (
    DAILY_USAGE_SQL,
    record_usage_event,
    usage_day_from_row,
)

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork

_CONVERSATION_MISSING = "Conversación no encontrada"
_MESSAGE_MISSING = "Mensaje no encontrado"
_IN_PROGRESS = "La solicitud con esta clave todavía está en curso"
_DIFFERENT_PAYLOAD = "La clave de idempotencia ya se usó con otra pregunta"
_STORED_INVALID = "El historial guardado no es válido"


class SqlAlchemyHistoryRepository(TenantScopedRepository):
    """Conversaciones, mensajes, trazas, feedback y uso de una sola empresa."""

    def __init__(self, unit_of_work: UnitOfWork, context: TenantContext) -> None:
        super().__init__(unit_of_work, context)

    async def find_idempotency(self, owner: OwnerRef, key_hash: str) -> IdempotencyRow | None:
        """Clave de este principal, si ya está reservada."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(QuestionIdempotencyModel).where(
                QuestionIdempotencyModel.tenant_id == self.tenant_id,
                QuestionIdempotencyModel.owner_kind == owner.kind.value,
                QuestionIdempotencyModel.owner_id == owner.owner_id,
                QuestionIdempotencyModel.key_hash == key_hash,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return IdempotencyRow(
            id=model.id,
            payload_hash=model.payload_hash,
            request_id=model.request_id,
            status=IdempotencyStatus(model.status),
        )

    async def add_idempotency(
        self,
        *,
        claim_id: UUID,
        owner: OwnerRef,
        key_hash: str,
        payload_hash: str,
        request_id: UUID,
        now: datetime,
    ) -> None:
        """Reserva la clave en estado pendiente, antes de llamar al modelo."""
        self._unit.require_open()
        self._unit.session.add(
            QuestionIdempotencyModel(
                id=claim_id,
                tenant_id=self.tenant_id,
                owner_kind=owner.kind.value,
                owner_id=owner.owner_id,
                key_hash=key_hash,
                payload_hash=payload_hash,
                request_id=request_id,
                status=IdempotencyStatus.PENDING.value,
                created_at=now,
                completed_at=None,
            )
        )
        await self._unit.session.flush()

    async def release_idempotency(self, claim_id: UUID) -> None:
        """Suelta una reserva pendiente para que la misma clave pueda reintentarse."""
        self._unit.require_open()
        await self._unit.session.execute(
            delete(QuestionIdempotencyModel).where(
                QuestionIdempotencyModel.id == claim_id,
                QuestionIdempotencyModel.tenant_id == self.tenant_id,
                QuestionIdempotencyModel.status == IdempotencyStatus.PENDING.value,
            )
        )

    async def load_replay(
        self,
        request_id: UUID,
        owner: OwnerRef,
        now: datetime,
    ) -> ReplaySnapshot | None:
        """Reconstruye la respuesta desde el mensaje del asistente."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(MessageModel)
            .join(
                ConversationModel,
                ConversationModel.id == MessageModel.conversation_id,
            )
            .where(
                MessageModel.tenant_id == self.tenant_id,
                MessageModel.request_id == request_id,
                MessageModel.role == MessageRole.ASSISTANT.value,
                *self._visible(owner, now),
            )
        )
        model = result.scalar_one_or_none()
        if model is None or model.pipeline_version is None or model.abstained is None:
            return None
        return ReplaySnapshot(
            request_id=model.request_id,
            conversation_id=model.conversation_id,
            message_id=model.id,
            answer=model.content,
            abstained=model.abstained,
            citations=_load_citations(model.citations),
            pipeline_version=model.pipeline_version,
        )

    async def require_conversation(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        """Exige una conversación propia, vigente y no borrada."""
        await self._visible_conversation(owner, conversation_id, now)

    async def previous_user_question(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> str | None:
        """Último texto de usuario de esta conversación. No lee respuestas ni trazas."""
        await self._visible_conversation(owner, conversation_id, now)
        result = await self._unit.session.execute(
            select(MessageModel.content)
            .where(
                MessageModel.tenant_id == self.tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
            .limit(1)
        )
        content = result.scalar_one_or_none()
        if content is None:
            return None
        return str(content)

    async def recent_user_questions(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        """Preguntas de usuario más recientes, de la nueva a la vieja. Sin respuestas."""
        await self._visible_conversation(owner, conversation_id, now)
        result = await self._unit.session.execute(
            select(MessageModel.content)
            .where(
                MessageModel.tenant_id == self.tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
            .limit(_recent_limit(limit))
        )
        return tuple(str(item) for item in result.scalars().all())

    async def save_turn(self, turn: TurnDraft) -> SavedTurn:
        """Guarda los dos mensajes, la traza y el uso, y cierra la clave."""
        self._unit.require_open()
        assistant_at = turn.now + timedelta(microseconds=1)
        if turn.conversation_id is None:
            conversation_id = uuid4()
            self._unit.session.add(
                ConversationModel(
                    id=conversation_id,
                    tenant_id=self.tenant_id,
                    owner_kind=turn.owner.kind.value,
                    owner_id=turn.owner.owner_id,
                    title=turn.title,
                    history_scope=HISTORY_SCOPE,
                    created_at=turn.now,
                    updated_at=assistant_at,
                    expires_at=turn.conversation_expires_at,
                    deleted_at=None,
                )
            )
            await self._unit.session.flush()
        else:
            conversation = await self._visible_conversation(
                turn.owner,
                turn.conversation_id,
                turn.now,
            )
            conversation.updated_at = assistant_at
            conversation_id = conversation.id
        assistant_id = uuid4()
        self._unit.session.add(
            MessageModel(
                id=uuid4(),
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                request_id=turn.request_id,
                role=MessageRole.USER.value,
                content=turn.question,
                citations=[],
                abstained=None,
                pipeline_version=None,
                created_at=turn.now,
            )
        )
        self._unit.session.add(
            MessageModel(
                id=assistant_id,
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                request_id=turn.request_id,
                role=MessageRole.ASSISTANT.value,
                content=turn.answer,
                citations=_dump_citations(turn.citations),
                abstained=turn.abstained,
                pipeline_version=turn.pipeline_version,
                created_at=assistant_at,
            )
        )
        await self._unit.session.flush()
        trace = turn.trace
        self._unit.session.add(
            QueryTraceModel(
                id=uuid4(),
                tenant_id=self.tenant_id,
                request_id=turn.request_id,
                conversation_id=conversation_id,
                message_id=assistant_id,
                evidence=_dump_hits(trace.hits),
                embedding_version=trace.embedding_version,
                index_version=trace.index_version,
                pipeline_version=trace.pipeline_version,
                hits_retrieved=trace.hits_retrieved,
                hits_after_validation=trace.hits_after_validation,
                hits_after_threshold=trace.hits_after_threshold,
                returned=trace.returned,
                rounds=trace.rounds,
                top_k=trace.top_k,
                min_retrieval_score=trace.min_retrieval_score,
                embed_ms=trace.embed_ms,
                search_ms=trace.search_ms,
                validate_ms=trace.validate_ms,
                generation_ms=trace.generation_ms,
                token_estimate=trace.token_estimate,
                result_code=trace.result_code.value,
                expires_at=turn.trace_expires_at,
                created_at=assistant_at,
            )
        )
        await record_usage_event(
            self._unit,
            self._context,
            kind=UsageKind.QUESTION,
            source_id=turn.request_id,
            occurred_at=turn.now,
            quantity=1,
            outcome=turn.outcome,
            latency_ms=turn.latency_ms,
            token_estimate=turn.token_estimate,
        )
        if turn.claim_id is not None:
            await self._complete_claim(turn.claim_id, assistant_at)
        await self._unit.session.flush()
        return SavedTurn(conversation_id=conversation_id, message_id=assistant_id)

    async def record_question_error(
        self,
        *,
        request_id: UUID,
        latency_ms: float,
        occurred_at: datetime,
    ) -> None:
        """Anota un error operativo. No guarda la pregunta."""
        await record_usage_event(
            self._unit,
            self._context,
            kind=UsageKind.QUESTION,
            source_id=request_id,
            occurred_at=occurred_at,
            quantity=1,
            outcome=QuestionOutcome.ERROR,
            latency_ms=latency_ms,
        )

    async def create_conversation(
        self,
        owner: OwnerRef,
        title: str,
        now: datetime,
        expires_at: datetime,
    ) -> ConversationSummary:
        """Alta explícita, antes de la primera pregunta."""
        self._unit.require_open()
        model = ConversationModel(
            id=uuid4(),
            tenant_id=self.tenant_id,
            owner_kind=owner.kind.value,
            owner_id=owner.owner_id,
            title=title,
            history_scope=HISTORY_SCOPE,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            deleted_at=None,
        )
        self._unit.session.add(model)
        await self._unit.session.flush()
        return _summary(model)

    async def list_conversations(
        self,
        owner: OwnerRef,
        now: datetime,
        after: PageCursor | None,
        limit: int,
    ) -> ConversationPage:
        """Página de conversaciones vigentes de este principal."""
        self._unit.require_open()
        visible = self._visible(owner, now)
        total_result = await self._unit.session.execute(
            select(func.count()).select_from(ConversationModel).where(*visible)
        )
        statement = select(ConversationModel).where(*visible)
        if after is not None:
            statement = statement.where(
                tuple_(ConversationModel.created_at, ConversationModel.id)
                > (after.created_at, after.entity_id)
            )
        statement = statement.order_by(ConversationModel.created_at, ConversationModel.id).limit(
            limit + 1
        )
        result = await self._unit.session.execute(statement)
        rows = list(result.scalars().all())
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = encode_page_cursor(last.created_at, last.id)
        return ConversationPage(
            items=tuple(_summary(item) for item in page),
            next_cursor=next_cursor,
            total=int(total_result.scalar_one()),
        )

    async def get_conversation(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> ConversationDetail:
        """Conversación propia con sus mensajes."""
        conversation = await self._visible_conversation(owner, conversation_id, now)
        result = await self._unit.session.execute(
            select(MessageModel)
            .where(
                MessageModel.tenant_id == self.tenant_id,
                MessageModel.conversation_id == conversation.id,
            )
            .order_by(MessageModel.created_at, MessageModel.id)
        )
        loaded = tuple(_message(item) for item in result.scalars().all())
        ratings = await self._author_ratings(owner, tuple(item.id for item in loaded))
        messages = tuple(
            replace(item, feedback_rating=ratings[item.id]) if item.id in ratings else item
            for item in loaded
        )
        return ConversationDetail(summary=_summary(conversation), messages=messages)

    async def _author_ratings(
        self,
        owner: OwnerRef,
        message_ids: tuple[UUID, ...],
    ) -> dict[UUID, FeedbackRating]:
        """Valoración de este autor. No incluye el motivo ni votos ajenos."""
        if not message_ids:
            return {}
        found = await self._unit.session.execute(
            select(FeedbackModel.message_id, FeedbackModel.rating).where(
                FeedbackModel.tenant_id == self.tenant_id,
                FeedbackModel.author_kind == owner.kind.value,
                FeedbackModel.author_id == owner.owner_id,
                FeedbackModel.message_id.in_(message_ids),
            )
        )
        return {message_id: FeedbackRating(rating) for message_id, rating in found.all()}

    async def rename_conversation(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        title: str,
        now: datetime,
    ) -> ConversationSummary:
        """Cambia el título visible. No toca los mensajes."""
        conversation = await self._visible_conversation(owner, conversation_id, now)
        conversation.title = title
        conversation.updated_at = now
        await self._unit.session.flush()
        return _summary(conversation)

    async def delete_conversation(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        """Borrado lógico. La fila sigue hasta la purga física."""
        conversation = await self._visible_conversation(owner, conversation_id, now)
        conversation.deleted_at = now
        conversation.updated_at = now
        await self._unit.session.flush()

    async def upsert_feedback(
        self,
        owner: OwnerRef,
        message_id: UUID,
        rating: FeedbackRating,
        reason: str | None,
        now: datetime,
    ) -> FeedbackRecord:
        """Crea o sustituye la valoración del autor sobre un mensaje del asistente."""
        self._unit.require_open()
        found = await self._unit.session.execute(
            select(MessageModel.id)
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .where(
                MessageModel.id == message_id,
                MessageModel.tenant_id == self.tenant_id,
                MessageModel.role == MessageRole.ASSISTANT.value,
                *self._visible(owner, now),
            )
        )
        if found.scalar_one_or_none() is None:
            raise NotFoundError(_MESSAGE_MISSING)
        inserted = (
            pg_insert(FeedbackModel)
            .values(
                id=uuid4(),
                tenant_id=self.tenant_id,
                message_id=message_id,
                author_kind=owner.kind.value,
                author_id=owner.owner_id,
                rating=rating.value,
                reason=reason,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint=UQ_FEEDBACK_AUTHOR,
                set_={"rating": rating.value, "reason": reason, "updated_at": now},
            )
            .returning(
                FeedbackModel.message_id,
                FeedbackModel.rating,
                FeedbackModel.reason,
                FeedbackModel.updated_at,
            )
        )
        row = (await self._unit.session.execute(inserted)).one()
        return FeedbackRecord(
            message_id=row.message_id,
            rating=FeedbackRating(row.rating),
            reason=row.reason,
            updated_at=row.updated_at,
        )

    async def daily_usage(self, start: datetime, end: datetime) -> tuple[DailyUsage, ...]:
        """Agrega en PostgreSQL. El percentil no se calcula en Python."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            DAILY_USAGE_SQL,
            {"tenant_id": self.tenant_id, "start": start, "end": end},
        )
        return tuple(
            usage_day_from_row(cast(Mapping[str, object], row._mapping)) for row in result.all()
        )

    async def resolve_claim(self, row: IdempotencyRow, payload_hash: str) -> None:
        """Rechaza otra pregunta o una clave que todavía no terminó."""
        if row.payload_hash != payload_hash:
            raise ConflictError(_DIFFERENT_PAYLOAD)
        if row.status is not IdempotencyStatus.COMPLETED:
            raise ConflictError(_IN_PROGRESS)

    async def _complete_claim(self, claim_id: UUID, completed_at: datetime) -> None:
        result = await self._unit.session.execute(
            update(QuestionIdempotencyModel)
            .where(
                QuestionIdempotencyModel.id == claim_id,
                QuestionIdempotencyModel.tenant_id == self.tenant_id,
                QuestionIdempotencyModel.status == IdempotencyStatus.PENDING.value,
            )
            .values(
                status=IdempotencyStatus.COMPLETED.value,
                completed_at=completed_at,
            )
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise ConflictError(_IN_PROGRESS)

    async def _visible_conversation(
        self,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> ConversationModel:
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(ConversationModel).where(
                ConversationModel.id == conversation_id,
                *self._visible(owner, now),
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_CONVERSATION_MISSING)
        return model

    def _visible(self, owner: OwnerRef, now: datetime) -> tuple[ColumnElement[bool], ...]:
        return (
            ConversationModel.tenant_id == self.tenant_id,
            ConversationModel.owner_kind == owner.kind.value,
            ConversationModel.owner_id == owner.owner_id,
            ConversationModel.deleted_at.is_(None),
            ConversationModel.expires_at > now,
        )


def _summary(model: ConversationModel) -> ConversationSummary:
    return ConversationSummary(
        id=model.id,
        title=model.title,
        history_scope=model.history_scope,
        created_at=model.created_at,
        updated_at=model.updated_at,
        expires_at=model.expires_at,
    )


def _message(model: MessageModel) -> ConversationMessage:
    role = MessageRole(model.role)
    return ConversationMessage(
        id=model.id,
        request_id=model.request_id,
        role=role,
        content=model.content,
        citations=_load_citations(model.citations),
        abstained=model.abstained,
        pipeline_version=model.pipeline_version,
        created_at=model.created_at,
    )


def _dump_citations(items: tuple[StoredCitation, ...]) -> list[dict[str, object]]:
    return [
        {
            "document_id": str(item.document_id),
            "document_name": item.document_name,
            "page": item.page,
            "section": item.section,
            "excerpt": item.excerpt,
        }
        for item in items
    ]


def _dump_hits(items: tuple[StoredTraceHit, ...]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": item.chunk_id,
            "document_id": str(item.document_id),
            "document_version_id": (
                None if item.document_version_id is None else str(item.document_version_id)
            ),
            "position": item.position,
            "score": item.score,
        }
        for item in items
    ]


def _load_citations(raw: object) -> tuple[StoredCitation, ...]:
    if not isinstance(raw, list):
        raise InvalidStateError(_STORED_INVALID)
    loaded: list[StoredCitation] = []
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidStateError(_STORED_INVALID)
        section = item.get("section")
        if section is not None and not isinstance(section, str):
            raise InvalidStateError(_STORED_INVALID)
        loaded.append(
            StoredCitation(
                document_id=_uuid(item.get("document_id")),
                document_name=_text(item.get("document_name")),
                page=_page(item.get("page")),
                section=section,
                excerpt=_text(item.get("excerpt")),
            )
        )
    return tuple(loaded)


def _uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise InvalidStateError(_STORED_INVALID)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    raise InvalidStateError(_STORED_INVALID)


def _page(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise InvalidStateError(_STORED_INVALID)


def _recent_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return RECENT_USER_QUESTIONS
    return min(limit, RECENT_USER_QUESTIONS)
