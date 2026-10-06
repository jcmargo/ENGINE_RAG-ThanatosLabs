"""Tope de descompresión ZIP y de páginas PDF sin reservar buffers enormes."""

from __future__ import annotations

import struct
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document

from app.documents.limits import (
    MAX_UNCOMPRESSED_BYTES,
    UncompressedSizeError,
    assert_zip_uncompressed_within_limit,
)
from app.documents.parsers.base import DocumentParseError
from app.documents.parsers.registry import DOCX_MIME, PDF_MIME, parse_document
from app.documents.sniff import sniff_file
from app.domain.errors import InvalidStateError

_OVERSIZE = "El archivo descomprimido supera el límite permitido"
_TOO_MANY_PAGES = "El PDF supera el número máximo de páginas permitido"
_CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b"</Types>"
)
_DOCUMENT_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    b"<w:body><w:p/></w:body></w:document>"
)


def test_max_uncompressed_is_four_times_upload_limit() -> None:
    assert MAX_UNCOMPRESSED_BYTES == 4 * 25 * 1024 * 1024


def test_assert_rejects_declared_size_without_reading_payload() -> None:
    payload = _docx_zip_with_declared_size(declared=MAX_UNCOMPRESSED_BYTES + 1)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        info = archive.getinfo("word/document.xml")
        assert info.file_size == MAX_UNCOMPRESSED_BYTES + 1
        assert info.compress_size == len(_DOCUMENT_XML)
        with pytest.raises(UncompressedSizeError, match="descomprimido"):
            assert_zip_uncompressed_within_limit(archive)


def test_sniff_rejects_docx_with_declared_oversize(tmp_path: Path) -> None:
    path = tmp_path / "nota.docx"
    path.write_bytes(_docx_zip_with_declared_size(declared=MAX_UNCOMPRESSED_BYTES + 1))
    with pytest.raises(InvalidStateError, match="descomprimido") as caught:
        sniff_file(path, "nota.docx")
    assert str(caught.value) == _OVERSIZE


def test_sniff_rejects_docx_when_cap_is_tiny(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.documents.limits.MAX_UNCOMPRESSED_BYTES", 64)
    path = tmp_path / "nota.docx"
    path.write_bytes(_docx_bytes(("Parrafo",)))
    with pytest.raises(InvalidStateError, match="descomprimido"):
        sniff_file(path, "nota.docx")


def test_sniff_rejects_pdf_with_too_many_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.documents.sniff.MAX_PDF_PAGES", 1)
    path = tmp_path / "nota.pdf"
    path.write_bytes(_pdf_bytes(("uno", "dos")))
    with pytest.raises(InvalidStateError, match="páginas") as caught:
        sniff_file(path, "nota.pdf")
    assert str(caught.value) == _TOO_MANY_PAGES


def test_parser_rejects_docx_with_declared_oversize() -> None:
    payload = _docx_zip_with_declared_size(declared=MAX_UNCOMPRESSED_BYTES + 1)
    with pytest.raises(DocumentParseError, match="descomprimido") as caught:
        parse_document("nota.docx", payload, DOCX_MIME)
    assert str(caught.value) == _OVERSIZE


def test_parser_rejects_docx_when_cap_is_tiny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.documents.limits.MAX_UNCOMPRESSED_BYTES", 64)
    with pytest.raises(DocumentParseError, match="descomprimido"):
        parse_document("nota.docx", _docx_bytes(("Parrafo",)), DOCX_MIME)


def test_sniff_still_accepts_small_pdf(tmp_path: Path) -> None:
    path = tmp_path / "nota.pdf"
    path.write_bytes(_pdf_bytes(("Pagina",)))
    assert sniff_file(path, "nota.pdf") == PDF_MIME


def _docx_zip_with_declared_size(*, declared: int) -> bytes:
    """DOCX mínimo con `word/document.xml` que declara `declared` sin reservarlo."""
    return _stored_zip(
        [
            ("[Content_Types].xml", _CONTENT_TYPES, None),
            ("word/document.xml", _DOCUMENT_XML, declared),
        ]
    )


def _stored_zip(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    locals_: list[bytes] = []
    centrals: list[bytes] = []
    offset = 0
    for name, data, forged in entries:
        name_bytes = name.encode("utf-8")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        uncompressed = len(data) if forged is None else forged
        local = (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                0,
                0,
                0,
                0,
                crc,
                len(data),
                uncompressed,
                len(name_bytes),
                0,
            )
            + name_bytes
            + data
        )
        central = (
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,
                20,
                0,
                0,
                0,
                0,
                crc,
                len(data),
                uncompressed,
                len(name_bytes),
                0,
                0,
                0,
                0,
                0,
                offset,
            )
            + name_bytes
        )
        locals_.append(local)
        centrals.append(central)
        offset += len(local)
    body = b"".join(locals_)
    central_dir = b"".join(centrals)
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(entries),
        len(entries),
        len(central_dir),
        len(body),
        0,
    )
    return body + central_dir + end


def _pdf_bytes(pages: tuple[str, ...]) -> bytes:
    pdf = pymupdf.open()
    try:
        for text in pages:
            page = pdf.new_page()
            if text:
                page.insert_textbox(pymupdf.Rect(72, 72, 540, 770), text)
        return bytes(pdf.tobytes())
    finally:
        pdf.close()


def _docx_bytes(paragraphs: tuple[str, ...]) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
