"""Carga, consulta y solicitudes de reintento o eliminación de documentos."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.documents.names import sanitize_display_name
from app.documents.store import FileStore, ReadChunk
from app.documents.versions import PIPELINE_VERSION
from app.domain.document_policy import (
    assert_accepts_new_version,
    assert_retryable,
    assert_visible,
    assert_within_quota,
)
from app.domain.entities import (
    Document,
    DocumentVersion,
    DocumentView,
    IngestionJob,
    TenantContext,
)
from app.domain.enums import DocumentVersionStatus, IngestionJobStatus, IngestionJobType, UsageKind
from app.domain.errors import InvalidStateError
from app.domain.pagination import PageCursor, decode_page_cursor, encode_page_cursor
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.observability.context import peek_request_id
from app.persistence.repositories.documents import SqlAlchemyDocumentRepository
from app.persistence.unit_of_work import UnitOfWork
from app.persistence.usage import record_usage_event


@dataclass(frozen=True)
class StoredDocument:
    """Resultado de una carga. `created` distingue el alta del duplicado."""

    view: DocumentView
    created: bool


@dataclass(frozen=True)
class DocumentPage:
    """Página de documentos de una empresa."""

    items: list[DocumentView]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class _PendingUpload:
    view: DocumentView
    storage_key: str


class DocumentService:
    """Casos de uso de documentos. El tenant sale solo de `TenantContext`."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        file_store: FileStore,
        *,
        max_upload_bytes: int,
        max_documents: int,
        max_storage_bytes: int,
        clock: Clock | None = None,
        before_commit: Callable[[], None] | None = None,
        max_job_attempts: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._store = file_store
        self._max_upload_bytes = max_upload_bytes
        self._max_documents = max_documents
        self._max_storage_bytes = max_storage_bytes
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._before_commit = before_commit
        if isinstance(max_job_attempts, bool) or max_job_attempts < 1:
            message = "El máximo de intentos del trabajo debe ser positivo"
            raise InvalidStateError(message)
        self._max_job_attempts = max_job_attempts

    async def create_document(
        self,
        context: TenantContext,
        *,
        filename: str,
        read_chunk: ReadChunk,
    ) -> StoredDocument:
        """Crea un documento o devuelve el activo con el mismo contenido."""
        return await self._store_upload(
            context,
            filename=filename,
            read_chunk=read_chunk,
            document_id=None,
        )

    async def add_version(
        self,
        context: TenantContext,
        document_id: UUID,
        *,
        filename: str,
        read_chunk: ReadChunk,
    ) -> StoredDocument:
        """Añade una versión o devuelve el activo con el mismo contenido."""
        return await self._store_upload(
            context,
            filename=filename,
            read_chunk=read_chunk,
            document_id=document_id,
        )

    async def list_documents(
        self,
        context: TenantContext,
        *,
        status: DocumentVersionStatus | None,
        cursor: str | None,
        limit: int,
    ) -> DocumentPage:
        """Lista documentos visibles con cursor `(created_at, id)`."""
        after = _cursor_or_none(cursor)
        async with UnitOfWork(self._session_factory) as uow:
            repository = SqlAlchemyDocumentRepository(uow, context)
            total = await repository.count_documents(status=status)
            rows = await repository.list_documents(status=status, after=after, limit=limit + 1)
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1][0]
            next_cursor = encode_page_cursor(last.created_at, last.id)
        return DocumentPage(
            items=[_view(document, version) for document, version in page],
            total=total,
            next_cursor=next_cursor,
        )

    async def get_document(self, context: TenantContext, document_id: UUID) -> DocumentView:
        """Ficha de un documento de esta empresa."""
        async with UnitOfWork(self._session_factory) as uow:
            repository = SqlAlchemyDocumentRepository(uow, context)
            document, version = await repository.get_document(document_id)
        assert_visible(document.status)
        return _view(document, version)

    async def retry_version(
        self,
        context: TenantContext,
        document_id: UUID,
        version_id: UUID,
    ) -> DocumentView:
        """Vuelve a encolar una versión fallida."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as uow:
            repository = SqlAlchemyDocumentRepository(uow, context)
            await repository.lock_tenant()
            version = await repository.get_version(document_id, version_id)
            assert_retryable(version.status)
            await repository.requeue_version(
                document_id=document_id,
                version_id=version_id,
                job=_job(
                    context,
                    document_id=document_id,
                    version_id=version_id,
                    job_type=IngestionJobType.INGEST,
                    now=now,
                    max_attempts=self._max_job_attempts,
                ),
                updated_at=now,
            )
            document, current = await repository.get_document(document_id)
            await uow.commit()
        return _view(document, current)

    async def request_deletion(self, context: TenantContext, document_id: UUID) -> DocumentView:
        """Solicita la eliminación. No declara el borrado como terminado."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as uow:
            from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository

            await SqlAlchemyLifecycleRepository(uow.session).assert_mutations_allowed(now)
            repository = SqlAlchemyDocumentRepository(uow, context)
            await repository.lock_tenant()
            document, version = await repository.get_document(document_id)
            assert_visible(document.status)
            job = None
            if document.status is not DocumentVersionStatus.DELETING:
                job = _job(
                    context,
                    document_id=document.id,
                    version_id=version.id,
                    job_type=IngestionJobType.DELETE,
                    now=now,
                    max_attempts=self._max_job_attempts,
                )
            document, version = await repository.mark_deleting(
                document_id=document_id,
                job=job,
                updated_at=now,
            )
            await uow.commit()
        return _view(document, version)

    async def _store_upload(
        self,
        context: TenantContext,
        *,
        filename: str,
        read_chunk: ReadChunk,
        document_id: UUID | None,
    ) -> StoredDocument:
        safe_name = sanitize_display_name(filename)
        staged = await self._store.stage(read_chunk, max_bytes=self._max_upload_bytes)
        promoted_key: str | None = None
        try:
            media_type = await asyncio.to_thread(
                self._store.detect_media_type,
                staged,
                filename=safe_name,
            )
            async with UnitOfWork(self._session_factory) as uow:
                repository = SqlAlchemyDocumentRepository(uow, context)
                await repository.lock_tenant()
                existing = await repository.find_active_version_by_hash(staged.content_sha256)
                if existing is not None:
                    document, version = await repository.get_document(existing.document_id)
                    view = _view(document, version)
                    await asyncio.to_thread(self._store.discard, staged)
                    await uow.rollback()
                    return StoredDocument(view=view, created=False)
                pending = await self._persist_upload(
                    repository,
                    context,
                    document_id=document_id,
                    display_name=safe_name,
                    media_type=media_type,
                    byte_size=staged.byte_size,
                    content_sha256=staged.content_sha256,
                )
                promoted_key = await asyncio.to_thread(
                    self._store.promote,
                    staged,
                    pending.storage_key,
                )
                if self._before_commit is not None:
                    self._before_commit()
                await record_usage_event(
                    uow,
                    context,
                    kind=UsageKind.UPLOAD,
                    source_id=pending.view.version_id,
                    occurred_at=pending.view.updated_at,
                    byte_size=pending.view.byte_size,
                    quantity=1,
                )
                await uow.commit()
                return StoredDocument(view=pending.view, created=True)
        except Exception:
            if promoted_key is not None:
                await asyncio.to_thread(self._store.delete, promoted_key)
            else:
                await asyncio.to_thread(self._store.discard, staged)
            raise

    async def _persist_upload(
        self,
        repository: SqlAlchemyDocumentRepository,
        context: TenantContext,
        *,
        document_id: UUID | None,
        display_name: str,
        media_type: str,
        byte_size: int,
        content_sha256: str,
    ) -> _PendingUpload:
        if document_id is None:
            return await self._insert_document(
                repository,
                context,
                display_name=display_name,
                media_type=media_type,
                byte_size=byte_size,
                content_sha256=content_sha256,
            )
        return await self._insert_version(
            repository,
            context,
            document_id=document_id,
            display_name=display_name,
            media_type=media_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
        )

    async def _insert_document(
        self,
        repository: SqlAlchemyDocumentRepository,
        context: TenantContext,
        *,
        display_name: str,
        media_type: str,
        byte_size: int,
        content_sha256: str,
    ) -> _PendingUpload:
        now = self._clock.now()
        await self._ensure_quota(repository, byte_size=byte_size, is_new_document=True)
        document_id = uuid4()
        version_id = uuid4()
        storage_key = _storage_key(context.tenant_id, document_id, version_id)
        version = _version(
            context,
            document_id=document_id,
            version_id=version_id,
            version_number=1,
            media_type=media_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            storage_key=storage_key,
            now=now,
        )
        document = Document(
            id=document_id,
            tenant_id=context.tenant_id,
            display_name=display_name,
            status=DocumentVersionStatus.QUEUED,
            current_version_id=version_id,
            created_at=now,
            updated_at=now,
        )
        await repository.add_document(
            document,
            version,
            _job(
                context,
                document_id=document_id,
                version_id=version_id,
                job_type=IngestionJobType.INGEST,
                max_attempts=self._max_job_attempts,
                now=now,
            ),
        )
        return _PendingUpload(view=_view(document, version), storage_key=storage_key)

    async def _insert_version(
        self,
        repository: SqlAlchemyDocumentRepository,
        context: TenantContext,
        *,
        document_id: UUID,
        display_name: str,
        media_type: str,
        byte_size: int,
        content_sha256: str,
    ) -> _PendingUpload:
        now = self._clock.now()
        document, _current = await repository.get_document(document_id)
        assert_accepts_new_version(document.status)
        await self._ensure_quota(repository, byte_size=byte_size, is_new_document=False)
        version_number = await repository.next_version_number(document_id)
        version_id = uuid4()
        storage_key = _storage_key(context.tenant_id, document_id, version_id)
        version = _version(
            context,
            document_id=document_id,
            version_id=version_id,
            version_number=version_number,
            media_type=media_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            storage_key=storage_key,
            now=now,
        )
        await repository.add_version(
            document_id=document_id,
            display_name=display_name,
            version=version,
            job=_job(
                context,
                document_id=document_id,
                version_id=version_id,
                job_type=IngestionJobType.INGEST,
                now=now,
                max_attempts=self._max_job_attempts,
            ),
            updated_at=now,
        )
        view = DocumentView(
            id=document.id,
            display_name=display_name,
            status=DocumentVersionStatus.QUEUED,
            version_number=version_number,
            byte_size=byte_size,
            mime_type=media_type,
            created_at=document.created_at,
            updated_at=now,
            chunk_count=0,
            version_id=version_id,
            progress=0,
        )
        return _PendingUpload(view=view, storage_key=storage_key)

    async def _ensure_quota(
        self,
        repository: SqlAlchemyDocumentRepository,
        *,
        byte_size: int,
        is_new_document: bool,
    ) -> None:
        assert_within_quota(
            document_count=await repository.count_documents(status=None),
            stored_bytes=await repository.sum_stored_bytes(),
            incoming_bytes=byte_size,
            max_documents=self._max_documents,
            max_bytes=self._max_storage_bytes,
            is_new_document=is_new_document,
        )


def _storage_key(tenant_id: UUID, document_id: UUID, version_id: UUID) -> str:
    return f"{tenant_id}/{document_id}/{version_id}"


def _version(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    version_number: int,
    media_type: str,
    byte_size: int,
    content_sha256: str,
    storage_key: str,
    now: datetime,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        tenant_id=context.tenant_id,
        document_id=document_id,
        version_number=version_number,
        status=DocumentVersionStatus.QUEUED,
        mime_type=media_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        storage_key=storage_key,
        pipeline_version=PIPELINE_VERSION,
        chunk_count=0,
        created_at=now,
        updated_at=now,
    )


def _job(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    job_type: IngestionJobType,
    now: datetime,
    max_attempts: int,
) -> IngestionJob:
    return IngestionJob(
        id=uuid4(),
        tenant_id=context.tenant_id,
        document_id=document_id,
        document_version_id=version_id,
        job_type=job_type,
        status=IngestionJobStatus.PENDING,
        created_at=now,
        updated_at=now,
        max_attempts=max_attempts,
        origin_request_id=peek_request_id(),
    )


def _view(document: Document, version: DocumentVersion) -> DocumentView:
    return DocumentView(
        id=document.id,
        display_name=document.display_name,
        status=document.status,
        version_number=version.version_number,
        byte_size=version.byte_size,
        mime_type=version.mime_type,
        created_at=document.created_at,
        updated_at=document.updated_at,
        chunk_count=version.chunk_count,
        version_id=version.id,
        progress=version.progress,
        public_error_code=version.public_error_code,
    )


def _cursor_or_none(cursor: str | None) -> PageCursor | None:
    if cursor is None or not cursor.strip():
        return None
    return decode_page_cursor(cursor)
