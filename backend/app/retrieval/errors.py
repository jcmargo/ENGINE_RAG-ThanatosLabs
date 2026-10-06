"""Errores estables de recuperación."""


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


class IncompatibleVectorIndexError(RetrievalError):
    """La colección existe con otra dimensión, distancia o descriptor."""

    code = "incompatible_vector_index"
