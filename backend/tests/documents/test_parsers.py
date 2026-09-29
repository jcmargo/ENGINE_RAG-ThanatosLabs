"""Parsers de PDF, DOCX, TXT y Markdown."""

from io import BytesIO

import pymupdf
import pytest
from docx import Document

from app.documents.chunking import chunk_document, document_content_hash
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


@pytest.mark.parametrize(
    ("name", "mime_type", "content", "expected"),
    [
        ("nota.txt", TEXT_MIME, b"Hola texto", "Hola texto"),
        ("nota.md", MARKDOWN_MIME, b"# Titulo\n\nHola markdown", "Hola markdown"),
        ("nota.markdown", MARKDOWN_MIME, b"Hola markdown largo", "Hola markdown largo"),
        ("nota.docx", DOCX_MIME, None, "Hola docx"),
        ("nota.pdf", PDF_MIME, None, "Hola pdf"),
    ],
)
def test_supported_formats_produce_extracted_documents(
    name: str,
    mime_type: str,
    content: bytes | None,
    expected: str,
) -> None:
    payload = content
    if name.endswith(".docx"):
        payload = _docx_bytes(("Hola docx",))
    if name.endswith(".pdf"):
        payload = _pdf_bytes(("Hola pdf",))
    assert payload is not None
    document = parse_document(name, payload, mime_type)

    assert document.name == name
    assert document.mime_type == mime_type
    assert document.parser_version == "parser-v1"
    assert document.pages
    assert all(page.number >= 1 for page in document.pages)
    assert expected in "\n".join(page.text for page in document.pages)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("El niño pidió ñame en A Coruña.", "El niño pidió ñame en A Coruña."),
        ("a\u0301rbol", "árbol"),
    ],
)
def test_spanish_unicode_roundtrip(raw: str, expected: str) -> None:
    document = parse_document("nota.txt", raw.encode(), TEXT_MIME)

    assert document.pages[0].text == expected
    assert document.pages[0].number == 1


def test_plain_text_with_charset_parameter_is_accepted() -> None:
    document = parse_document("nota.txt", b"hola", "text/plain; charset=utf-8")

    assert document.mime_type == TEXT_MIME
    assert document.pages[0].text == "hola"


def test_pdf_of_two_pages_does_not_mix_chunks() -> None:
    content = _pdf_bytes(("SOLO_PAGINA_UNO codigo A-17", "SOLO_PAGINA_DOS codigo B-23"))
    document = parse_document("notas.pdf", content, PDF_MIME)
    chunks = chunk_document(
        document,
        document_hash=document_content_hash(content),
        document_version="v1",
        max_tokens=20,
        overlap_tokens=0,
    )

    assert [page.number for page in document.pages] == [1, 2]
    assert chunks
    assert {chunk.page_start for chunk in chunks} == {1, 2}
    for chunk in chunks:
        assert chunk.page_start == chunk.page_end
        assert chunk.page_start >= 1
        has_first = "SOLO_PAGINA_UNO" in chunk.text
        has_second = "SOLO_PAGINA_DOS" in chunk.text
        assert not (has_first and has_second)


@pytest.mark.parametrize(
    ("name", "mime_type", "content"),
    [
        ("vacio.txt", TEXT_MIME, b" \n\t "),
        ("vacio.md", MARKDOWN_MIME, b""),
    ],
)
def test_empty_text_documents_raise(name: str, mime_type: str, content: bytes) -> None:
    with pytest.raises(EmptyDocumentError) as caught:
        parse_document(name, content, mime_type)

    assert caught.value.code == "empty_document"


def test_blank_pdf_raises_empty_document() -> None:
    with pytest.raises(EmptyDocumentError) as caught:
        parse_document("vacio.pdf", _pdf_bytes(("",)), PDF_MIME)

    assert caught.value.code == "empty_document"


@pytest.mark.parametrize(
    ("name", "mime_type", "content"),
    [
        ("notas.pdf", TEXT_MIME, b"hola"),
        ("notas.txt", PDF_MIME, b"%PDF-1.7"),
        ("notas", TEXT_MIME, b"hola"),
        ("notas.exe", "application/octet-stream", b"hola"),
    ],
)
def test_unsupported_type_or_extension_raises(name: str, mime_type: str, content: bytes) -> None:
    with pytest.raises(UnsupportedDocumentError) as caught:
        parse_document(name, content, mime_type)

    assert caught.value.code == "unsupported_document"


def test_corrupt_docx_hides_library_trace() -> None:
    with pytest.raises(DocumentParseError) as caught:
        parse_document("nota.docx", b"esto-no-es-un-docx", DOCX_MIME)

    assert str(caught.value) == "No se pudo leer el DOCX"
    assert "Traceback" not in str(caught.value)
    assert caught.value.__cause__ is not None


def test_pdf_library_failure_hides_library_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("fitz internal boom")

    monkeypatch.setattr("app.documents.parsers.pdf.pymupdf.open", boom)
    with pytest.raises(DocumentParseError) as caught:
        parse_document("nota.pdf", b"%PDF", PDF_MIME)

    assert str(caught.value) == "No se pudo leer el PDF"
    assert "boom" not in str(caught.value)
    assert "fitz" not in str(caught.value).lower()


def test_invalid_utf8_raises_domain_error() -> None:
    with pytest.raises(DocumentParseError) as caught:
        parse_document("nota.txt", b"\xff", TEXT_MIME)

    assert str(caught.value) == "El texto debe estar en UTF-8"
