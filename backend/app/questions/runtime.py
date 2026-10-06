"""Arma la pregunta de producción en el primer uso. No abre el índice."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import Settings, resolve_model_cache_dir
from app.questions.service import QuestionService

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.generation.directory import ConfiguredLlmDirectory


def build_question_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    qdrant_client: AsyncQdrantClient,
    directory: ConfiguredLlmDirectory | None = None,
    *,
    prepare_encoder: bool = False,
) -> QuestionService:
    """Compone recuperación, el modelo elegido y catálogo. No crea la colección."""
    from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
    from app.generation.directory import ConfiguredLlmDirectory, RoutingLLMProvider
    from app.infrastructure.qdrant_store import QdrantVectorStore
    from app.questions.catalog import SqlAlchemyCatalogOpener
    from app.retrieval.service import RetrievalService

    embeddings = SentenceTransformerEmbeddingProvider(
        settings.embedding_model,
        cache_dir=resolve_model_cache_dir(settings),
        batch_size=settings.embed_batch_size,
    )
    if prepare_encoder:
        embeddings.embed_query("preparado")
    vectors = QdrantVectorStore(
        qdrant_client,
        embedding_model=embeddings.model_version,
        revision=settings.index_revision,
        dimension=embeddings.dimension,
        alias_name=settings.vector_alias,
        batch_size=settings.embed_batch_size,
    )
    retrieval = RetrievalService(
        embeddings,
        vectors,
        min_retrieval_score=settings.min_retrieval_score,
        index_version=settings.index_revision,
        max_question_chars=settings.max_question_chars,
    )
    llm = RoutingLLMProvider(
        directory if directory is not None else ConfiguredLlmDirectory(settings)
    )
    return QuestionService(
        retrieval,
        llm,
        SqlAlchemyCatalogOpener(session_factory),
        max_context_chars=settings.max_context_chars,
        min_retrieval_score=settings.min_retrieval_score,
        min_lexical_score=settings.min_lexical_score,
    )


def load_question_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    qdrant_client: AsyncQdrantClient,
) -> QuestionService:
    """Carga el codificador antes de aceptar la primera pregunta. No crea la colección."""
    logger.info("embeddings warm model=%s", settings.embedding_model)
    service = build_question_service(
        settings,
        session_factory,
        qdrant_client,
        prepare_encoder=True,
    )
    logger.info("embeddings warm ready model=%s", settings.embedding_model)
    return service
