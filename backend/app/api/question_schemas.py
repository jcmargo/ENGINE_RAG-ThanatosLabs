"""Esquemas HTTP de preguntas. Sin `tenant_id`, score ni ruta."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.retrieval.contracts import MAX_TOP_K, MIN_TOP_K


class _IgnoreExtra(BaseModel):
    """Ignora un `tenant_id` enviado por el cliente."""

    model_config = ConfigDict(extra="ignore")


class QuestionRequestBody(_IgnoreExtra):
    """Pregunta, tope opcional y filtro opcional de documentos de esta empresa."""

    question: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=MIN_TOP_K, le=MAX_TOP_K)
    document_ids: list[UUID] | None = None
    conversation_id: UUID | None = None
    llm: str | None = Field(default=None, max_length=200)

    @field_validator("llm")
    @classmethod
    def blank_llm_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CitationResponse(_IgnoreExtra):
    """Cita pública construida desde metadatos recuperados."""

    document_id: UUID
    document_name: str
    page: int
    section: str | None
    excerpt: str


class DebugEvidenceResponse(_IgnoreExtra):
    """Fragmento de depuración. No incluye texto, vector ni prompt."""

    chunk_id: str
    document_id: UUID
    score: float
    position: int


class QuestionDebugResponse(_IgnoreExtra):
    """Traza de una pregunta. Solo la recibe un tenant admin que la pidió."""

    request_id: UUID
    embedding_version: str
    index_version: str
    hits_retrieved: int
    hits_after_validation: int
    hits_after_threshold: int
    returned: int
    rounds: int
    top_k: int
    min_retrieval_score: float
    embed_ms: float
    search_ms: float
    validate_ms: float
    generation_ms: float
    llm_called: bool
    token_estimate: int
    evidence: list[DebugEvidenceResponse]


class QuestionResponse(_IgnoreExtra):
    """Contrato público de una pregunta fundamentada."""

    request_id: UUID
    answer: str
    abstained: bool
    citations: list[CitationResponse]
    pipeline_version: str
    conversation_id: UUID
    message_id: UUID
    history_scope: str
    debug: QuestionDebugResponse | None = None


class ModelOptionResponse(_IgnoreExtra):
    """Modelo elegible. No incluye la URL ni la clave de una API externa."""

    id: str
    label: str
    provider: Literal["ollama", "external"]


class ModelListResponse(_IgnoreExtra):
    """Lista de modelos que esta instalación puede usar para responder."""

    items: list[ModelOptionResponse]
