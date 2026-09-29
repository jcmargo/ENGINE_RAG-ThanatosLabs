"""Chunking por página, solapamiento y IDs deterministas."""

from itertools import pairwise

import pytest

from app.documents.chunking import WordTokenCounter, chunk_document
from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.versions import (
    CHUNKER_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    PARSER_VERSION,
)


def _document(pages: tuple[str, ...]) -> ExtractedDocument:
    extracted_pages = tuple(
        ExtractedPage(number=index, text=text, section=None)
        for index, text in enumerate(pages, start=1)
    )
    return ExtractedDocument(
        name="notas.txt",
        mime_type="text/plain",
        pages=extracted_pages,
        parser_version=PARSER_VERSION,
    )


def test_default_overlap_is_fifteen_percent() -> None:
    assert DEFAULT_MAX_TOKENS == 500
    assert DEFAULT_OVERLAP_TOKENS == 75
    assert DEFAULT_OVERLAP_TOKENS == DEFAULT_MAX_TOKENS * 15 // 100
    assert CHUNKER_VERSION == "chunker-v1"


def test_word_counter_splits_without_dropping_words() -> None:
    counter = WordTokenCounter()
    text = "uno dos tres cuatro"

    assert counter.count(text) == 4
    assert counter.split(text, 3) == ("uno dos tres", "cuatro")
    assert "".join(part.replace(" ", "") for part in counter.split(text, 1)) == "unodostrescuatro"


def test_chunks_do_not_exceed_max_tokens_and_overlap() -> None:
    words = [f"w{index}" for index in range(80)]
    document = _document((" ".join(words),))
    chunks = chunk_document(
        document,
        document_hash="hash",
        document_version="v1",
        max_tokens=10,
        overlap_tokens=4,
    )
    counter = WordTokenCounter()

    assert chunks
    assert all(counter.count(chunk.text) <= 10 for chunk in chunks)
    assert all(chunk.page_start == 1 and chunk.page_end == 1 for chunk in chunks)
    for left, right in pairwise(chunks):
        assert right.text.split()[:4] == left.text.split()[-4:]
    rebuilt = chunks[0].text.split()
    for chunk in chunks[1:]:
        rebuilt.extend(chunk.text.split()[4:])
    assert rebuilt == words


class _SeparatorTokenCounter:
    """Cada palabra vale 1 y cada espacio o salto también."""

    def count(self, text: str) -> int:
        if not text.strip():
            return 0
        whitespace = sum(1 for char in text if char.isspace())
        return len(text.split()) + whitespace

    def split(self, text: str, limit: int) -> tuple[str, ...]:
        if limit < 1:
            message = "El límite de tokens debe ser mayor que cero"
            raise ValueError(message)
        words = text.split()
        if not words:
            return ()
        if limit == 1:
            return tuple(words)
        parts: list[str] = []
        bucket: list[str] = []
        for word in words:
            candidate = " ".join((*bucket, word))
            if bucket and self.count(candidate) > limit:
                parts.append(" ".join(bucket))
                bucket = [word]
                continue
            bucket.append(word)
        if bucket:
            parts.append(" ".join(bucket))
        return tuple(parts)


def test_rendered_separators_count_toward_max_tokens() -> None:
    counter = _SeparatorTokenCounter()
    document = _document(("uno dos tres\n\nalfa beta",))
    chunks = chunk_document(
        document,
        document_hash="hash",
        document_version="v1",
        counter=counter,
        max_tokens=3,
        overlap_tokens=0,
    )

    assert [chunk.text for chunk in chunks] == ["uno dos", "tres", "alfa beta"]
    assert all(counter.count(chunk.text) <= 3 for chunk in chunks)
    assert all(chunk.page_start == chunk.page_end == 1 for chunk in chunks)
    bare_words = sum(len(chunk.text.split()) for chunk in chunks)
    assert bare_words == 5
    assert counter.count("uno dos tres") > 3


def test_long_paragraph_is_split_without_losing_content() -> None:
    words = [f"p{index}" for index in range(25)]
    document = _document((" ".join(words),))
    chunks = chunk_document(
        document,
        document_hash="hash",
        document_version="v1",
        max_tokens=8,
        overlap_tokens=3,
    )
    rebuilt = chunks[0].text.split()
    for chunk in chunks[1:]:
        rebuilt.extend(chunk.text.split()[3:])

    assert rebuilt == words


def test_same_document_produces_the_same_ids_and_order() -> None:
    document = _document(("alfa beta gamma", "delta epsilon zeta"))
    first = chunk_document(
        document,
        document_hash="abc",
        document_version="1",
        max_tokens=2,
        overlap_tokens=1,
    )
    second = chunk_document(
        document,
        document_hash="abc",
        document_version="1",
        max_tokens=2,
        overlap_tokens=1,
    )

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert [chunk.position for chunk in first] == list(range(len(first)))


def test_ids_change_when_text_version_or_chunker_changes() -> None:
    base_document = _document(("texto estable de ejemplo",))
    shared = {
        "document_hash": "h",
        "max_tokens": 20,
        "overlap_tokens": 2,
    }
    base = chunk_document(
        base_document,
        document_version="v1",
        chunker_version="chunker-v1",
        **shared,
    )
    changed_text = chunk_document(
        _document(("texto distinto de ejemplo",)),
        document_version="v1",
        chunker_version="chunker-v1",
        **shared,
    )
    changed_version = chunk_document(
        base_document,
        document_version="v2",
        chunker_version="chunker-v1",
        **shared,
    )
    changed_chunker = chunk_document(
        base_document,
        document_version="v1",
        chunker_version="chunker-v2",
        **shared,
    )
    base_ids = {chunk.id for chunk in base}

    assert base_ids.isdisjoint({chunk.id for chunk in changed_text})
    assert base_ids.isdisjoint({chunk.id for chunk in changed_version})
    assert base_ids.isdisjoint({chunk.id for chunk in changed_chunker})


def test_untouched_page_keeps_its_ids() -> None:
    original = _document(("pagina uno completa", "pagina dos original"))
    updated = _document(("pagina uno completa", "pagina dos cambiada"))
    first = chunk_document(
        original,
        document_hash="h",
        document_version="v1",
        max_tokens=20,
        overlap_tokens=0,
    )
    second = chunk_document(
        updated,
        document_hash="h",
        document_version="v1",
        max_tokens=20,
        overlap_tokens=0,
    )

    assert [chunk.page_start for chunk in first] == [1, 2]
    assert all(chunk.page_start == chunk.page_end for chunk in first)
    assert [chunk.id for chunk in first if chunk.page_start == 1] == [
        chunk.id for chunk in second if chunk.page_start == 1
    ]
    assert [chunk.id for chunk in first if chunk.page_start == 2] != [
        chunk.id for chunk in second if chunk.page_start == 2
    ]


def test_markdown_sections_follow_headings() -> None:
    text = "# Vacaciones\n\nLos empleados tienen descanso.\n\n# Gastos\n\nEl límite es claro."
    chunks = chunk_document(
        _document((text,)),
        document_hash="h",
        document_version="v1",
        max_tokens=6,
        overlap_tokens=0,
    )
    sections = {chunk.section for chunk in chunks}

    assert "Vacaciones" in sections
    assert "Gastos" in sections


def test_single_token_chunks_cover_every_word() -> None:
    chunks = chunk_document(
        _document(("uno dos tres",)),
        document_hash="h",
        document_version="v1",
        max_tokens=1,
        overlap_tokens=0,
    )

    assert [chunk.text for chunk in chunks] == ["uno", "dos", "tres"]


def test_rejects_overlap_that_would_not_advance() -> None:
    with pytest.raises(ValueError, match="overlap_tokens"):
        chunk_document(
            _document(("uno dos",)),
            document_hash="h",
            document_version="v1",
            max_tokens=2,
            overlap_tokens=2,
        )
