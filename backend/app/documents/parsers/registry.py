"""Selección de parser por tipo MIME, con extensión compatible."""

from pathlib import Path

from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.normalize import normalize_text
from app.documents.parsers.base import (
    DocumentParseError,
    DocumentParser,
    EmptyDocumentError,
    UnsupportedDocumentError,
)
from app.documents.parsers.docx import DocxParser
from app.documents.parsers.pdf import PdfParser
from app.documents.parsers.text import TextParser

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
MARKDOWN_MIME = "text/markdown"

_EXTENSIONS: dict[str, frozenset[str]] = {
    PDF_MIME: frozenset({".pdf"}),
    DOCX_MIME: frozenset({".docx"}),
    TEXT_MIME: frozenset({".txt"}),
    MARKDOWN_MIME: frozenset({".md", ".markdown"}),
}

_PARSERS: dict[str, DocumentParser] = {
    PDF_MIME: PdfParser(PDF_MIME),
    DOCX_MIME: DocxParser(DOCX_MIME),
    TEXT_MIME: TextParser(TEXT_MIME),
    MARKDOWN_MIME: TextParser(MARKDOWN_MIME),
}


def parse_document(name: str, content: bytes, mime_type: str) -> ExtractedDocument:
    """Elige parser por MIME admitido y exige una extensión compatible."""
    media_type = mime_type.split(";", maxsplit=1)[0].strip().lower()
    extension = Path(name).suffix.lower()
    allowed_extensions = _EXTENSIONS.get(media_type)
    if allowed_extensions is None or extension not in allowed_extensions:
        message = (
            f"El tipo {media_type or '(vacío)'} con extensión "
            f"{extension or '(sin extensión)'} no está admitido"
        )
        raise UnsupportedDocumentError(message)
    parser = _PARSERS[media_type]
    extracted = _parse_with_domain_errors(parser, name, content)
    pages = tuple(
        ExtractedPage(
            number=page.number,
            text=normalize_text(page.text),
            section=page.section,
        )
        for page in extracted.pages
        if normalize_text(page.text)
    )
    if not pages:
        message = "El documento no contiene texto utilizable"
        raise EmptyDocumentError(message)
    return ExtractedDocument(
        name=extracted.name,
        mime_type=media_type,
        pages=pages,
        parser_version=extracted.parser_version,
    )


def _parse_with_domain_errors(
    parser: DocumentParser,
    name: str,
    content: bytes,
) -> ExtractedDocument:
    try:
        return parser.parse(name, content)
    except (DocumentParseError, EmptyDocumentError, UnsupportedDocumentError):
        raise
    except Exception as error:
        message = "No se pudo leer el documento"
        raise DocumentParseError(message) from error
