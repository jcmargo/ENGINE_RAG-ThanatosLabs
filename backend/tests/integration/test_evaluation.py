"""Persistencia de evaluación y ausencia entre empresas."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.enums import EvaluationRunStatus
from app.domain.errors import NotFoundError
from app.domain.evaluation import (
    CountRatio,
    EvaluationConfig,
    EvaluationMetrics,
    EvaluationRun,
    ReciprocalMean,
    config_fingerprint,
)
from app.evaluation.context import context_for_evaluation
from app.evaluation.store import persist_dataset, persist_run
from app.persistence.repositories.evaluation import SqlAlchemyEvaluationRepository
from app.persistence.unit_of_work import UnitOfWork
from tests.factories.identity import make_tenant
from tests.integration.db_helpers import (
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    session_factory_for,
)

pytestmark = pytest.mark.integration

EVALUATION_DB = "engine_rag_evaluation_test"
_STAMP = datetime(2026, 10, 5, tzinfo=UTC)
_QUESTION = "PREGUNTA-EVAL-NO-LOG"


@pytest.fixture(scope="module")
def evaluation_database() -> Iterator[None]:
    """Base desechable. No usa `engine_rag`."""
    asyncio.run(drop_and_create_database(EVALUATION_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(EVALUATION_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(EVALUATION_DB))


def test_dataset_and_run_are_absent_for_another_tenant(evaluation_database: None) -> None:
    asyncio.run(_round_trip())


async def _round_trip() -> None:
    async with session_factory_for(EVALUATION_DB) as factory:
        async with UnitOfWork(factory) as unit:
            tenant_a = await unit.tenants.add(make_tenant(slug="eval-alfa", name="Eval Alfa"))
            tenant_b = await unit.tenants.add(make_tenant(slug="eval-beta", name="Eval Beta"))
            await unit.commit()
        text = _jsonl()
        dataset_id = await persist_dataset(
            factory,
            tenant_id=tenant_a.id,
            name="lumen",
            version="v1",
            text=text,
            corpus_names=frozenset({"vacaciones.md"}),
            created_at=_STAMP,
            new_id=uuid4,
        )
        async with UnitOfWork(factory) as unit:
            visible = SqlAlchemyEvaluationRepository(unit, context_for_evaluation(tenant_a.id))
            hidden = SqlAlchemyEvaluationRepository(unit, context_for_evaluation(tenant_b.id))
            dataset = await visible.get_dataset(dataset_id)
            cases = await visible.list_cases(dataset_id)
            by_key = {case.case_key: case for case in cases}
            assert dataset.case_count == 4
            assert by_key["t-uno"].question == _QUESTION
            with pytest.raises(NotFoundError):
                await hidden.get_dataset(dataset_id)
            run = _run(tenant_a.id, dataset_id)
        await persist_run(factory, run)
        async with UnitOfWork(factory) as unit:
            visible = SqlAlchemyEvaluationRepository(unit, context_for_evaluation(tenant_a.id))
            hidden = SqlAlchemyEvaluationRepository(unit, context_for_evaluation(tenant_b.id))
            stored = await visible.get_run(run.id)
            assert stored.version == run.version
            assert stored.metrics.hit_at_5.numerator == 1
            assert stored.failed_cases == ()
            with pytest.raises(NotFoundError):
                await hidden.get_run(run.id)


def _run(tenant_id: UUID, dataset_id: UUID) -> EvaluationRun:
    config = EvaluationConfig(
        corpus_sha256="a" * 64,
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version="deterministic-fake-v1",
        index_version="eval-reference-v1",
        top_k=5,
        min_retrieval_score=1.0,
        prompt_version="prompt-v4",
        llm_version="deterministic-fake-llm-v1",
        dataset_version="v1",
    )
    metrics = EvaluationMetrics(
        hit_at_5=CountRatio(1, 1),
        mrr=ReciprocalMean(1, 1, 1),
        citation=CountRatio(1, 1),
        unanswerable_abstention=CountRatio(1, 1),
        expected_facts=CountRatio(1, 1),
        uncited_answers=CountRatio(0, 1),
        fidelity_unsupported=CountRatio(0, 1),
        retrieval_latency_ms_sum=3.0,
        retrieval_latency_count=2,
        labels_complete=True,
    )
    return EvaluationRun(
        id=uuid4(),
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        version=config_fingerprint(config),
        status=EvaluationRunStatus.COMPLETED,
        config=config,
        metrics=metrics,
        failed_cases=(),
        isolation_ok=True,
        created_at=_STAMP,
        updated_at=_STAMP,
    )


def _jsonl() -> str:
    rows = []
    for case_id, split in (
        ("c-uno", "calibration"),
        ("c-dos", "calibration"),
        ("t-uno", "test"),
        ("t-dos", "test"),
    ):
        question = _QUESTION if case_id == "t-uno" else f"pregunta {case_id}"
        rows.append(
            '{"id":"'
            + case_id
            + '","question":"'
            + question
            + '","answerable":true,"expected_sources":["vacaciones.md"],'
            + '"expected_facts":["18 días"],"expected_patterns":[],"split":"'
            + split
            + '","tags":["numero"],"fidelity":"supported","label_version":1}'
        )
    return "\n".join(rows) + "\n"
