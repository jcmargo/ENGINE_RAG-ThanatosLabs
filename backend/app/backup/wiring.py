"""Compone los adaptadores de backup a partir de la configuración."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.ports import BackupPublisher
from app.backup.restore import RestoreCoordinator
from app.backup.service import BackupCoordinator
from app.config import Settings, resolve_file_store_path
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.local_files import LocalFileStore
from app.infrastructure.originals_archive import OriginalsArchive
from app.infrastructure.pg_dump import PgDumpClient
from app.infrastructure.qdrant_snapshots import QdrantSnapshots
from app.infrastructure.restic_store import ResticRepository
from app.infrastructure.tenant_points import QdrantTenantPoints
from app.infrastructure.vector_client import create_qdrant_client


def public_config(settings: Settings) -> dict[str, object]:
    """Ajustes que hacen falta para restaurar y no son secretos."""
    return {
        "audit_retention_days": settings.audit_retention_days,
        "conversation_retention_days": settings.conversation_retention_days,
        "embedding_model": settings.embedding_model,
        "index_revision": settings.index_revision,
        "trace_retention_days": settings.trace_retention_days,
        "vector_alias": settings.vector_alias,
    }


def publisher_for(settings: Settings) -> ResticRepository:
    """Repositorio restic. Falla si falta la ruta o el archivo de contraseña."""
    password = settings.restic_password_file
    if not settings.restic_repository or password is None:
        from app.domain.errors import InvalidStateError

        message = "RESTIC_REPOSITORY y RESTIC_PASSWORD_FILE son obligatorios"
        raise InvalidStateError(message)
    root = resolve_file_store_path(settings)
    return ResticRepository(
        settings.restic_repository,
        password,
        prune_password_file=settings.restic_prune_password_file,
        protected_paths=(str(root),),
    )


def backup_coordinator(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    publisher: BackupPublisher | None = None,
) -> BackupCoordinator:
    """Coordinador con pg_dump, Qdrant y restic del entorno."""
    root = resolve_file_store_path(settings)
    return BackupCoordinator(
        session_factory,
        PgDumpClient(settings.database_url),
        QdrantSnapshots(settings.qdrant_url),
        OriginalsArchive(root),
        publisher if publisher is not None else publisher_for(settings),
        lock_seconds=settings.backup_lock_seconds,
        application_version=os.environ.get("ENGINE_RAG_VERSION", "0.1.0"),
        revision=os.environ.get("ENGINE_RAG_REVISION", "dev"),
        public_config=public_config(settings),
    )


def restore_coordinator(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    publisher: BackupPublisher | None = None,
) -> RestoreCoordinator:
    """Restauración con el mismo cliente de PostgreSQL y el cliente de Qdrant."""
    root = resolve_file_store_path(settings)
    client = create_qdrant_client(settings.qdrant_url)
    return RestoreCoordinator(
        session_factory,
        PgDumpClient(settings.database_url),
        QdrantSnapshots(settings.qdrant_url),
        OriginalsArchive(root),
        publisher if publisher is not None else publisher_for(settings),
        pseudonym_secret=settings.pseudonym_secret,
        vectors=QdrantTenantPoints(client),
        files=LocalFileStore(root),
    )


def export_store(settings: Settings) -> ExportFileStore:
    """Paquetes cifrados junto al almacén, en un directorio que el barrido ignora."""
    return ExportFileStore(resolve_file_store_path(settings))
