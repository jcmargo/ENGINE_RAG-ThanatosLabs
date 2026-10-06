"""Exportación cifrada para el administrador de la empresa."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversations.payload import owner_from
from app.documents.store import FileStore
from app.domain.entities import TenantContext
from app.domain.errors import NotFoundError
from app.infrastructure.export_crypto import decrypt_package, encrypt_package
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.infrastructure.secrets import hash_opaque_secret, secrets_match
from app.persistence.models.document import DocumentModel
from app.persistence.models.document_version import DocumentVersionModel
from app.persistence.models.history import ConversationModel, MessageModel
from app.persistence.models.membership import MembershipModel
from app.persistence.models.tenant import TenantModel
from app.persistence.models.user import UserModel
from app.persistence.repositories.exports import SqlAlchemyExportRepository
from app.persistence.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreatedExport:
    """El token se entrega una sola vez."""

    package_id: UUID
    token: str
    expires_at: datetime


class ExportService:
    """Arma el paquete, lo cifra y no vuelve a mostrar el token."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        files: FileStore,
        export_files: ExportFileStore,
        *,
        ttl_hours: int,
        public_config: dict[str, object],
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._files = files
        self._export_files = export_files
        self._ttl = timedelta(hours=ttl_hours)
        self._public_config = public_config
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def create(self, context: TenantContext) -> CreatedExport:
        """Cifra documentos, metadatos, usuarios y conversaciones propias."""
        now = self._clock.now()
        package_id = uuid4()
        payload = await self._bundle(context)
        token, nonce_hex, ciphertext = encrypt_package(payload)
        key = self._export_files.write(package_id, ciphertext)
        owner = context.principal.user_id or context.principal.api_key_id
        if owner is None:
            message = "La exportación no tiene autor"
            raise NotFoundError(message)
        async with UnitOfWork(self._session_factory) as unit:
            SqlAlchemyExportRepository(unit, context).add(
                package_id=package_id,
                token_hash=hash_opaque_secret(token),
                nonce_hex=nonce_hex,
                expires_at=now + self._ttl,
                storage_key=key,
                created_by=owner,
                created_at=now,
                byte_size=len(ciphertext),
            )
            await unit.commit()
        logger.info("export status=ok package=%s bytes=%d", package_id, len(ciphertext))
        return CreatedExport(package_id=package_id, token=token, expires_at=now + self._ttl)

    async def download(self, context: TenantContext, package_id: UUID, token: str) -> bytes:
        """Descifra si el token y la caducidad coinciden. No registra el token."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as unit:
            row = await SqlAlchemyExportRepository(unit, context).get(package_id)
        if row is None or row.expires_at <= now:
            raise NotFoundError("La exportación no existe")
        if not secrets_match(hash_opaque_secret(token), row.token_hash):
            raise NotFoundError("La exportación no existe")
        ciphertext = self._export_files.read(row.storage_key)
        return decrypt_package(token, row.nonce_hex, ciphertext)

    async def _bundle(self, context: TenantContext) -> bytes:
        owner = owner_from(context.principal)
        async with UnitOfWork(self._session_factory) as unit:
            session = unit.session
            tenant = await session.get(TenantModel, context.tenant_id)
            if tenant is None:
                raise NotFoundError("La empresa no existe")
            users = await _users(session, context.tenant_id)
            documents = await _documents(session, context.tenant_id)
            conversations = await _conversations(
                session,
                context.tenant_id,
                owner_kind=owner.kind.value,
                owner_id=owner.owner_id,
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "tenant_id": str(context.tenant_id),
                        "documents": len(documents),
                        "users": len(users),
                        "conversations": len(conversations),
                    },
                    sort_keys=True,
                ),
            )
            archive.writestr(
                "configuration.json",
                json.dumps(
                    {
                        "name": tenant.name,
                        "slug": tenant.slug,
                        "status": tenant.status,
                        **self._public_config,
                    },
                    sort_keys=True,
                    default=str,
                ),
            )
            archive.writestr("users.json", json.dumps(users, sort_keys=True))
            archive.writestr("documents.json", json.dumps(documents, sort_keys=True, default=str))
            archive.writestr(
                "conversations.json",
                json.dumps(conversations, sort_keys=True, default=str),
            )
            for document in documents:
                key = str(document["storage_key"])
                if self._files.exists(key):
                    with self._files.open_read(key) as handle:
                        archive.writestr(f"originals/{key}", handle.read())
        return buffer.getvalue()


async def _users(session: AsyncSession, tenant_id: UUID) -> list[dict[str, str]]:
    result = await session.execute(
        select(UserModel.id, UserModel.email, MembershipModel.role)
        .join(MembershipModel, MembershipModel.user_id == UserModel.id)
        .where(MembershipModel.tenant_id == tenant_id)
    )
    return [{"id": str(row[0]), "email": row[1], "role": row[2]} for row in result.all()]


async def _documents(session: AsyncSession, tenant_id: UUID) -> list[dict[str, object]]:
    result = await session.execute(
        select(
            DocumentModel.id,
            DocumentModel.display_name,
            DocumentModel.status,
            DocumentVersionModel.storage_key,
            DocumentVersionModel.content_sha256,
            DocumentVersionModel.byte_size,
            DocumentVersionModel.mime_type,
        )
        .join(DocumentVersionModel, DocumentVersionModel.id == DocumentModel.current_version_id)
        .where(DocumentModel.tenant_id == tenant_id)
    )
    return [
        {
            "id": str(row[0]),
            "display_name": row[1],
            "status": row[2],
            "storage_key": row[3],
            "content_sha256": row[4],
            "byte_size": row[5],
            "mime_type": row[6],
        }
        for row in result.all()
    ]


async def _conversations(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    owner_kind: str,
    owner_id: UUID,
) -> list[dict[str, object]]:
    result = await session.execute(
        select(ConversationModel, MessageModel)
        .join(MessageModel, MessageModel.conversation_id == ConversationModel.id)
        .where(
            ConversationModel.tenant_id == tenant_id,
            ConversationModel.owner_kind == owner_kind,
            ConversationModel.owner_id == owner_id,
            ConversationModel.deleted_at.is_(None),
        )
        .order_by(ConversationModel.created_at, MessageModel.created_at)
    )
    grouped: dict[str, dict[str, object]] = {}
    for conversation, message in result.all():
        key = str(conversation.id)
        item = grouped.get(key)
        if item is None:
            item = {"id": key, "title": conversation.title, "messages": []}
            grouped[key] = item
        messages = item["messages"]
        if isinstance(messages, list):
            messages.append(
                {
                    "role": message.role,
                    "content": message.content,
                    "citations": message.citations,
                }
            )
    return list(grouped.values())
