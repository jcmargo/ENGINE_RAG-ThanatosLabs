"""Normalización conservadora del texto extraído."""

from app.documents.normalize import normalize_text


def test_preserves_titles_lists_and_paragraphs() -> None:
    raw = "Título\n\n- uno\n- dos\n\n\nPárrafo"

    assert normalize_text(raw) == "Título\n\n- uno\n- dos\n\nPárrafo"


def test_normalizes_spanish_unicode_and_whitespace() -> None:
    assert normalize_text("a\u0301rbol") == "árbol"
    assert normalize_text("El niño   pidió\tñame") == "El niño pidió ñame"
    assert normalize_text("hola\r\n\r\nmundo") == "hola\n\nmundo"


def test_blank_text_becomes_empty() -> None:
    assert normalize_text(" \n\t  ") == ""
