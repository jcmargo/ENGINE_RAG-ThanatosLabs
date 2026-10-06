"""Persiste conjuntos y corridas. El tenant sale del slug guardado."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import NotFoundError
from app.domain.evaluation import EvaluationRun
from app.evaluation.context import context_for_evaluation
from app.evaluation.importing import build_dataset, load_cases
from app.persistence.repositories.evaluation import SqlAlchemyEvaluationRepository
from app.persistence.unit_of_work import UnitOfWork


async def tenant_id_for_slug(
    session_factory: async_sessionmaker[AsyncSession],
    slug: str,
) -> UUID:
    """Empresa ya creada. Un slug desconocido es ausencia."""
    async with UnitOfWork(session_factory) as unit:
        try:
            tenant = await unit.tenants.get_by_slug(slug)
        except NotFoundError as error:
            message = "La empresa no existe"
            raise NotFoundError(message) from error
        return tenant.id


async def persist_dataset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: UUID,
    name: str,
    version: str,
    text: str,
    corpus_names: frozenset[str],
    created_at: datetime,
    new_id: Callable[[], UUID],
) -> UUID:
    """Valida e inserta el conjunto de esa empresa."""
    dataset_id = new_id()
    cases = load_cases(
        text,
        corpus_names,
        version=version,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        new_id=new_id,
    )
    dataset = build_dataset(
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        name=name,
        version=version,
        text=text,
        cases=cases,
        created_at=created_at,
    )
    context = context_for_evaluation(tenant_id)
    async with UnitOfWork(session_factory) as unit:
        repository = SqlAlchemyEvaluationRepository(unit, context)
        await repository.add_dataset(dataset, cases)
        await unit.commit()
    return dataset_id


async def persist_run(
    session_factory: async_sessionmaker[AsyncSession],
    run: EvaluationRun,
) -> None:
    """Inserta la corrida si el conjunto es de la misma empresa."""
    context = context_for_evaluation(run.tenant_id)
    async with UnitOfWork(session_factory) as unit:
        repository = SqlAlchemyEvaluationRepository(unit, context)
        await repository.add_run(run)
        await unit.commit()
