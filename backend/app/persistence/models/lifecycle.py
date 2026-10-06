"""Corridas de backup, tombstones y paquetes de exportación."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_BACKUP_RUNS_STATUS,
    FK_EXPORT_PACKAGES_TENANT_ID,
    IX_BACKUP_RUNS_ACTIVE,
    IX_EXPORT_PACKAGES_EXPIRES,
    UQ_EXPORT_PACKAGES_TOKEN_HASH,
)
from app.persistence.models.base import Base

_BACKUP_STATUS = "status IN ('running', 'succeeded', 'failed')"


class BackupRunModel(Base):
    """Una captura. `running` y no vencida bloquea borrados."""

    __tablename__ = "backup_runs"
    __table_args__ = (
        CheckConstraint(_BACKUP_STATUS, name=CK_BACKUP_RUNS_STATUS),
        Index(IX_BACKUP_RUNS_ACTIVE, "status", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TenantTombstoneModel(Base):
    """Borrado seudonimizado. No guarda nombre, email ni id en claro."""

    __tablename__ = "tenant_tombstones"

    tenant_hmac: Mapped[str] = mapped_column(String(64), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExportPackageModel(Base):
    """Paquete cifrado. El token en claro no se guarda."""

    __tablename__ = "export_packages"
    __table_args__ = (
        UniqueConstraint("token_hash", name=UQ_EXPORT_PACKAGES_TOKEN_HASH),
        Index(IX_EXPORT_PACKAGES_EXPIRES, "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_EXPORT_PACKAGES_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hex: Mapped[str] = mapped_column(String(24), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
