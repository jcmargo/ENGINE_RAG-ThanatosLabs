"""Identificadores explícitos de una pregunta: números con punto o rótulos."""

import re
import unicodedata
from collections.abc import Sequence

_DOTTED_NUMBER = re.compile(r"(?<!\d)\d+(?:\.\d+)+(?!\d)")
_LABELED_NUMBER = re.compile(
    r"\b(?:teorema|art[ií]culo|cl[aá]usula|inciso|secci[oó]n|paso)\s+(\d+)\b"
)
_LABEL_WORD = r"(?:teorema|art[ií]culo|cl[aá]usula|inciso|secci[oó]n|paso)"
_SPACED_DOT = re.compile(r"(?<=\d)\s*\.\s*(?=\d)")
_DEMONSTRATION = re.compile(r"(?i)(?:\n+\s*|\s{2,})demostraci[oó]n\b")
_OPENING_CHARS = 80
_STATEMENT_CHARS = 700


def required_texts(question: str) -> tuple[str, ...]:
    """Números que el fragmento debe contener. Un entero suelto no cuenta."""
    normalized = normalize_identifier_text(question)
    dotted = _unique(_DOTTED_NUMBER.findall(normalized))
    if dotted:
        return dotted
    return _unique(_LABELED_NUMBER.findall(normalized))


def contains_all(text: str, required: Sequence[str]) -> bool:
    """El texto trae cada identificador, sin confundir 12 con 120."""
    if not required:
        return True
    normalized = normalize_identifier_text(text)
    return all(_contains(normalized, item) for item in required)


def opens_with_any(text: str, required: Sequence[str]) -> bool:
    """Algún identificador aparece al inicio del fragmento, no solo citado al final."""
    if not required:
        return False
    normalized = normalize_identifier_text(text).lstrip()
    return any(_opens(normalized, item) for item in required)


def label_heading(text: str, required: Sequence[str]) -> bool:
    """El fragmento nombra el rótulo, por ejemplo 'teorema 8.4.4', y no solo el número."""
    if not required:
        return False
    normalized = normalize_identifier_text(text)
    return any(_label_at(normalized, item) is not None for item in required)


def statement_span(text: str, required: Sequence[str]) -> str:
    """Recorta desde el rótulo hasta la demostración, para no mandar el resto de la página."""
    if not required:
        return text
    start = None
    for item in required:
        found = _label_in_original(text, item)
        if found is not None:
            start = found
            break
    if start is None:
        return text
    span = text[start:]
    demonstration = _DEMONSTRATION.search(span)
    if demonstration is not None and demonstration.start() > 20:
        span = span[: demonstration.start()]
    return span[:_STATEMENT_CHARS].strip()


def normalize_identifier_text(text: str) -> str:
    """NFKC, minúsculas y puntos de números sin espacios alrededor."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    previous = ""
    while previous != normalized:
        previous = normalized
        normalized = _SPACED_DOT.sub(".", normalized)
    return normalized


def _contains(normalized: str, item: str) -> bool:
    return re.search(_boundary(item), normalized) is not None


def _opens(normalized: str, item: str) -> bool:
    match = re.search(_boundary(item), normalized)
    return match is not None and match.start() < _OPENING_CHARS


def _label_at(normalized: str, item: str) -> int | None:
    match = _label_pattern(item).search(normalized)
    if match is None:
        return None
    return match.start()


def _label_in_original(text: str, item: str) -> int | None:
    match = _label_pattern(item).search(text)
    if match is None:
        return None
    return match.start()


def _label_pattern(item: str) -> re.Pattern[str]:
    parts = item.split(".")
    number = r"\s*\.\s*".join(re.escape(part) for part in parts)
    return re.compile(rf"(?i)(?<![\w]){_LABEL_WORD}(?![\w])\s+{number}(?!\d)")


def _boundary(item: str) -> str:
    return rf"(?<!\d){re.escape(item)}(?!\d)"


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)
