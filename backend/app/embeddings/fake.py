"""Embeddings deterministas, sin red ni modelos."""

import hashlib
import math
from collections.abc import Sequence

FAKE_EMBEDDING_VERSION = "deterministic-fake-v1"


class DeterministicFakeEmbeddingProvider:
    """Vectores repetibles de dimensión fija. No aplica prefijos de ningún modelo."""

    def __init__(self, dimension: int = 32) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            message = "La dimensión debe ser un entero mayor que cero"
            raise ValueError(message)
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_version(self) -> str:
        return FAKE_EMBEDDING_VERSION

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        block = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < self._dimension:
            block = hashlib.sha256(block).digest()
            for byte in block:
                values.append((byte - 127.5) / 127.5)
                if len(values) == self._dimension:
                    break
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            head = [0.0] * self._dimension
            head[0] = 1.0
            return head
        return [value / norm for value in values]
