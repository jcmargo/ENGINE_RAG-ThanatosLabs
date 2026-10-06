"""Ventana fija. PostgreSQL la comparte entre procesos; los tests usan memoria."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_INSERT = text(
    """
    INSERT INTO rate_limit_buckets (bucket_key, window_start, hit_count)
    VALUES (:bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key, window_start)
    DO UPDATE SET hit_count = rate_limit_buckets.hit_count + 1
    RETURNING hit_count
    """
)


@dataclass(frozen=True)
class RateDecision:
    """Cupo consumido. `retry_after_seconds` es lo que falta de la ventana."""

    allowed: bool
    retry_after_seconds: int


class Clock(Protocol):
    """Reloj UTC inyectable."""

    def now(self) -> datetime:
        """Instante actual."""


class RateLimiter(Protocol):
    """Consume un golpe del bucket. Un fallo de infraestructura se propaga."""

    async def consume(self, bucket_key: str, *, limit: int, window_seconds: int) -> RateDecision:
        """Incrementa el contador atómico y dice si todavía cabe."""


def window_bounds(now: datetime, window_seconds: int) -> tuple[datetime, int]:
    """Inicio de la ventana y segundos hasta la siguiente."""
    epoch = int(now.timestamp())
    start = epoch - (epoch % window_seconds)
    remaining = window_seconds - (epoch % window_seconds)
    if remaining <= 0:
        remaining = window_seconds
    return datetime.fromtimestamp(start, UTC), remaining


class MemoryRateLimiter:
    """Contador del proceso. La suite hermética no abre PostgreSQL por request."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = asyncio.Lock()

    async def consume(self, bucket_key: str, *, limit: int, window_seconds: int) -> RateDecision:
        """Incrementa en memoria."""
        start, retry_after = window_bounds(self._clock.now(), window_seconds)
        key = (bucket_key, int(start.timestamp()))
        async with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
        return RateDecision(allowed=count <= limit, retry_after_seconds=retry_after)


class PostgresRateLimiter:
    """Un `INSERT ... ON CONFLICT` compartido por todas las réplicas de la API."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def consume(self, bucket_key: str, *, limit: int, window_seconds: int) -> RateDecision:
        """Incrementa en PostgreSQL y devuelve el conteo ya confirmado."""
        start, retry_after = window_bounds(self._clock.now(), window_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                _INSERT,
                {"bucket_key": bucket_key, "window_start": start},
            )
            count = int(result.scalar_one())
            await session.commit()
        return RateDecision(allowed=count <= limit, retry_after_seconds=retry_after)
