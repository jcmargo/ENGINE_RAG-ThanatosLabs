"""Errores estables de la búsqueda en memoria."""


class RetrievalError(Exception):
    """Error de recuperación con un código estable."""

    code = "retrieval_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DimensionMismatchError(RetrievalError):
    """El vector no tiene la dimensión del índice."""

    code = "dimension_mismatch"


class EmptySampleCorpusError(RetrievalError):
    """El directorio no contiene documentos admitidos."""

    code = "empty_sample_corpus"
