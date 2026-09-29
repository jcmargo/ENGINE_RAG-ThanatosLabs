"""Contrato sustituible de embeddings."""

from collections.abc import Sequence
from typing import Protocol

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"


class EmbeddingProvider(Protocol):
    """Vectoriza fragmentos y preguntas sin imponer un modelo concreto."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Devuelve un vector por fragmento, en el mismo orden."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Devuelve el vector de una pregunta."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensión fija de los vectores de este proveedor."""
        ...

    @property
    def model_version(self) -> str:
        """Identificador versionado del modelo de embeddings."""
        ...
