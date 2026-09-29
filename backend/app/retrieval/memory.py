"""Índice vectorial temporal. No sustituye al repositorio Qdrant."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.documents.models import Chunk
from app.retrieval.errors import DimensionMismatchError
from app.retrieval.identifiers import contains_all, label_heading, opens_with_any
from app.retrieval.models import SearchResult


@dataclass(frozen=True)
class _Entry:
    chunk: Chunk
    vector: tuple[float, ...]


class InMemoryVectorIndex:
    """Guarda chunks y vectores, y ordena por producto punto."""

    def __init__(self, dimension: int) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            message = "La dimensión del índice debe ser un entero mayor que cero"
            raise ValueError(message)
        self._dimension = dimension
        self._entries: list[_Entry] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def add(self, chunk: Chunk, vector: Sequence[float]) -> None:
        """Almacena una copia del vector. Rechaza una dimensión distinta."""
        self._entries.append(_Entry(chunk=chunk, vector=_require_vector(vector, self._dimension)))

    def search(
        self,
        vector: Sequence[float],
        top_k: int,
        *,
        containing: Sequence[str] = (),
    ) -> tuple[SearchResult, ...]:
        """Devuelve como máximo `top_k` resultados, de mayor a menor puntuación.

        Con `containing`, recorre todo el índice y se queda con los fragmentos
        que incluyen esos textos. Los que los tienen al inicio van primero.
        """
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            message = "top_k debe ser un entero mayor que cero"
            raise ValueError(message)
        query = _require_vector(vector, self._dimension)
        required = tuple(item for item in containing if item.strip())
        scored = [
            (_dot(query, entry.vector), entry)
            for entry in self._entries
            if not required or contains_all(entry.chunk.text, required)
        ]
        if required:
            scored.sort(
                key=lambda item: (
                    0 if label_heading(item[1].chunk.text, required) else 1,
                    0 if opens_with_any(item[1].chunk.text, required) else 1,
                    -item[0],
                    item[1].chunk.id,
                )
            )
        else:
            scored.sort(key=lambda item: (-item[0], item[1].chunk.id))
        return tuple(
            SearchResult(chunk=entry.chunk, score=score, position=position)
            for position, (score, entry) in enumerate(scored[:top_k], start=1)
        )


def _require_vector(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(values) != dimension:
        message = f"La dimensión del vector es {len(values)} y el índice espera {dimension}"
        raise DimensionMismatchError(message)
    return tuple(float(value) for value in values)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )
