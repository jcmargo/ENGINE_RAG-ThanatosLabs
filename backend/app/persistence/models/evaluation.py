"""Datasets, casos y corridas de evaluación. El reporte no copia el documento."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_EVALUATION_CASES_FIDELITY,
    CK_EVALUATION_CASES_LABEL,
    CK_EVALUATION_CASES_SPLIT,
    CK_EVALUATION_CASES_STATUS,
    CK_EVALUATION_DATASETS_STATUS,
    CK_EVALUATION_RUNS_STATUS,
    FK_EVALUATION_CASES_DATASET_ID,
    FK_EVALUATION_CASES_TENANT_ID,
    FK_EVALUATION_DATASETS_TENANT_ID,
    FK_EVALUATION_RUNS_DATASET_ID,
    FK_EVALUATION_RUNS_TENANT_ID,
    IX_EVALUATION_CASES_TENANT_DATASET,
    IX_EVALUATION_DATASETS_TENANT_CREATED,
    IX_EVALUATION_RUNS_TENANT_DATASET,
    UQ_EVALUATION_CASES_KEY,
    UQ_EVALUATION_DATASETS_VERSION,
)
from app.persistence.models.base import Base

_DATASET_STATUS = "status IN ('draft', 'active', 'archived')"
_CASE_STATUS = "status IN ('active', 'excluded')"
_CASE_SPLIT = "split IN ('calibration', 'test')"
_FIDELITY = (
    "fidelity_label IS NULL OR fidelity_label IN "
    "('supported', 'partially_supported', 'unsupported')"
)
_LABEL_PAIR = (
    "(fidelity_label IS NULL AND labeled_answer_sha256 IS NULL) OR "
    "(fidelity_label IS NOT NULL AND labeled_answer_sha256 IS NOT NULL)"
)
_RUN_STATUS = "status IN ('pending', 'running', 'completed', 'failed')"


class EvaluationDatasetModel(Base):
    """Conjunto versionado. El JSONL de un cliente no vive en Git."""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name=UQ_EVALUATION_DATASETS_VERSION),
        CheckConstraint(_DATASET_STATUS, name=CK_EVALUATION_DATASETS_STATUS),
        CheckConstraint("case_count >= 1", name="ck_evaluation_datasets_case_count"),
        Index(IX_EVALUATION_DATASETS_TENANT_CREATED, "tenant_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_EVALUATION_DATASETS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationCaseModel(Base):
    """Caso de oro de una empresa. La pregunta no se escribe en logs."""

    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "case_key", name=UQ_EVALUATION_CASES_KEY),
        CheckConstraint(_CASE_STATUS, name=CK_EVALUATION_CASES_STATUS),
        CheckConstraint(_CASE_SPLIT, name=CK_EVALUATION_CASES_SPLIT),
        CheckConstraint(_FIDELITY, name=CK_EVALUATION_CASES_FIDELITY),
        CheckConstraint(_LABEL_PAIR, name=CK_EVALUATION_CASES_LABEL),
        CheckConstraint("label_version >= 1", name="ck_evaluation_cases_label_version"),
        Index(IX_EVALUATION_CASES_TENANT_DATASET, "tenant_id", "dataset_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_EVALUATION_CASES_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evaluation_datasets.id",
            name=FK_EVALUATION_CASES_DATASET_ID,
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    case_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    answerable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_sources: Mapped[object] = mapped_column(JSONB, nullable=False)
    expected_pages: Mapped[object] = mapped_column(JSONB, nullable=False)
    expected_facts: Mapped[object] = mapped_column(JSONB, nullable=False)
    expected_patterns: Mapped[object] = mapped_column(JSONB, nullable=False)
    tags: Mapped[object] = mapped_column(JSONB, nullable=False)
    fidelity_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label_version: Mapped[int] = mapped_column(Integer, nullable=False)
    labeled_answer_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EvaluationRunModel(Base):
    """Corrida. La configuración y las métricas no incluyen el documento."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(_RUN_STATUS, name=CK_EVALUATION_RUNS_STATUS),
        CheckConstraint("top_k >= 1 AND top_k <= 20", name="ck_evaluation_runs_top_k"),
        Index(IX_EVALUATION_RUNS_TENANT_DATASET, "tenant_id", "dataset_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_EVALUATION_RUNS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evaluation_datasets.id",
            name=FK_EVALUATION_RUNS_DATASET_ID,
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    corpus_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(255), nullable=False)
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    min_retrieval_score: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_version: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[object] = mapped_column(JSONB, nullable=False)
    failed_cases: Mapped[object] = mapped_column(JSONB, nullable=False)
    isolation_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
