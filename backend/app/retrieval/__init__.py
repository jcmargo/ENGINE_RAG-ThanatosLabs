"""Búsqueda semántica: índice en memoria y recuperación de negocio."""

from app.retrieval.contracts import (
    Evidence,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)
from app.retrieval.errors import (
    DimensionMismatchError,
    EmptySampleCorpusError,
    RetrievalError,
)
from app.retrieval.memory import InMemoryVectorIndex
from app.retrieval.models import SearchResult
from app.retrieval.sample_search import LocalSearchHit, SampleSearchService
from app.retrieval.service import DenseCandidateRetriever, RetrievalService

__all__ = [
    "DenseCandidateRetriever",
    "DimensionMismatchError",
    "EmptySampleCorpusError",
    "Evidence",
    "InMemoryVectorIndex",
    "LocalSearchHit",
    "RetrievalError",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalTrace",
    "SampleSearchService",
    "SearchResult",
]
