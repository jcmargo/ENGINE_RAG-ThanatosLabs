"""Migraciones Alembic contra bases desechables. Fuera de la suite por defecto."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Environment, LogLevel, Settings
from app.main import create_app
from tests.integration.db_helpers import (
    EXPECTED_CONSTRAINTS,
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    MIGRATION_DB,
    constraint_names,
    database_url_for,
    drop_and_create_database,
    drop_database,
    index_names,
    public_table_names,
    run_alembic,
    session_factory_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def migration_database() -> None:
    asyncio.run(drop_and_create_database(MIGRATION_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(MIGRATION_DB))


def test_upgrade_head_creates_exact_tables_and_constraints(migration_database: None) -> None:
    url = database_url_for(MIGRATION_DB)
    completed = run_alembic("upgrade", "head", database_url=url)
    assert completed.returncode == 0

    async def _assert_schema() -> None:
        async with session_factory_for(MIGRATION_DB) as factory:
            tables = await public_table_names(factory)
            constraints = await constraint_names(factory)
            indexes = await index_names(factory)

        assert tables == EXPECTED_TABLES
        assert constraints >= EXPECTED_CONSTRAINTS
        assert indexes >= EXPECTED_INDEXES

    asyncio.run(_assert_schema())


def test_downgrade_base_and_upgrade_head_again(migration_database: None) -> None:
    url = database_url_for(MIGRATION_DB)
    run_alembic("upgrade", "head", database_url=url)
    downgrade = run_alembic("downgrade", "base", database_url=url)
    assert downgrade.returncode == 0
    upgrade = run_alembic("upgrade", "head", database_url=url)
    assert upgrade.returncode == 0

    async def _assert_schema() -> None:
        async with session_factory_for(MIGRATION_DB) as factory:
            tables = await public_table_names(factory)
            constraints = await constraint_names(factory)

        assert tables == EXPECTED_TABLES
        assert constraints >= EXPECTED_CONSTRAINTS

    asyncio.run(_assert_schema())


def test_fastapi_startup_does_not_modify_schema(migration_database: None) -> None:
    url = database_url_for(MIGRATION_DB)
    run_alembic("upgrade", "head", database_url=url)

    async def _tables_before() -> set[str]:
        async with session_factory_for(MIGRATION_DB) as factory:
            return await public_table_names(factory)

    before = asyncio.run(_tables_before())
    settings = Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        database_url=url,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200

    after = asyncio.run(_tables_before())
    assert after == before == EXPECTED_TABLES
