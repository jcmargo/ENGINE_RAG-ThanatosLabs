"""Parser de PDF textual mediante PyMuPDF."""

from collections.abc import Iterator
from typing import Protocol, cast

import pymupdf

from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.parsers.base import DocumentParseError
from app.documents.versions import PARSER_VERSION


class _PdfPage(Protocol):
    def get_text(self, option: str) -> str:
        """Texto de la página."""
        ...


class _PdfDocument(Protocol):
    needs_pass: int

    def close(self) -> None:
        """Cierra el documento."""
        ...

    def __iter__(self) -> Iterator[_PdfPage]:
        """Recorre las páginas."""
        ...


def _open_pdf(content: bytes) -> _PdfDocument:
    # PyMuPDF no anota `Document`: la llamada y la iteración quedan fuera del tipo.
    opened = pymupdf.open(stream=content, filetype="pdf")  # type: ignore[no-untyped-call]
    return cast(_PdfDocument, opened)


class PdfParser:
    """Extrae el texto de cada página. No aplica OCR ni abre PDF con contraseña."""

    def __init__(self, mime_type: str) -> None:
        self._mime_type = mime_type

    def parse(self, name: str, content: bytes) -> ExtractedDocument:
        try:
            document = _open_pdf(content)
        except Exception as error:
            message = "No se pudo leer el PDF"
            raise DocumentParseError(message) from error
        try:
            if document.needs_pass:
                message = "El PDF protegido con contraseña no está admitido"
                raise DocumentParseError(message)
            pages = tuple(
                ExtractedPage(number=index, text=page.get_text("text"), section=None)
                for index, page in enumerate(document, start=1)
            )
        except DocumentParseError:
            raise
        except Exception as error:
            message = "No se pudo leer el PDF"
            raise DocumentParseError(message) from error
        finally:
            document.close()
        return ExtractedDocument(
            name=name,
            mime_type=self._mime_type,
            pages=pages,
            parser_version=PARSER_VERSION,
        )
