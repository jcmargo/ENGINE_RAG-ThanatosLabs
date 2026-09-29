"""Presupuesto de tokens sin descargar el tokenizer real."""

import pytest

from app.documents.chunking import chunk_document
from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.versions import PARSER_VERSION
from app.embeddings.tokens import HuggingFaceTokenCounter


class CharTokenizer:
    """Un token por carácter que no es espacio."""

    model_max_length = 1_000_000_000

    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        padding: bool,
        truncation: bool,
    ) -> dict[str, list[int]]:
        del add_special_tokens, padding, truncation
        return {"input_ids": [index for index, char in enumerate(text) if not char.isspace()]}


def test_unit_split_keeps_words_for_the_chunker() -> None:
    counter = HuggingFaceTokenCounter(tokenizer=CharTokenizer())

    assert counter.split("18 días hábiles", 1) == ("18", "días", "hábiles")
    assert counter.count("hábiles") == len("hábiles")


def test_budget_split_respects_limit_without_dropping_characters() -> None:
    counter = HuggingFaceTokenCounter(tokenizer=CharTokenizer())

    parts = counter.split("abcdef", 2)

    assert "".join(parts) == "abcdef"
    assert all(counter.count(part) <= 2 for part in parts)


def test_chunker_respects_injected_tokenizer_limit() -> None:
    counter = HuggingFaceTokenCounter(tokenizer=CharTokenizer())
    text = " ".join(f"palabra{index}" for index in range(12))
    document = ExtractedDocument(
        name="largo.txt",
        mime_type="text/plain",
        pages=(ExtractedPage(number=1, text=text),),
        parser_version=PARSER_VERSION,
    )

    chunks = chunk_document(
        document,
        document_hash="hash",
        document_version="v1",
        counter=counter,
        max_tokens=20,
        overlap_tokens=4,
    )

    assert len(chunks) > 1
    assert all(counter.count(chunk.text) <= 20 for chunk in chunks)
    blob = " ".join(chunk.text for chunk in chunks)
    assert "palabra0" in blob
    assert "palabra11" in blob


def test_split_rejects_non_positive_limit() -> None:
    counter = HuggingFaceTokenCounter(tokenizer=CharTokenizer())

    with pytest.raises(ValueError, match="límite"):
        counter.split("hola", 0)
