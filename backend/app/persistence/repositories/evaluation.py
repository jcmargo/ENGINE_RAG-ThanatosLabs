"""Evaluación de una empresa. El tenant sale solo de `TenantContext`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.entities import TenantContext
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.evaluation import EvaluationCase, EvaluationDataset, EvaluationRun
from app.persistence.evaluation_mapping import (
    case_to_domain,
    case_to_model,
    dataset_to_domain,
    dataset_to_model,
    run_to_domain,
    run_to_model,
)
from app.persistence.integrity import translate_integrity_error
from app.persistence.models.evaluation import (
    EvaluationCaseModel,
    EvaluationDatasetModel,
    EvaluationRunModel,
)
from app.persistence.scoped import TenantScopedRepository

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork

_DATASET_MISSING = "Conjunto de evaluación no encontrado"
_RUN_MISSING = "Corrida de evaluación no encontrada"


class SqlAlchemyEvaluationRepository(TenantScopedRepository):
    """Datasets, casos y corridas de una sola empresa."""

    def __init__(self, unit_of_work: UnitOfWork, context: TenantContext) -> None:
        super().__init__(unit_of_work, context)

    async def add_dataset(
        self,
        dataset: EvaluationDataset,
        cases: Sequence[EvaluationCase],
    ) -> EvaluationDataset:
        """Inserta el conjunto y sus casos. Un id de otra empresa no se acepta."""
        self._unit.require_open()
        if dataset.tenant_id != self.tenant_id:
            raise NotFoundError(_DATASET_MISSING)
        if dataset.case_count != len(cases):
            message = "El conjunto no coincide con sus casos"
            raise InvalidStateError(message)
        self._unit.session.add(dataset_to_model(dataset))
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        for case in cases:
            if case.tenant_id != self.tenant_id or case.dataset_id != dataset.id:
                raise NotFoundError(_DATASET_MISSING)
            self._unit.session.add(case_to_model(case))
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return dataset

    async def get_dataset(self, dataset_id: UUID) -> EvaluationDataset:
        """Conjunto de esta empresa, o ausencia si es de otra."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(EvaluationDatasetModel).where(
                EvaluationDatasetModel.tenant_id == self.tenant_id,
                EvaluationDatasetModel.id == dataset_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_DATASET_MISSING)
        return dataset_to_domain(model)

    async def list_cases(self, dataset_id: UUID) -> tuple[EvaluationCase, ...]:
        """Casos del conjunto de esta empresa, ordenados por id externo."""
        self._unit.require_open()
        await self.get_dataset(dataset_id)
        result = await self._unit.session.execute(
            select(EvaluationCaseModel)
            .where(
                EvaluationCaseModel.tenant_id == self.tenant_id,
                EvaluationCaseModel.dataset_id == dataset_id,
            )
            .order_by(EvaluationCaseModel.case_key)
        )
        return tuple(case_to_domain(model) for model in result.scalars().all())

    async def add_run(self, run: EvaluationRun) -> EvaluationRun:
        """Inserta una corrida de esta empresa."""
        self._unit.require_open()
        if run.tenant_id != self.tenant_id:
            raise NotFoundError(_RUN_MISSING)
        await self.get_dataset(run.dataset_id)
        self._unit.session.add(run_to_model(run))
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return run

    async def get_run(self, run_id: UUID) -> EvaluationRun:
        """Corrida de esta empresa, o ausencia si es de otra."""
        self._unit.require_open()
        result = await self._unit.session.execute(
            select(EvaluationRunModel).where(
                EvaluationRunModel.tenant_id == self.tenant_id,
                EvaluationRunModel.id == run_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_RUN_MISSING)
        return run_to_domain(model)
