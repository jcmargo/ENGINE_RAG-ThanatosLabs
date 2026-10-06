"""Eventos de uso y agregados. El texto de la pregunta no entra en estas filas."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.entities import TenantContext
from app.domain.enums import QuestionOutcome, UsageKind
from app.domain.errors import InvalidStateError
from app.domain.history import DailyUsage, UsageTotals
from app.persistence.constraints import UQ_USAGE_EVENTS_SOURCE
from app.persistence.models.history import UsageEventModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork

_METRICS = """
    COUNT(*) FILTER (WHERE kind = 'upload') AS documents,
    COALESCE(SUM(byte_size) FILTER (WHERE kind = 'upload'), 0) AS byte_size,
    COALESCE(SUM(quantity) FILTER (WHERE kind = 'chunks'), 0) AS chunks,
    COUNT(*) FILTER (WHERE kind = 'question') AS questions,
    COUNT(*) FILTER (WHERE kind = 'question' AND outcome = 'abstained') AS abstentions,
    COUNT(*) FILTER (WHERE kind = 'question' AND outcome = 'error') AS errors,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE kind = 'question' AND latency_ms IS NOT NULL) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE kind = 'question' AND latency_ms IS NOT NULL) AS p95_ms
"""

DAILY_USAGE_SQL = text(
    f"""
    SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day, {_METRICS}
    FROM usage_events
    WHERE tenant_id = :tenant_id
      AND occurred_at >= :start
      AND occurred_at < :end
    GROUP BY 1
    ORDER BY 1
    """
)

GLOBAL_USAGE_SQL = text(
    f"""
    SELECT {_METRICS}
    FROM usage_events
    """
)


async def record_usage_event(
    unit: UnitOfWork,
    context: TenantContext,
    *,
    kind: UsageKind,
    source_id: UUID,
    occurred_at: datetime,
    byte_size: int | None = None,
    quantity: int = 0,
    outcome: QuestionOutcome | None = None,
    latency_ms: float | None = None,
    token_estimate: int | None = None,
) -> None:
    """Inserta un evento. Si el reintento repite la clave, no duplica la fila."""
    unit.require_open()
    statement = pg_insert(UsageEventModel).values(
        id=uuid4(),
        tenant_id=context.tenant_id,
        kind=kind.value,
        source_id=source_id,
        outcome=None if outcome is None else outcome.value,
        byte_size=byte_size,
        quantity=quantity,
        latency_ms=latency_ms,
        token_estimate=token_estimate,
        occurred_at=occurred_at,
    )
    await unit.session.execute(statement.on_conflict_do_nothing(constraint=UQ_USAGE_EVENTS_SOURCE))


def usage_totals_from_row(row: Mapping[str, object]) -> UsageTotals:
    """Lee un agregado. No hay columnas de texto en la fila."""
    return UsageTotals(
        documents=_as_int(row["documents"]),
        byte_size=_as_int(row["byte_size"]),
        chunks=_as_int(row["chunks"]),
        questions=_as_int(row["questions"]),
        abstentions=_as_int(row["abstentions"]),
        errors=_as_int(row["errors"]),
        p50_ms=_as_float(row["p50_ms"]),
        p95_ms=_as_float(row["p95_ms"]),
    )


def usage_day_from_row(row: Mapping[str, object]) -> DailyUsage:
    """Un día UTC con su agregado."""
    return DailyUsage(day=_as_date(row["day"]), totals=usage_totals_from_row(row))


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        message = "Agregado de uso inválido"
        raise InvalidStateError(message)
    return int(value)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        message = "Percentil de uso inválido"
        raise InvalidStateError(message)
    return float(value)


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    message = "Día de uso inválido"
    raise InvalidStateError(message)
