"""Embeddings locales sustituibles."""

from app.embeddings.base import DEFAULT_EMBEDDING_MODEL, EmbeddingProvider
from app.embeddings.fake import DeterministicFakeEmbeddingProvider
from app.embeddings.sentence_transformer import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    SentenceTransformerEmbeddingProvider,
)
from app.embeddings.tokens import HuggingFaceTokenCounter

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "PASSAGE_PREFIX",
    "QUERY_PREFIX",
    "DeterministicFakeEmbeddingProvider",
    "EmbeddingProvider",
    "HuggingFaceTokenCounter",
    "SentenceTransformerEmbeddingProvider",
]
