"""Resultados de la búsqueda semántica en memoria."""

from dataclasses import dataclass

from app.documents.models import Chunk


@dataclass(frozen=True)
class SearchResult:
    """Fragmento recuperado, con puntuación y rango empezando en 1."""

    chunk: Chunk
    score: float
    position: int
