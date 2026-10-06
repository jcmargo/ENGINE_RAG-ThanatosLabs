"""Documentos de una empresa. Todas las consultas filtran por `TenantContext`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError

from app.domain.entities import (
    Document,
    DocumentVersion,
    IngestionJob,
    RetrievableVersion,
    TenantContext,
)
from app.domain.enums import DocumentVersionStatus, IngestionJobStatus, IngestionJobType
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.pagination import PageCursor
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import (
    document_to_domain,
    document_to_model,
    document_version_to_domain,
    document_version_to_model,
    ingestion_job_to_model,
)
from app.persistence.models import (
    DocumentModel,
    DocumentVersionModel,
    IngestionJobModel,
    TenantModel,
)
from app.persistence.scoped import TenantScopedRepository

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork

_NOT_FOUND = "Documento no encontrado"
_INACTIVE = (
    DocumentVersionStatus.DELETING.value,
    DocumentVersionStatus.DELETED.value,
    DocumentVersionStatus.SUPERSEDED.value,
)
_STORED = (
    DocumentVersionStatus.DELETED.value,
    DocumentVersionStatus.SUPERSEDED.value,
)


class SqlAlchemyDocumentRepository(TenantScopedRepository):
    """Persistencia de documentos, versiones y trabajos de una sola empresa."""

    def __init__(self, unit_of_work: UnitOfWork, context: TenantContext) -> None:
        super().__init__(unit_of_work, context)

    async def lock_tenant(self) -> None:
        """Bloquea la fila de la empresa durante cuota e idempotencia."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(TenantModel.id).where(TenantModel.id == self.tenant_id).with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError(_NOT_FOUND)

    async def count_documents(self, *, status: DocumentVersionStatus | None) -> int:
        """Cuenta documentos visibles de esta empresa."""
        statement = (
            select(func.count())
            .select_from(DocumentModel)
            .where(
                DocumentModel.tenant_id == self.tenant_id,
                DocumentModel.status != DocumentVersionStatus.DELETED.value,
            )
        )
        if status is not None:
            statement = statement.where(DocumentModel.status == status.value)
        result = await self._unit.session.execute(statement)
        return int(result.scalar_one())

    async def sum_stored_bytes(self) -> int:
        """Suma bytes de versiones que todavía ocupan almacenamiento."""
        result = await self._unit.session.execute(
            select(func.coalesce(func.sum(DocumentVersionModel.byte_size), 0)).where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.status.not_in(_STORED),
            )
        )
        return int(result.scalar_one())

    async def find_active_version_by_hash(self, content_sha256: str) -> DocumentVersion | None:
        """Versión activa con el mismo contenido, si ya existe en esta empresa."""
        result = await self._unit.session.execute(
            select(DocumentVersionModel).where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.content_sha256 == content_sha256,
                DocumentVersionModel.status.not_in(_INACTIVE),
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return document_version_to_domain(model)

    async def get_document(self, document_id: UUID) -> tuple[Document, DocumentVersion]:
        """Ficha de esta empresa, o ausencia si el id es ajeno o desconocido."""
        document = await self._document_model(document_id)
        version = await self._version_model(document.current_version_id, document.id)
        return document_to_domain(document), document_version_to_domain(version)

    async def list_documents(
        self,
        *,
        status: DocumentVersionStatus | None,
        after: PageCursor | None,
        limit: int,
    ) -> list[tuple[Document, DocumentVersion]]:
        """Lista documentos ordenados por `(created_at, id)`."""
        statement = (
            select(DocumentModel, DocumentVersionModel)
            .join(
                DocumentVersionModel,
                DocumentVersionModel.id == DocumentModel.current_version_id,
            )
            .where(
                DocumentModel.tenant_id == self.tenant_id,
                DocumentModel.status != DocumentVersionStatus.DELETED.value,
            )
        )
        if status is not None:
            statement = statement.where(DocumentModel.status == status.value)
        if after is not None:
            statement = statement.where(
                tuple_(DocumentModel.created_at, DocumentModel.id)
                > (after.created_at, after.entity_id)
            )
        statement = statement.order_by(DocumentModel.created_at, DocumentModel.id).limit(limit)
        result = await self._unit.session.execute(statement)
        return [
            (document_to_domain(document), document_version_to_domain(version))
            for document, version in result.all()
        ]

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        """Ids pedidos que están listos en esta empresa. No consulta si la lista va vacía."""
        unique = tuple(dict.fromkeys(document_ids))
        if not unique:
            return frozenset()
        result = await self._unit.session.execute(
            select(DocumentModel.id).where(
                DocumentModel.tenant_id == self.tenant_id,
                DocumentModel.id.in_(unique),
                DocumentModel.status == DocumentVersionStatus.READY.value,
            )
        )
        return frozenset(result.scalars().all())

    async def find_retrievable_versions(
        self, version_ids: Sequence[UUID]
    ) -> tuple[RetrievableVersion, ...]:
        """Versiones ready y actuales de esta empresa, en una sola consulta."""
        unique = tuple(dict.fromkeys(version_ids))
        if not unique:
            return ()
        result = await self._unit.session.execute(
            select(
                DocumentVersionModel.document_id,
                DocumentVersionModel.id.label("version_id"),
                DocumentModel.display_name,
            )
            .join(
                DocumentModel,
                (DocumentModel.id == DocumentVersionModel.document_id)
                & (DocumentModel.tenant_id == DocumentVersionModel.tenant_id),
            )
            .where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.id.in_(unique),
                DocumentVersionModel.status == DocumentVersionStatus.READY.value,
                DocumentModel.current_version_id == DocumentVersionModel.id,
                DocumentModel.status == DocumentVersionStatus.READY.value,
            )
        )
        return tuple(
            RetrievableVersion(
                document_id=row.document_id,
                version_id=row.version_id,
                display_name=row.display_name,
            )
            for row in result.all()
        )

    async def add_document(
        self,
        document: Document,
        version: DocumentVersion,
        job: IngestionJob,
    ) -> None:
        """Inserta documento, primera versión y trabajo en la transacción abierta."""
        self._guard(document.tenant_id, version, job)
        self._unit.session.add(document_to_model(document))
        self._unit.session.add(document_version_to_model(version))
        self._unit.session.add(ingestion_job_to_model(job))
        await self._flush()

    async def add_version(
        self,
        *,
        document_id: UUID,
        display_name: str,
        version: DocumentVersion,
        job: IngestionJob,
        updated_at: datetime,
    ) -> None:
        """Añade una versión y la deja como versión actual."""
        self._unit.require_open()
        self._guard(self.tenant_id, version, job)
        document = await self._document_model(document_id)
        document.display_name = display_name
        document.status = version.status.value
        document.current_version_id = version.id
        document.updated_at = updated_at
        self._unit.session.add(document_version_to_model(version))
        self._unit.session.add(ingestion_job_to_model(job))
        await self._flush()

    async def next_version_number(self, document_id: UUID) -> int:
        """Siguiente número de versión dentro de esta empresa."""
        await self._document_model(document_id)
        result = await self._unit.session.execute(
            select(func.coalesce(func.max(DocumentVersionModel.version_number), 0)).where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.document_id == document_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def get_version(self, document_id: UUID, version_id: UUID) -> DocumentVersion:
        """Versión de un documento de esta empresa, o ausencia."""
        await self._document_model(document_id)
        model = await self._version_model(version_id, document_id)
        return document_version_to_domain(model)

    async def requeue_version(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        job: IngestionJob,
        updated_at: datetime,
    ) -> tuple[Document, DocumentVersion]:
        """Pasa una versión fallida a `queued` y registra el trabajo."""
        self._unit.require_open()
        self._guard(self.tenant_id, None, job)
        document = await self._document_model(document_id)
        version = await self._version_model(version_id, document_id)
        version.status = DocumentVersionStatus.QUEUED.value
        version.public_error_code = None
        version.updated_at = updated_at
        if document.current_version_id == version.id:
            document.status = DocumentVersionStatus.QUEUED.value
            document.updated_at = updated_at
        self._unit.session.add(ingestion_job_to_model(job))
        await self._flush()
        return document_to_domain(document), document_version_to_domain(version)

    async def mark_deleting(
        self,
        *,
        document_id: UUID,
        job: IngestionJob | None,
        updated_at: datetime,
    ) -> tuple[Document, DocumentVersion]:
        """Marca el documento en eliminación y, si hace falta, registra un trabajo."""
        self._unit.require_open()
        document = await self._document_model(document_id)
        if document.status != DocumentVersionStatus.DELETING.value:
            document.status = DocumentVersionStatus.DELETING.value
            document.updated_at = updated_at
            await self._unit.session.execute(
                update(DocumentVersionModel)
                .where(
                    DocumentVersionModel.tenant_id == self.tenant_id,
                    DocumentVersionModel.document_id == document_id,
                    DocumentVersionModel.status.not_in(_INACTIVE),
                )
                .values(status=DocumentVersionStatus.DELETING.value, updated_at=updated_at)
            )
        if job is not None:
            self._guard(self.tenant_id, None, job)
            self._unit.session.add(ingestion_job_to_model(job))
        await self._flush()
        self._unit.session.expire_all()
        refreshed = await self._document_model(document_id)
        version = await self._version_model(refreshed.current_version_id, refreshed.id)
        return document_to_domain(refreshed), document_version_to_domain(version)

    async def set_version_progress(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        progress: int,
        updated_at: datetime,
    ) -> None:
        """Anota el avance de la versión que la API ya muestra."""
        self._unit.require_open()
        version = await self._version_model(version_id, document_id)
        if version.status not in {
            DocumentVersionStatus.QUEUED.value,
            DocumentVersionStatus.PROCESSING.value,
        }:
            return
        version.progress = progress
        version.updated_at = updated_at
        await self._flush()

    async def mark_indexed(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        chunk_count: int,
        progress: int,
        indexed_at: datetime,
    ) -> None:
        """Registra que los puntos ya están escritos, sin declarar la versión lista."""
        self._unit.require_open()
        version = await self._version_model(version_id, document_id)
        if version.status not in {
            DocumentVersionStatus.QUEUED.value,
            DocumentVersionStatus.PROCESSING.value,
        }:
            return
        version.chunk_count = chunk_count
        version.progress = progress
        version.indexed_at = indexed_at
        version.updated_at = indexed_at
        await self._flush()

    async def mark_ready(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        chunk_count: int,
        updated_at: datetime,
    ) -> None:
        """Cierra la versión y, si es la actual, el documento."""
        self._unit.require_open()
        document = await self._document_model(document_id)
        version = await self._version_model(version_id, document_id)
        version.status = DocumentVersionStatus.READY.value
        version.chunk_count = chunk_count
        version.progress = 100
        version.public_error_code = None
        version.updated_at = updated_at
        if document.current_version_id == version.id:
            document.status = DocumentVersionStatus.READY.value
            document.updated_at = updated_at
        await self._flush()

    async def mark_failed(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        error_code: str,
        updated_at: datetime,
    ) -> None:
        """Deja la versión fallida con un código público, sin texto del archivo."""
        self._unit.require_open()
        document = await self._document_model(document_id)
        version = await self._version_model(version_id, document_id)
        if version.status not in {
            DocumentVersionStatus.QUEUED.value,
            DocumentVersionStatus.PROCESSING.value,
        }:
            return
        version.status = DocumentVersionStatus.FAILED.value
        version.public_error_code = error_code
        version.updated_at = updated_at
        if document.current_version_id == version.id and document.status in {
            DocumentVersionStatus.QUEUED.value,
            DocumentVersionStatus.PROCESSING.value,
        }:
            document.status = DocumentVersionStatus.FAILED.value
            document.updated_at = updated_at
        await self._flush()

    async def mark_document_deleted(self, document_id: UUID, updated_at: datetime) -> None:
        """Marca el documento y todas sus versiones como eliminados."""
        self._unit.require_open()
        document = await self._document_model(document_id)
        document.status = DocumentVersionStatus.DELETED.value
        document.updated_at = updated_at
        await self._unit.session.execute(
            update(DocumentVersionModel)
            .where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.document_id == document_id,
            )
            .values(status=DocumentVersionStatus.DELETED.value, updated_at=updated_at, progress=100)
        )
        await self._flush()

    async def list_storage_keys(self, document_id: UUID) -> tuple[str, ...]:
        """Claves internas del documento, sin repetir un archivo compartido."""
        await self._document_model(document_id)
        result = await self._unit.session.execute(
            select(DocumentVersionModel.storage_key).where(
                DocumentVersionModel.tenant_id == self.tenant_id,
                DocumentVersionModel.document_id == document_id,
            )
        )
        return tuple(dict.fromkeys(result.scalars().all()))

    async def commit_replacement(
        self,
        *,
        document_id: UUID,
        source_version_id: UUID,
        replacement: DocumentVersion,
        updated_at: datetime,
    ) -> DocumentVersion:
        """Sustituye la versión servida solo cuando la nueva ya puede quedar lista."""
        self._unit.require_open()
        if replacement.tenant_id != self.tenant_id or replacement.document_id != document_id:
            message = "La versión de reemplazo no pertenece al contexto de empresa"
            raise InvalidStateError(message)
        document = await self._document_model(document_id)
        source = await self._version_model(source_version_id, document_id)
        existing = await self._unit.session.execute(
            select(DocumentVersionModel).where(
                DocumentVersionModel.id == replacement.id,
                DocumentVersionModel.tenant_id == self.tenant_id,
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return document_version_to_domain(found)
        source.status = DocumentVersionStatus.SUPERSEDED.value
        source.updated_at = updated_at
        await self._flush()
        number = await self.next_version_number(document_id)
        model = document_version_to_model(replacement)
        model.version_number = number
        model.status = DocumentVersionStatus.READY.value
        model.progress = 100
        self._unit.session.add(model)
        document.current_version_id = replacement.id
        document.status = DocumentVersionStatus.READY.value
        document.updated_at = updated_at
        await self._flush()
        return document_version_to_domain(model)

    async def count_jobs(self, document_id: UUID, job_type: IngestionJobType) -> int:
        """Cuenta trabajos de un documento de esta empresa."""
        result = await self._unit.session.execute(
            select(func.count())
            .select_from(IngestionJobModel)
            .where(
                IngestionJobModel.tenant_id == self.tenant_id,
                IngestionJobModel.document_id == document_id,
                IngestionJobModel.job_type == job_type.value,
                IngestionJobModel.status.in_(
                    (IngestionJobStatus.PENDING.value, IngestionJobStatus.LEASED.value)
                ),
            )
        )
        return int(result.scalar_one())

    def _guard(
        self,
        tenant_id: UUID,
        version: DocumentVersion | None,
        job: IngestionJob,
    ) -> None:
        self._unit.require_open()
        if tenant_id != self.tenant_id or job.tenant_id != self.tenant_id:
            message = "El documento no pertenece al contexto de empresa"
            raise InvalidStateError(message)
        if version is not None and version.tenant_id != self.tenant_id:
            message = "La versión no pertenece al contexto de empresa"
            raise InvalidStateError(message)

    async def _document_model(self, document_id: UUID) -> DocumentModel:
        result = await self._unit.session.execute(
            select(DocumentModel).where(
                DocumentModel.id == document_id,
                DocumentModel.tenant_id == self.tenant_id,
                DocumentModel.status != DocumentVersionStatus.DELETED.value,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_NOT_FOUND)
        return model

    async def _version_model(self, version_id: UUID, document_id: UUID) -> DocumentVersionModel:
        result = await self._unit.session.execute(
            select(DocumentVersionModel).where(
                DocumentVersionModel.id == version_id,
                DocumentVersionModel.document_id == document_id,
                DocumentVersionModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_NOT_FOUND)
        return model

    async def _flush(self) -> None:
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
