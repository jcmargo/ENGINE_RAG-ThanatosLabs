"""Detección de PDF, DOCX, TXT y Markdown por contenido, no por el header HTTP."""

from __future__ import annotations

import codecs
import zipfile
from pathlib import Path
from typing import Protocol, cast

import pymupdf

from app.documents.limits import (
    MAX_PDF_PAGES,
    UncompressedSizeError,
    assert_zip_uncompressed_within_limit,
    too_many_pdf_pages_message,
)
from app.documents.parsers.registry import DOCX_MIME, MARKDOWN_MIME, PDF_MIME, TEXT_MIME
from app.domain.errors import InvalidStateError

_UNSUPPORTED = "El tipo de archivo no está permitido"
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_PARTS = {"[Content_Types].xml", "word/document.xml"}
_TEXT_SUFFIXES = {
    ".txt": TEXT_MIME,
    ".md": MARKDOWN_MIME,
    ".markdown": MARKDOWN_MIME,
}
_READ_SIZE = 64 * 1024


def sniff_file(path: Path, filename: str) -> str:
    """Exige que la extensión y la estructura del archivo coincidan."""
    suffix = Path(filename).suffix.lower()
    header = _header(path)
    if suffix == ".pdf":
        _require_pdf(path, header)
        return PDF_MIME
    if suffix == ".docx":
        _require_docx(path, header)
        return DOCX_MIME
    media_type = _TEXT_SUFFIXES.get(suffix)
    if media_type is None:
        raise InvalidStateError(_UNSUPPORTED)
    _require_text(path, header)
    return media_type


def _header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(len(_PDF_MAGIC))


class _OpenedPdf(Protocol):
    """Superficie mínima de PyMuPDF usada para validar, no para extraer texto."""

    is_encrypted: bool
    page_count: int

    def close(self) -> None:
        """Cierra el documento."""


def _require_pdf(path: Path, header: bytes) -> None:
    if not header.startswith(_PDF_MAGIC):
        raise InvalidStateError(_UNSUPPORTED)
    try:
        # PyMuPDF no anota `open`: la llamada queda fuera del tipo.
        opened = pymupdf.open(path, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as error:
        raise InvalidStateError(_UNSUPPORTED) from error
    document = cast(_OpenedPdf, opened)
    try:
        if document.is_encrypted or document.page_count < 1:
            raise InvalidStateError(_UNSUPPORTED)
        if document.page_count > MAX_PDF_PAGES:
            raise InvalidStateError(too_many_pdf_pages_message())
    finally:
        document.close()


def _require_docx(path: Path, header: bytes) -> None:
    if not header.startswith(_ZIP_MAGIC) or not zipfile.is_zipfile(path):
        raise InvalidStateError(_UNSUPPORTED)
    try:
        with zipfile.ZipFile(path) as archive:
            assert_zip_uncompressed_within_limit(archive)
            names = set(archive.namelist())
    except UncompressedSizeError as error:
        raise InvalidStateError(error.message) from error
    except zipfile.BadZipFile as error:
        raise InvalidStateError(_UNSUPPORTED) from error
    if not names >= _DOCX_PARTS:
        raise InvalidStateError(_UNSUPPORTED)


def _require_text(path: Path, header: bytes) -> None:
    if header.startswith(_PDF_MAGIC) or header.startswith(_ZIP_MAGIC):
        raise InvalidStateError(_UNSUPPORTED)
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(_READ_SIZE)
                if not block:
                    break
                if b"\x00" in block:
                    raise InvalidStateError(_UNSUPPORTED)
                decoder.decode(block)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise InvalidStateError(_UNSUPPORTED) from error
