"""Adaptador local de Sentence Transformers con prefijos E5 y carga perezosa."""

import logging
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.embeddings.base import DEFAULT_EMBEDDING_MODEL
from app.embeddings.errors import InvalidEmbeddingError

logger = logging.getLogger(__name__)

PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


class BatchEncoder(Protocol):
    """Superficie mínima del modelo, para pruebas sin descargarlo."""

    def encode(
        self,
        inputs: Sequence[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
        prompt: str,
    ) -> object: ...

    def get_embedding_dimension(self) -> int | None: ...


class SentenceTransformerEmbeddingProvider:
    """Modelo multilingüe local. Antepone los prefijos E5 y normaliza los vectores."""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        cache_dir: Path | None = None,
        batch_size: int = 32,
        encoder: BatchEncoder | None = None,
    ) -> None:
        if not model_name.strip():
            message = "El nombre del modelo de embeddings no puede estar vacío"
            raise ValueError(message)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            message = "batch_size debe ser un entero mayor que cero"
            raise ValueError(message)
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._encoder = encoder
        self._dimension: int | None = None

    @property
    def model_version(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            reported = self._require_encoder().get_embedding_dimension()
            if isinstance(reported, bool) or not isinstance(reported, int) or reported < 1:
                message = "El modelo no informó una dimensión válida"
                raise InvalidEmbeddingError(message)
            self._dimension = reported
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._encode(tuple(f"{PASSAGE_PREFIX}{text}" for text in texts))

    def embed_query(self, text: str) -> list[float]:
        vectors = self._encode((f"{QUERY_PREFIX}{text}",))
        return vectors[0]

    def _require_encoder(self) -> BatchEncoder:
        if self._encoder is None:
            self._encoder = _load_sentence_transformer(self._model_name, self._cache_dir)
        return self._encoder

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        started = time.perf_counter()
        encoded = self._require_encoder().encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            prompt="",
        )
        vectors = _as_vectors(encoded)
        if len(vectors) != len(texts):
            message = "El modelo devolvió un número de vectores distinto al de textos"
            raise InvalidEmbeddingError(message)
        width = self.dimension
        for vector in vectors:
            if len(vector) != width:
                message = f"El modelo devolvió dimensión {len(vector)} y se esperaba {width}"
                raise InvalidEmbeddingError(message)
            if any(not math.isfinite(value) for value in vector):
                message = "El modelo devolvió un valor no finito"
                raise InvalidEmbeddingError(message)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "embeddings model=%s version=%s dimension=%d count=%d duration_ms=%.3f",
            self._model_name,
            self.model_version,
            width,
            len(texts),
            elapsed_ms,
        )
        return vectors


def _load_sentence_transformer(model_name: str, cache_dir: Path | None) -> BatchEncoder:
    from sentence_transformers import SentenceTransformer

    cache_folder = None if cache_dir is None else str(cache_dir)
    try:
        model = SentenceTransformer(
            model_name,
            cache_folder=cache_folder,
            local_files_only=True,
        )
    except OSError:
        # La caché todavía no tiene los pesos; la segunda carga puede descargarlos.
        model = SentenceTransformer(
            model_name,
            cache_folder=cache_folder,
            local_files_only=False,
        )
    model.default_prompt_name = None
    return model


def _as_vectors(encoded: object) -> list[list[float]]:
    return [_as_row(row) for row in _as_matrix(encoded)]


def _as_matrix(encoded: object) -> list[object]:
    if isinstance(encoded, list):
        return list(encoded)
    tolist = getattr(encoded, "tolist", None)
    if not callable(tolist):
        message = "El codificador no devolvió una matriz de vectores"
        raise InvalidEmbeddingError(message)
    raw: object = tolist()
    if not isinstance(raw, list):
        message = "El codificador no devolvió una matriz de vectores"
        raise InvalidEmbeddingError(message)
    return list(raw)


def _as_row(row: object) -> list[float]:
    if isinstance(row, list | tuple):
        return [_as_float(value) for value in row]
    tolist = getattr(row, "tolist", None)
    if callable(tolist):
        raw: object = tolist()
        if isinstance(raw, list):
            return [_as_float(value) for value in raw]
    message = "El codificador no devolvió una fila de floats"
    raise InvalidEmbeddingError(message)


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "El codificador devolvió un valor que no es numérico"
        raise InvalidEmbeddingError(message)
    return float(value)
