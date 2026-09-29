"""Documentos ficticios de data/sample."""

from pathlib import Path

import pytest

from app.documents.chunking import chunk_document, document_content_hash
from app.documents.parsers.registry import MARKDOWN_MIME, parse_document

_SAMPLE_ROOT = Path(__file__).resolve().parents[3] / "data" / "sample"


@pytest.mark.parametrize(
    ("filename", "fact"),
    [
        ("politica-vacaciones.md", "18 días hábiles"),
        ("politica-gastos.md", "40 USD"),
        ("procedimiento-soporte.md", "15 minutos"),
    ],
)
def test_sample_document_keeps_expected_fact(filename: str, fact: str) -> None:
    content = (_SAMPLE_ROOT / filename).read_bytes()
    document = parse_document(filename, content, MARKDOWN_MIME)
    text = "\n".join(page.text for page in document.pages)
    chunks = chunk_document(
        document,
        document_hash=document_content_hash(content),
        document_version="sample-v1",
    )
    again = chunk_document(
        document,
        document_hash=document_content_hash(content),
        document_version="sample-v1",
    )

    assert fact in text
    assert any(fact in chunk.text for chunk in chunks)
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in again]
