"""Detección de tipo por contenido."""

from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from docx import Document

from app.documents.parsers.registry import DOCX_MIME, MARKDOWN_MIME, PDF_MIME, TEXT_MIME
from app.documents.sniff import sniff_file
from app.domain.errors import InvalidStateError


def test_sniffs_allowed_formats(tmp_path: Path) -> None:
    samples = {
        "nota.pdf": (_pdf_bytes(("Pagina uno",)), PDF_MIME),
        "nota.docx": (_docx_bytes(("Parrafo",)), DOCX_MIME),
        "nota.txt": (b"Hola texto", TEXT_MIME),
        "nota.md": (b"# Titulo\n\nHola markdown", MARKDOWN_MIME),
    }
    for name, (content, media_type) in samples.items():
        path = tmp_path / name
        path.write_bytes(content)
        assert sniff_file(path, name) == media_type


def test_rejects_disguised_and_unknown_files(tmp_path: Path) -> None:
    pdf = _pdf_bytes(("No soy texto",))
    disguised = {
        "nota.txt": pdf,
        "nota.pdf": b"esto no es un pdf",
        "nota.docx": pdf,
        "nota.exe": b"MZ",
    }
    for name, content in disguised.items():
        path = tmp_path / name
        path.write_bytes(content)
        with pytest.raises(InvalidStateError):
            sniff_file(path, name)


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
