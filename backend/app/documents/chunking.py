"""Chunking por página, con límite de tokens y solapamiento."""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.documents.models import Chunk, ExtractedDocument, ExtractedPage
from app.documents.normalize import normalize_text
from app.documents.versions import CHUNKER_VERSION, DEFAULT_MAX_TOKENS, DEFAULT_OVERLAP_TOKENS

_HEADING = re.compile(r"^#{1,6}[ \t]+(\S.*?)\s*$")


class TokenCounter(Protocol):
    """Cuenta y segmenta tokens sin acoplarse a un modelo."""

    def count(self, text: str) -> int:
        """Devuelve cuántos tokens contiene el texto."""
        ...

    def split(self, text: str, limit: int) -> tuple[str, ...]:
        """Parte el texto en trozos de hasta `limit` tokens, sin perder contenido."""
        ...


class WordTokenCounter:
    """Contador determinista por palabras, para pruebas y para esta versión."""

    def count(self, text: str) -> int:
        if not text.strip():
            return 0
        return len(text.split())

    def split(self, text: str, limit: int) -> tuple[str, ...]:
        if limit < 1:
            message = "El límite de tokens debe ser mayor que cero"
            raise ValueError(message)
        words = text.split()
        if not words:
            return ()
        parts: list[str] = []
        for start in range(0, len(words), limit):
            parts.append(" ".join(words[start : start + limit]))
        return tuple(parts)


@dataclass(frozen=True)
class _Word:
    text: str
    paragraph_index: int
    section: str | None


def document_content_hash(content: bytes) -> str:
    """Hash estable de los bytes originales del documento."""
    return hashlib.sha256(content).hexdigest()


def chunk_document(
    document: ExtractedDocument,
    *,
    document_hash: str,
    document_version: str,
    counter: TokenCounter | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    chunker_version: str = CHUNKER_VERSION,
) -> tuple[Chunk, ...]:
    """Divide cada página sin mezclarlas. El tope se mide sobre el texto renderizado."""
    if max_tokens < 1:
        message = "max_tokens debe ser mayor que cero"
        raise ValueError(message)
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        message = "overlap_tokens debe ser menor que max_tokens"
        raise ValueError(message)
    token_counter = WordTokenCounter() if counter is None else counter
    chunks: list[Chunk] = []
    position = 0
    for page in document.pages:
        page_words = _page_words(page, token_counter, max_tokens)
        for words in _windows(page_words, token_counter, max_tokens, overlap_tokens):
            text = _chunk_text(words)
            if not text:
                continue
            chunks.append(
                Chunk(
                    id=_chunk_id(
                        document_hash=document_hash,
                        document_version=document_version,
                        page=page.number,
                        position=position,
                        text=text,
                        chunker_version=chunker_version,
                    ),
                    text=text,
                    page_start=page.number,
                    page_end=page.number,
                    section=words[0].section,
                    position=position,
                    chunker_version=chunker_version,
                )
            )
            position += 1
    return tuple(chunks)


def _chunk_id(
    *,
    document_hash: str,
    document_version: str,
    page: int,
    position: int,
    text: str,
    chunker_version: str,
) -> str:
    material = "\n".join(
        (
            document_hash,
            document_version,
            str(page),
            str(position),
            text,
            chunker_version,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _heading_text(paragraph: str) -> str | None:
    match = _HEADING.match(paragraph)
    if match is None:
        return None
    return match.group(1).strip()


def _page_words(
    page: ExtractedPage,
    counter: TokenCounter,
    max_tokens: int,
) -> tuple[_Word, ...]:
    paragraphs = tuple(
        part.strip() for part in re.split(r"\n{2,}", page.text.strip()) if part.strip()
    )
    section = page.section
    words: list[_Word] = []
    for index, paragraph in enumerate(paragraphs):
        heading = _heading_text(paragraph)
        if heading is not None:
            section = heading
        pieces = (
            (paragraph,)
            if counter.count(paragraph) <= max_tokens
            else counter.split(paragraph, max_tokens)
        )
        for piece in pieces:
            for token in counter.split(piece, 1):
                if counter.count(token) < 1:
                    continue
                words.append(
                    _Word(
                        text=token,
                        paragraph_index=index,
                        section=section,
                    )
                )
    return tuple(words)


def _windows(
    words: Sequence[_Word],
    counter: TokenCounter,
    max_tokens: int,
    overlap_tokens: int,
) -> Iterator[tuple[_Word, ...]]:
    """Corta por el texto ya unido, con espacios y saltos de párrafo incluidos."""
    count = len(words)
    start = 0
    while start < count:
        end = start
        while end < count:
            if (
                _starts_paragraph(words, end)
                and end > start
                and _rendered_tokens(counter, words[start : _paragraph_end(words, end)])
                > max_tokens
            ):
                break
            if _rendered_tokens(counter, words[start : end + 1]) > max_tokens:
                break
            end += 1
        if end <= start:
            end = min(start + 1, count)
        yield tuple(words[start:end])
        if end >= count:
            break
        next_start = end
        overlap = 0
        while next_start > start and overlap < overlap_tokens:
            next_start -= 1
            overlap = _rendered_tokens(counter, words[next_start:end])
        if next_start <= start:
            next_start = start + 1
        start = min(next_start, end)


def _starts_paragraph(words: Sequence[_Word], index: int) -> bool:
    if index == 0:
        return True
    return words[index].paragraph_index != words[index - 1].paragraph_index


def _paragraph_end(words: Sequence[_Word], start: int) -> int:
    paragraph_index = words[start].paragraph_index
    index = start + 1
    while index < len(words) and words[index].paragraph_index == paragraph_index:
        index += 1
    return index


def _chunk_text(words: Sequence[_Word]) -> str:
    return normalize_text(_render(words))


def _rendered_tokens(counter: TokenCounter, words: Sequence[_Word]) -> int:
    if not words:
        return 0
    return counter.count(_chunk_text(words))


def _render(words: Sequence[_Word]) -> str:
    parts: list[str] = []
    paragraph_index = words[0].paragraph_index
    bucket = [words[0].text]
    for word in words[1:]:
        if word.paragraph_index != paragraph_index:
            parts.append(" ".join(bucket))
            bucket = [word.text]
            paragraph_index = word.paragraph_index
            continue
        bucket.append(word.text)
    parts.append(" ".join(bucket))
    return "\n\n".join(parts)
