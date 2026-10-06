"""Conversión explícita entre filas de evaluación y entidades."""

from app.domain.enums import (
    EvaluationCaseStatus,
    EvaluationDatasetStatus,
    EvaluationRunStatus,
    EvaluationSplit,
)
from app.domain.evaluation import EvaluationCase, EvaluationDataset, EvaluationRun
from app.domain.evaluation_codec import (
    config_from_columns,
    failed_from_payload,
    failed_payload,
    fidelity_from_column,
    int_tuple,
    metrics_from_payload,
    metrics_payload,
    string_tuple,
)
from app.persistence.models.evaluation import (
    EvaluationCaseModel,
    EvaluationDatasetModel,
    EvaluationRunModel,
)


def dataset_to_domain(model: EvaluationDatasetModel) -> EvaluationDataset:
    """Mapea una fila de `evaluation_datasets`."""
    return EvaluationDataset(
        id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        version=model.version,
        status=EvaluationDatasetStatus(model.status),
        content_sha256=model.content_sha256,
        case_count=model.case_count,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def dataset_to_model(entity: EvaluationDataset) -> EvaluationDatasetModel:
    """Mapea un conjunto a fila ORM."""
    return EvaluationDatasetModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        name=entity.name,
        version=entity.version,
        status=entity.status.value,
        content_sha256=entity.content_sha256,
        case_count=entity.case_count,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def case_to_domain(model: EvaluationCaseModel) -> EvaluationCase:
    """Mapea una fila de `evaluation_cases`."""
    return EvaluationCase(
        id=model.id,
        tenant_id=model.tenant_id,
        dataset_id=model.dataset_id,
        case_key=model.case_key,
        version=model.version,
        status=EvaluationCaseStatus(model.status),
        split=EvaluationSplit(model.split),
        question=model.question,
        answerable=model.answerable,
        expected_sources=string_tuple(model.expected_sources, "expected_sources"),
        expected_pages=int_tuple(model.expected_pages, "expected_pages"),
        expected_facts=string_tuple(model.expected_facts, "expected_facts"),
        expected_patterns=string_tuple(model.expected_patterns, "expected_patterns"),
        tags=string_tuple(model.tags, "tags"),
        fidelity_label=fidelity_from_column(model.fidelity_label),
        label_version=model.label_version,
        labeled_answer_sha256=model.labeled_answer_sha256,
    )


def case_to_model(entity: EvaluationCase) -> EvaluationCaseModel:
    """Mapea un caso a fila ORM."""
    label = None if entity.fidelity_label is None else entity.fidelity_label.value
    return EvaluationCaseModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        dataset_id=entity.dataset_id,
        case_key=entity.case_key,
        version=entity.version,
        status=entity.status.value,
        split=entity.split.value,
        answerable=entity.answerable,
        question=entity.question,
        expected_sources=list(entity.expected_sources),
        expected_pages=list(entity.expected_pages),
        expected_facts=list(entity.expected_facts),
        expected_patterns=list(entity.expected_patterns),
        tags=list(entity.tags),
        fidelity_label=label,
        label_version=entity.label_version,
        labeled_answer_sha256=entity.labeled_answer_sha256,
    )


def run_to_domain(model: EvaluationRunModel) -> EvaluationRun:
    """Mapea una fila de `evaluation_runs`."""
    return EvaluationRun(
        id=model.id,
        tenant_id=model.tenant_id,
        dataset_id=model.dataset_id,
        version=model.version,
        status=EvaluationRunStatus(model.status),
        config=config_from_columns(
            corpus_sha256=model.corpus_sha256,
            parser_version=model.parser_version,
            chunker_version=model.chunker_version,
            embedding_version=model.embedding_version,
            index_version=model.index_version,
            top_k=model.top_k,
            min_retrieval_score=model.min_retrieval_score,
            prompt_version=model.prompt_version,
            llm_version=model.llm_version,
            dataset_version=model.dataset_version,
        ),
        metrics=metrics_from_payload(model.metrics),
        failed_cases=failed_from_payload(model.failed_cases),
        isolation_ok=model.isolation_ok,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def run_to_model(entity: EvaluationRun) -> EvaluationRunModel:
    """Mapea una corrida a fila ORM."""
    config = entity.config
    return EvaluationRunModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        dataset_id=entity.dataset_id,
        version=entity.version,
        status=entity.status.value,
        corpus_sha256=config.corpus_sha256,
        parser_version=config.parser_version,
        chunker_version=config.chunker_version,
        embedding_version=config.embedding_version,
        index_version=config.index_version,
        top_k=config.top_k,
        min_retrieval_score=config.min_retrieval_score,
        prompt_version=config.prompt_version,
        llm_version=config.llm_version,
        dataset_version=config.dataset_version,
        metrics=metrics_payload(entity.metrics),
        failed_cases=failed_payload(entity.failed_cases),
        isolation_ok=entity.isolation_ok,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )
