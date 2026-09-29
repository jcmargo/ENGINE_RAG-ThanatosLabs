"""Extracción de documentos y chunking de Engine RAG."""

from app.documents.chunking import WordTokenCounter, chunk_document, document_content_hash
from app.documents.models import Chunk, ExtractedDocument, ExtractedPage
from app.documents.parsers.base import (
    DocumentParseError,
    EmptyDocumentError,
    UnsupportedDocumentError,
)
from app.documents.parsers.registry import (
    DOCX_MIME,
    MARKDOWN_MIME,
    PDF_MIME,
    TEXT_MIME,
    parse_document,
)
from app.documents.versions import (
    CHUNKER_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    PARSER_VERSION,
)

__all__ = [
    "CHUNKER_VERSION",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OVERLAP_TOKENS",
    "DOCX_MIME",
    "MARKDOWN_MIME",
    "PARSER_VERSION",
    "PDF_MIME",
    "TEXT_MIME",
    "Chunk",
    "DocumentParseError",
    "EmptyDocumentError",
    "ExtractedDocument",
    "ExtractedPage",
    "UnsupportedDocumentError",
    "WordTokenCounter",
    "chunk_document",
    "document_content_hash",
    "parse_document",
]
