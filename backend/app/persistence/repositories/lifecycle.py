"""Backup, retención y borrado de empresa. No filtra por un tenant de UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ConflictError, NotFoundError
from app.domain.lifecycle import Tombstone, backup_blocks_mutations
from app.persistence.models.activation_token import ActivationTokenModel
from app.persistence.models.api_key import ApiKeyModel
from app.persistence.models.audit_event import AuditEventModel
from app.persistence.models.auth_session import AuthSessionModel
from app.persistence.models.document import DocumentModel
from app.persistence.models.document_version import DocumentVersionModel
from app.persistence.models.evaluation import (
    EvaluationCaseModel,
    EvaluationDatasetModel,
    EvaluationRunModel,
)
from app.persistence.models.history import (
    ConversationModel,
    FeedbackModel,
    MessageModel,
    QueryTraceModel,
    QuestionIdempotencyModel,
    UsageEventModel,
)
from app.persistence.models.ingestion_job import IngestionJobModel
from app.persistence.models.lifecycle import (
    BackupRunModel,
    ExportPackageModel,
    TenantTombstoneModel,
)
from app.persistence.models.membership import MembershipModel
from app.persistence.models.tenant import TenantModel
from app.persistence.models.user import UserModel

_BACKUP_ACTIVE = "Hay un backup en curso"


class SqlAlchemyLifecycleRepository:
    """Operaciones globales de ciclo de vida. El tenant sale de la fila, no del cliente."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mutations_blocked(self, now: datetime) -> bool:
        """True si hay una captura vigente."""
        run = await self._active_run(now)
        if run is None:
            return False
        return backup_blocks_mutations(status=run.status, expires_at=run.expires_at, now=now)

    async def assert_mutations_allowed(self, now: datetime) -> None:
        """Impide borrados mientras el backup no ha cerrado."""
        if await self.mutations_blocked(now):
            raise ConflictError(_BACKUP_ACTIVE)

    async def begin_run(self, *, run_id: UUID, now: datetime, lock_seconds: int) -> None:
        """Abre la captura. Una segunda captura vigente es un conflicto."""
        await self.assert_mutations_allowed(now)
        self._session.add(
            BackupRunModel(
                id=run_id,
                status="running",
                started_at=now,
                expires_at=now + timedelta(seconds=lock_seconds),
                finished_at=None,
                manifest_sha256=None,
            )
        )

    async def finish_run(
        self,
        *,
        run_id: UUID,
        status: str,
        finished_at: datetime,
        manifest_sha256: str | None,
    ) -> None:
        """Cierra la captura y libera el bloqueo."""
        await self._session.execute(
            update(BackupRunModel)
            .where(BackupRunModel.id == run_id)
            .values(status=status, finished_at=finished_at, manifest_sha256=manifest_sha256)
        )

    async def leased_delete_count(self) -> int:
        """Borrados de documento que ya están en curso."""
        result = await self._session.execute(
            select(func.count())
            .select_from(IngestionJobModel)
            .where(
                IngestionJobModel.job_type == "delete",
                IngestionJobModel.status == "leased",
            )
        )
        return int(result.scalar_one())

    async def counts(self) -> dict[str, int]:
        """Conteos públicos. No lee texto de documentos ni mensajes."""
        names = {
            "tenants": TenantModel,
            "users": UserModel,
            "documents": DocumentModel,
            "conversations": ConversationModel,
            "messages": MessageModel,
        }
        found: dict[str, int] = {}
        for name, model in names.items():
            result = await self._session.execute(select(func.count()).select_from(model))
            found[name] = int(result.scalar_one())
        return found

    async def alembic_revision(self) -> str:
        """Revisión aplicada en esta base."""
        found = await self._session.execute(text("SELECT version_num FROM alembic_version"))
        value = found.scalar_one_or_none()
        if not isinstance(value, str) or not value:
            message = "No hay revisión de Alembic"
            raise NotFoundError(message)
        return value

    async def count_expired_conversations(self, now: datetime) -> int:
        """Cuántas conversaciones vencerían. No las borra."""
        result = await self._session.execute(
            select(func.count())
            .select_from(ConversationModel)
            .where(ConversationModel.expires_at < now)
        )
        return int(result.scalar_one())

    async def count_expired_traces(self, now: datetime) -> int:
        """Cuántas trazas vencerían. No las borra."""
        result = await self._session.execute(
            select(func.count())
            .select_from(QueryTraceModel)
            .where(QueryTraceModel.expires_at < now)
        )
        return int(result.scalar_one())

    async def count_expired_audit(self, cutoff: datetime) -> int:
        """Cuánta auditoría vencería. No la borra."""
        result = await self._session.execute(
            select(func.count())
            .select_from(AuditEventModel)
            .where(AuditEventModel.created_at < cutoff)
        )
        return int(result.scalar_one())

    async def purge_expired_conversations(self, *, now: datetime, limit: int) -> int:
        """Borra un lote de conversaciones vencidas y sus hijos."""
        ids = await self._ids(
            select(ConversationModel.id)
            .where(ConversationModel.expires_at < now)
            .order_by(ConversationModel.expires_at, ConversationModel.id)
            .limit(limit)
        )
        if not ids:
            return 0
        message_ids = select(MessageModel.id).where(MessageModel.conversation_id.in_(ids))
        await self._session.execute(
            delete(FeedbackModel).where(FeedbackModel.message_id.in_(message_ids))
        )
        await self._session.execute(
            delete(QueryTraceModel).where(QueryTraceModel.message_id.in_(message_ids))
        )
        await self._session.execute(
            delete(MessageModel).where(MessageModel.conversation_id.in_(ids))
        )
        await self._session.execute(delete(ConversationModel).where(ConversationModel.id.in_(ids)))
        return len(ids)

    async def purge_expired_traces(self, *, now: datetime, limit: int) -> int:
        """Borra trazas vencidas que sigan vivas después de su conversación."""
        ids = await self._ids(
            select(QueryTraceModel.id)
            .where(QueryTraceModel.expires_at < now)
            .order_by(QueryTraceModel.expires_at, QueryTraceModel.id)
            .limit(limit)
        )
        if not ids:
            return 0
        await self._session.execute(delete(QueryTraceModel).where(QueryTraceModel.id.in_(ids)))
        return len(ids)

    async def purge_expired_audit(self, *, cutoff: datetime, limit: int) -> int:
        """Borra un lote de auditoría anterior al corte."""
        ids = await self._ids(
            select(AuditEventModel.id)
            .where(AuditEventModel.created_at < cutoff)
            .order_by(AuditEventModel.created_at, AuditEventModel.id)
            .limit(limit)
        )
        if not ids:
            return 0
        await self._session.execute(delete(AuditEventModel).where(AuditEventModel.id.in_(ids)))
        return len(ids)

    async def expired_export_keys(self, *, now: datetime, limit: int) -> list[tuple[UUID, str]]:
        """Paquetes vencidos, para borrar el archivo y luego la fila."""
        result = await self._session.execute(
            select(ExportPackageModel.id, ExportPackageModel.storage_key)
            .where(ExportPackageModel.expires_at < now)
            .order_by(ExportPackageModel.expires_at, ExportPackageModel.id)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def delete_exports(self, package_ids: list[UUID]) -> int:
        """Borra las filas de exportación ya retiradas del disco."""
        if not package_ids:
            return 0
        await self._session.execute(
            delete(ExportPackageModel).where(ExportPackageModel.id.in_(package_ids))
        )
        return len(package_ids)

    async def list_tombstones(self) -> tuple[Tombstone, ...]:
        """Todos los tombstones, para republicar el conjunto."""
        result = await self._session.execute(
            select(TenantTombstoneModel).order_by(TenantTombstoneModel.deleted_at)
        )
        return tuple(
            Tombstone(tenant_hmac=row.tenant_hmac, deleted_at=row.deleted_at)
            for row in result.scalars().all()
        )

    async def upsert_tombstone(self, row: Tombstone) -> None:
        """Inserta o actualiza la fecha. No escribe nombre ni email."""
        existing = await self._session.get(TenantTombstoneModel, row.tenant_hmac)
        if existing is None:
            self._session.add(
                TenantTombstoneModel(tenant_hmac=row.tenant_hmac, deleted_at=row.deleted_at)
            )
            return
        existing.deleted_at = row.deleted_at

    async def tenant_ids(self) -> list[UUID]:
        """Ids presentes. La reconciliación recalcula el HMAC fuera de esta capa."""
        result = await self._session.execute(select(TenantModel.id))
        return list(result.scalars().all())

    async def storage_keys(self, tenant_id: UUID) -> list[str]:
        """Claves de originales de una empresa."""
        result = await self._session.execute(
            select(DocumentVersionModel.storage_key).where(
                DocumentVersionModel.tenant_id == tenant_id
            )
        )
        return [row for row in result.scalars().all()]

    async def export_keys(self, tenant_id: UUID) -> list[str]:
        """Claves de paquetes de esa empresa."""
        result = await self._session.execute(
            select(ExportPackageModel.storage_key).where(ExportPackageModel.tenant_id == tenant_id)
        )
        return [row for row in result.scalars().all()]

    async def purge_tenant_batch(self, tenant_id: UUID, *, limit: int) -> int:
        """Borra un lote de conversaciones y documentos de una empresa."""
        removed = await self._purge_tenant_conversations(tenant_id, limit=limit)
        removed += await self._purge_tenant_documents(tenant_id, limit=limit)
        return removed

    async def finish_tenant_purge(self, tenant_id: UUID, *, tombstone: Tombstone) -> None:
        """Borra el resto de filas y deja solo el tombstone."""
        await self._delete_tenant_tables(tenant_id)
        user_ids = await self._membership_user_ids(tenant_id)
        await self._session.execute(
            delete(MembershipModel).where(MembershipModel.tenant_id == tenant_id)
        )
        await self._delete_exclusive_users(user_ids)
        await self._session.execute(
            delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
        )
        result = await self._session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        removed = result.rowcount if isinstance(result, CursorResult) else 0
        if int(removed or 0) == 0:
            raise NotFoundError("La empresa no existe")
        await self.upsert_tombstone(tombstone)

    async def revoke_access(self, tenant_id: UUID, *, now: datetime) -> None:
        """Revoca sesiones y API keys. La empresa deja de autenticar al pasar a deleting."""
        await self._session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.tenant_id == tenant_id, AuthSessionModel.status == "active")
            .values(status="revoked", updated_at=now)
        )
        await self._session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id, ApiKeyModel.status == "active")
            .values(status="revoked", revoked_at=now, updated_at=now)
        )

    async def ready_versions(self) -> list[tuple[UUID, UUID, UUID]]:
        """Versiones listas para reindexar: tenant, documento y versión."""
        result = await self._session.execute(
            select(
                DocumentVersionModel.tenant_id,
                DocumentVersionModel.document_id,
                DocumentVersionModel.id,
            ).where(DocumentVersionModel.status == "ready")
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def _purge_tenant_conversations(self, tenant_id: UUID, *, limit: int) -> int:
        ids = await self._ids(
            select(ConversationModel.id)
            .where(ConversationModel.tenant_id == tenant_id)
            .order_by(ConversationModel.id)
            .limit(limit)
        )
        if not ids:
            return 0
        message_ids = select(MessageModel.id).where(MessageModel.conversation_id.in_(ids))
        await self._session.execute(
            delete(FeedbackModel).where(FeedbackModel.message_id.in_(message_ids))
        )
        await self._session.execute(
            delete(QueryTraceModel).where(QueryTraceModel.message_id.in_(message_ids))
        )
        await self._session.execute(
            delete(MessageModel).where(MessageModel.conversation_id.in_(ids))
        )
        await self._session.execute(delete(ConversationModel).where(ConversationModel.id.in_(ids)))
        return len(ids)

    async def _purge_tenant_documents(self, tenant_id: UUID, *, limit: int) -> int:
        ids = await self._ids(
            select(DocumentModel.id)
            .where(DocumentModel.tenant_id == tenant_id)
            .order_by(DocumentModel.id)
            .limit(limit)
        )
        if not ids:
            return 0
        await self._session.execute(
            delete(IngestionJobModel).where(IngestionJobModel.document_id.in_(ids))
        )
        await self._session.execute(
            delete(DocumentVersionModel).where(DocumentVersionModel.document_id.in_(ids))
        )
        await self._session.execute(delete(DocumentModel).where(DocumentModel.id.in_(ids)))
        return len(ids)

    async def _delete_tenant_tables(self, tenant_id: UUID) -> None:
        await self._session.execute(
            delete(EvaluationRunModel).where(EvaluationRunModel.tenant_id == tenant_id)
        )
        await self._session.execute(
            delete(EvaluationCaseModel).where(EvaluationCaseModel.tenant_id == tenant_id)
        )
        await self._session.execute(
            delete(EvaluationDatasetModel).where(EvaluationDatasetModel.tenant_id == tenant_id)
        )
        await self._session.execute(
            delete(UsageEventModel).where(UsageEventModel.tenant_id == tenant_id)
        )
        await self._session.execute(
            delete(QuestionIdempotencyModel).where(QuestionIdempotencyModel.tenant_id == tenant_id)
        )
        await self._session.execute(
            delete(ExportPackageModel).where(ExportPackageModel.tenant_id == tenant_id)
        )
        await self._session.execute(delete(ApiKeyModel).where(ApiKeyModel.tenant_id == tenant_id))
        await self._session.execute(
            delete(ActivationTokenModel).where(ActivationTokenModel.tenant_id == tenant_id)
        )
        await self._clear_session_replacements(tenant_id)
        await self._session.execute(
            delete(AuthSessionModel).where(AuthSessionModel.tenant_id == tenant_id)
        )

    async def _clear_session_replacements(self, tenant_id: UUID) -> None:
        session_ids = select(AuthSessionModel.id).where(AuthSessionModel.tenant_id == tenant_id)
        await self._session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.replaced_by_session_id.in_(session_ids))
            .values(replaced_by_session_id=None)
        )

    async def _membership_user_ids(self, tenant_id: UUID) -> list[UUID]:
        result = await self._session.execute(
            select(MembershipModel.user_id).where(MembershipModel.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    async def _delete_exclusive_users(self, user_ids: list[UUID]) -> None:
        for user_id in user_ids:
            remaining = await self._session.execute(
                select(func.count())
                .select_from(MembershipModel)
                .where(MembershipModel.user_id == user_id)
            )
            if int(remaining.scalar_one()) > 0:
                continue
            session_ids = select(AuthSessionModel.id).where(AuthSessionModel.user_id == user_id)
            await self._session.execute(
                update(AuthSessionModel)
                .where(AuthSessionModel.replaced_by_session_id.in_(session_ids))
                .values(replaced_by_session_id=None)
            )
            await self._session.execute(
                delete(AuthSessionModel).where(AuthSessionModel.user_id == user_id)
            )
            await self._session.execute(
                delete(ActivationTokenModel).where(ActivationTokenModel.user_id == user_id)
            )
            await self._session.execute(delete(UserModel).where(UserModel.id == user_id))

    async def _active_run(self, now: datetime) -> BackupRunModel | None:
        result = await self._session.execute(
            select(BackupRunModel)
            .where(BackupRunModel.status == "running", BackupRunModel.expires_at > now)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _ids(self, statement: Select[UUID]) -> list[UUID]:
        result = await self._session.execute(statement)
        return list(result.scalars().all())
