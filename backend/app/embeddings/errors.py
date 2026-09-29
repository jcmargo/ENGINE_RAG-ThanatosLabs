"""Errores estables del borde de embeddings."""


class EmbeddingError(Exception):
    """Error de embeddings con un código estable."""

    code = "embedding_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidEmbeddingError(EmbeddingError):
    """El modelo devolvió vectores que no se pueden indexar."""

    code = "invalid_embedding"
