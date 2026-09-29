"""Búsqueda semántica en memoria sobre fragmentos ya embedidos."""

from app.retrieval.errors import (
    DimensionMismatchError,
    EmptySampleCorpusError,
    RetrievalError,
)
from app.retrieval.memory import InMemoryVectorIndex
from app.retrieval.models import SearchResult
from app.retrieval.sample_search import LocalSearchHit, SampleSearchService

__all__ = [
    "DimensionMismatchError",
    "EmptySampleCorpusError",
    "InMemoryVectorIndex",
    "LocalSearchHit",
    "RetrievalError",
    "SampleSearchService",
    "SearchResult",
]
