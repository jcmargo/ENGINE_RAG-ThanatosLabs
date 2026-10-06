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
        "documents",
        "document_versions",
        "ingestion_jobs",
        "conversations",
        "messages",
        "query_traces",
        "feedback",
        "usage_events",
        "question_idempotency",
        "evaluation_datasets",
        "evaluation_cases",
        "evaluation_runs",
        "audit_events",
        "rate_limit_buckets",
        "backup_runs",
        "tenant_tombstones",
        "export_packages",
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
        "ck_documents_status",
        "uq_documents_current_version",
        "fk_documents_tenant_id",
        "ck_document_versions_status",
        "ck_document_versions_progress",
        "uq_document_versions_document_number",
        "fk_document_versions_tenant_id",
        "fk_document_versions_document_id",
        "ck_ingestion_jobs_type",
        "ck_ingestion_jobs_status",
        "ck_ingestion_jobs_attempts",
        "ck_ingestion_jobs_progress",
        "ck_ingestion_jobs_lease",
        "fk_ingestion_jobs_tenant_id",
        "fk_ingestion_jobs_document_id",
        "fk_ingestion_jobs_version_id",
        "ck_conversations_owner_kind",
        "ck_conversations_history_scope",
        "fk_conversations_tenant_id",
        "ck_messages_role",
        "fk_messages_tenant_id",
        "fk_messages_conversation_id",
        "uq_query_traces_tenant_request",
        "ck_query_traces_result",
        "fk_query_traces_tenant_id",
        "fk_query_traces_message_id",
        "uq_feedback_message_author",
        "ck_feedback_rating",
        "ck_feedback_author_kind",
        "fk_feedback_tenant_id",
        "fk_feedback_message_id",
        "uq_usage_events_tenant_source_kind",
        "ck_usage_events_kind",
        "ck_usage_events_outcome",
        "fk_usage_events_tenant_id",
        "uq_question_idempotency_owner_key",
        "ck_question_idempotency_status",
        "ck_question_idempotency_owner_kind",
        "fk_question_idempotency_tenant_id",
        "ck_evaluation_datasets_status",
        "ck_evaluation_datasets_case_count",
        "uq_evaluation_datasets_tenant_name_version",
        "fk_evaluation_datasets_tenant_id",
        "ck_evaluation_cases_status",
        "ck_evaluation_cases_split",
        "ck_evaluation_cases_fidelity",
        "ck_evaluation_cases_label",
        "ck_evaluation_cases_label_version",
        "uq_evaluation_cases_dataset_key",
        "fk_evaluation_cases_tenant_id",
        "fk_evaluation_cases_dataset_id",
        "ck_evaluation_runs_status",
        "ck_evaluation_runs_top_k",
        "fk_evaluation_runs_tenant_id",
        "fk_evaluation_runs_dataset_id",
        "ck_audit_events_result",
        "fk_audit_events_tenant_id",
        "pk_rate_limit_buckets",
        "ck_backup_runs_status",
        "fk_export_packages_tenant_id",
        "uq_export_packages_token_hash",
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
        "ix_documents_tenant_created",
        "uq_documents_current_version",
        "ix_document_versions_document_id",
        "uq_document_versions_document_number",
        "uq_document_versions_tenant_sha256_active",
        "ix_ingestion_jobs_document_id",
        "ix_ingestion_jobs_pending",
        "ix_conversations_owner",
        "ix_messages_conversation",
        "ix_messages_tenant_request",
        "uq_query_traces_tenant_request",
        "ix_query_traces_expires",
        "uq_feedback_message_author",
        "ix_usage_events_tenant_occurred",
        "uq_usage_events_tenant_source_kind",
        "uq_question_idempotency_owner_key",
        "ix_evaluation_datasets_tenant_created",
        "uq_evaluation_datasets_tenant_name_version",
        "ix_evaluation_cases_tenant_dataset",
        "uq_evaluation_cases_dataset_key",
        "ix_evaluation_runs_tenant_dataset",
        "ix_audit_events_created",
        "pk_rate_limit_buckets",
        "ix_backup_runs_active",
        "ix_export_packages_expires",
        "uq_export_packages_token_hash",
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
                "TRUNCATE export_packages, backup_runs, tenant_tombstones, "
                "feedback, query_traces, messages, question_idempotency, "
                "usage_events, conversations, ingestion_jobs, document_versions, documents, "
                "evaluation_runs, evaluation_cases, evaluation_datasets, "
                "audit_events, rate_limit_buckets, "
                "activation_tokens, api_keys, auth_sessions, "
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
