"""Engine de PostgreSQL y sesiones por unidad de trabajo."""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 2


def create_database_engine(database_url: str) -> AsyncEngine:
    """Crea el único engine del proceso. No abre conexión ni crea tablas."""
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "timeout": _CONNECT_TIMEOUT_SECONDS,
            "command_timeout": _CONNECT_TIMEOUT_SECONDS,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Fabrica una sesión nueva para cada unidad de trabajo."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def postgres_is_ready(
    session_factory: async_sessionmaker[AsyncSession],
    timeout_seconds: float = 2.0,
) -> bool:
    """Ejecuta `SELECT 1`. No registra la URL ni el error del driver."""
    try:
        async with asyncio.timeout(timeout_seconds):
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
    except Exception:
        # El mensaje del driver puede incluir la URL y la contraseña.
        logger.info("readyz component=postgresql status=unavailable")
        return False
    return True
