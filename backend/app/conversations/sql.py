"""Transacciones cortas del historial. El modelo no se llama dentro de ellas."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import TenantContext
from app.domain.enums import FeedbackRating
from app.domain.errors import ConflictError, NotFoundError
from app.domain.history import (
    ClaimedKey,
    ConversationDetail,
    ConversationPage,
    ConversationSummary,
    DailyUsage,
    FeedbackRecord,
    IdempotencyRow,
    OwnerRef,
    SavedTurn,
    TurnDraft,
    UsageTotals,
)
from app.domain.pagination import PageCursor
from app.persistence.repositories.history import SqlAlchemyHistoryRepository
from app.persistence.unit_of_work import UnitOfWork
from app.persistence.usage import GLOBAL_USAGE_SQL, usage_totals_from_row

_IN_PROGRESS = "La solicitud con esta clave todavía está en curso"
_CONVERSATION_MISSING = "Conversación no encontrada"


class SqlAlchemyHistoryStore:
    """Abre una unidad de trabajo por operación y confirma al terminar."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        """Reserva la clave o devuelve la respuesta ya guardada."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            existing = await repository.find_idempotency(owner, key_hash)
            if existing is not None:
                return await self._existing(repository, existing, payload_hash, owner, now)
            claim_id = uuid4()
            try:
                await repository.add_idempotency(
                    claim_id=claim_id,
                    owner=owner,
                    key_hash=key_hash,
                    payload_hash=payload_hash,
                    request_id=request_id,
                    now=now,
                )
                await unit.commit()
            except IntegrityError:
                await unit.rollback()
            else:
                return ClaimedKey(id=claim_id, request_id=request_id, replay=None)
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            existing = await repository.find_idempotency(owner, key_hash)
            if existing is None:
                raise ConflictError(_IN_PROGRESS)
            return await self._existing(repository, existing, payload_hash, owner, now)

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
        """Suelta la reserva y, si la pregunta falló, anota el error sin texto."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            if claim_id is not None:
                await repository.release_idempotency(claim_id)
            if record_usage:
                await repository.record_question_error(
                    request_id=request_id,
                    latency_ms=latency_ms,
                    occurred_at=occurred_at,
                )
            await unit.commit()

    async def require_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        """Comprueba la propiedad antes de llamar al modelo."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            await repository.require_conversation(owner, conversation_id, now)

    async def previous_user_question(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> str | None:
        """Lee solo la pregunta de usuario anterior, ya comprobada la propiedad."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            return await repository.previous_user_question(owner, conversation_id, now)

    async def recent_user_questions(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
        limit: int,
    ) -> tuple[str, ...]:
        """Lee las preguntas de usuario recientes, ya comprobada la propiedad."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            return await repository.recent_user_questions(
                owner,
                conversation_id,
                now,
                limit=limit,
            )

    async def save_turn(self, context: TenantContext, turn: TurnDraft) -> SavedTurn:
        """Persiste el turno después de obtener la respuesta."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            saved = await repository.save_turn(turn)
            await unit.commit()
            return saved

    async def create_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        title: str,
        now: datetime,
        expires_at: datetime,
    ) -> ConversationSummary:
        """Crea una conversación vacía del principal."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            created = await repository.create_conversation(owner, title, now, expires_at)
            await unit.commit()
            return created

    async def list_conversations(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        now: datetime,
        after: PageCursor | None,
        limit: int,
    ) -> ConversationPage:
        """Lista solo las conversaciones vigentes de este principal."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            return await repository.list_conversations(owner, now, after, limit)

    async def get_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> ConversationDetail:
        """Ficha propia. Ajena, borrada o vencida es ausencia."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            return await repository.get_conversation(owner, conversation_id, now)

    async def rename_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        title: str,
        now: datetime,
    ) -> ConversationSummary:
        """Renombra una conversación propia."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            renamed = await repository.rename_conversation(owner, conversation_id, title, now)
            await unit.commit()
            return renamed

    async def delete_conversation(
        self,
        context: TenantContext,
        *,
        owner: OwnerRef,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        """Marca la conversación como borrada."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            await repository.delete_conversation(owner, conversation_id, now)
            await unit.commit()

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
        """Guarda la valoración del autor en una sola fila."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            saved = await repository.upsert_feedback(owner, message_id, rating, reason, now)
            await unit.commit()
            return saved

    async def daily_usage(
        self,
        context: TenantContext,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[DailyUsage, ...]:
        """Agregado diario de esta empresa."""
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyHistoryRepository(unit, context)
            return await repository.daily_usage(start, end)

    async def global_usage(self) -> UsageTotals:
        """Total operativo de todas las empresas, sin identificadores."""
        async with self._session_factory() as session:
            result = await session.execute(GLOBAL_USAGE_SQL)
            return usage_totals_from_row(cast(Mapping[str, object], result.one()._mapping))

    @staticmethod
    async def _existing(
        repository: SqlAlchemyHistoryRepository,
        row: IdempotencyRow,
        payload_hash: str,
        owner: OwnerRef,
        now: datetime,
    ) -> ClaimedKey:
        await repository.resolve_claim(row, payload_hash)
        replay = await repository.load_replay(row.request_id, owner, now)
        if replay is None:
            raise NotFoundError(_CONVERSATION_MISSING)
        return ClaimedKey(id=row.id, request_id=row.request_id, replay=replay)
