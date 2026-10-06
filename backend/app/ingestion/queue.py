"""Puerto de la cola. Un adaptador distinto puede sustituir a PostgreSQL."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.enums import IngestionJobType


@dataclass(frozen=True)
class ClaimedJob:
    """Trabajo alquilado. No incluye texto ni bytes del documento."""

    id: UUID
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    job_type: IngestionJobType
    attempts: int
    max_attempts: int
    progress: int
    replacement_version_id: UUID | None
    lease_owner: str
    origin_request_id: UUID | None = None


@dataclass(frozen=True)
class RetryResult:
    """Resultado de reprogramar un fallo transitorio."""

    accepted: bool
    dead: bool


class JobQueue(Protocol):
    """Cola con lease. Las transacciones no cubren parseo ni Qdrant."""

    async def claim_next(
        self,
        owner: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> ClaimedJob | None:
        """Reclama un pendiente o un lease vencido."""

    async def heartbeat(
        self,
        job_id: UUID,
        owner: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> bool:
        """Alarga el vencimiento si el dueño sigue siendo el mismo."""

    async def still_owned(self, job_id: UUID, owner: str, now: datetime) -> bool:
        """Indica si el lease sigue vigente para ese dueño."""

    async def set_progress(self, job_id: UUID, owner: str, progress: int, now: datetime) -> bool:
        """Anota el avance sin soltar el lease."""

    async def ensure_replacement(
        self,
        job_id: UUID,
        owner: str,
        candidate: UUID,
        now: datetime,
    ) -> UUID | None:
        """Fija el id de la revisión nueva, o devuelve el que ya existía."""

    async def complete(self, job_id: UUID, owner: str, now: datetime) -> bool:
        """Cierra el trabajo si el lease sigue siendo de este dueño."""

    async def retry(
        self,
        job_id: UUID,
        owner: str,
        *,
        now: datetime,
        available_at: datetime,
        error_code: str,
    ) -> RetryResult:
        """Devuelve el trabajo a la cola o lo declara muerto al agotar intentos."""

    async def fail_permanent(
        self,
        job_id: UUID,
        owner: str,
        *,
        now: datetime,
        error_code: str,
    ) -> bool:
        """Cierra el trabajo sin reprogramarlo."""

    async def release(self, job_id: UUID, owner: str, now: datetime) -> bool:
        """Suelta el lease y deja el trabajo pendiente de inmediato."""

    async def finalize_consistent(self, now: datetime) -> int:
        """Cierra trabajos cuya versión o borrado ya quedó confirmado."""
