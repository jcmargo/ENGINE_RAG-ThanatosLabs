"""Contrato de parsers y errores estables de documento."""

from typing import Protocol

from app.documents.models import ExtractedDocument


class DocumentError(Exception):
    """Error de documento con un código estable."""

    code = "document_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedDocumentError(DocumentError):
    """El tipo MIME o la extensión no están admitidos."""

    code = "unsupported_document"


class EmptyDocumentError(DocumentError):
    """El documento no tiene texto utilizable."""

    code = "empty_document"


class DocumentParseError(DocumentError):
    """La librería no pudo leer el archivo. El mensaje no incluye su traza."""

    code = "document_parse_error"


class DocumentParser(Protocol):
    """Extrae un documento ya identificado por el registro."""

    def parse(self, name: str, content: bytes) -> ExtractedDocument:
        """Devuelve páginas estructuradas a partir de los bytes originales."""
        ...
