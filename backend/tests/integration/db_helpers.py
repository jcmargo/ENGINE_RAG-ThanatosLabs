"""Helpers compartidos para bases desechables de integración."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import get_settings, repository_root
from app.infrastructure.database import create_database_engine, create_session_factory

MIGRATION_DB = "engine_rag_migration_test"
PERSISTENCE_DB = "engine_rag_persistence_test"

EXPECTED_TABLES = frozenset(
    {
        "tenants",
        "users",
        "memberships",
        "auth_sessions",
        "api_keys",
        "activation_tokens",
        "alembic_version",
    }
)
EXPECTED_CONSTRAINTS = frozenset(
    {
        "uq_tenants_slug",
        "ck_tenants_status",
        "uq_users_email",
        "ck_users_status",
        "ck_users_system_role",
        "uq_memberships_tenant_user",
        "ck_memberships_role",
        "fk_memberships_tenant_id",
        "fk_memberships_user_id",
        "uq_auth_sessions_refresh_hash",
        "ck_auth_sessions_status",
        "fk_auth_sessions_user_id",
        "fk_auth_sessions_tenant_id",
        "fk_auth_sessions_replaced_by",
        "uq_api_keys_prefix",
        "ck_api_keys_status",
        "fk_api_keys_tenant_id",
        "ck_memberships_status",
        "uq_activation_tokens_token_hash",
        "ck_activation_tokens_status",
        "fk_activation_tokens_tenant_id",
        "fk_activation_tokens_user_id",
    }
)
EXPECTED_INDEXES = frozenset(
    {
        "ix_memberships_user_id",
        "uq_memberships_tenant_user",
        "ix_auth_sessions_user_id",
        "uq_auth_sessions_refresh_hash",
        "ix_api_keys_tenant_id",
        "uq_api_keys_prefix",
        "ix_activation_tokens_tenant_user",
        "uq_activation_tokens_token_hash",
    }
)


def database_url_for(db_name: str) -> str:
    """Sustituye el nombre de base en la URL tipada de Settings."""
    settings = get_settings()
    parts = urlsplit(settings.database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


def admin_database_url() -> str:
    """URL de mantenimiento para CREATE/DROP DATABASE (base `postgres`)."""
    return database_url_for("postgres")


async def drop_and_create_database(db_name: str) -> None:
    """Recrea una base desechable. No usa `engine_rag` como destino de pruebas."""
    engine = create_async_engine_autocommit(admin_database_url())
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()


async def drop_database(db_name: str) -> None:
    """Elimina una base desechable al terminar la suite."""
    engine = create_async_engine_autocommit(admin_database_url())
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    finally:
        await engine.dispose()


def create_async_engine_autocommit(url: str) -> AsyncEngine:
    """Engine con AUTOCOMMIT para CREATE/DROP DATABASE."""
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(url, isolation_level="AUTOCOMMIT")


def run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta Alembic desde la raíz con `RAG_DATABASE_URL` apuntando a la base de prueba."""
    env = os.environ.copy()
    env["RAG_DATABASE_URL"] = database_url
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=repository_root(),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@asynccontextmanager
async def session_factory_for(db_name: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Abre y cierra un engine contra una base desechable."""
    engine = create_database_engine(database_url_for(db_name))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def truncate_identity_tables(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Limpia filas entre pruebas sin tocar otras bases."""
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE activation_tokens, api_keys, auth_sessions, "
                "memberships, tenants, users CASCADE"
            )
        )
        await session.commit()


async def public_table_names(session_factory: async_sessionmaker[AsyncSession]) -> set[str]:
    """Nombres de tablas en el esquema public."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        return {row[0] for row in result.all()}


async def constraint_names(session_factory: async_sessionmaker[AsyncSession]) -> set[str]:
    """Nombres de restricciones en el esquema public."""
    async with session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint
                JOIN pg_namespace ON pg_namespace.oid = pg_constraint.connamespace
                WHERE pg_namespace.nspname = 'public'
                """
            )
        )
        return {row[0] for row in result.all()}


async def index_names(session_factory: async_sessionmaker[AsyncSession]) -> set[str]:
    """Nombres de índices en el esquema public."""
    async with session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
        )
        return {row[0] for row in result.all()}
