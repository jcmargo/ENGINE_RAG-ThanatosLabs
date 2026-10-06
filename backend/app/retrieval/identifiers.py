"""Identificadores explícitos de una pregunta: números con punto o rótulos."""

import re
import unicodedata
from collections.abc import Sequence

_DOTTED_NUMBER = re.compile(r"(?<!\d)\d+(?:\.\d+)+(?!\d)")
_LABELED_NUMBER = re.compile(
    r"\b(?:teorema|art[ií]culo|cl[aá]usula|inciso|secci[oó]n|paso)\s+(\d+)\b"
)
_LABEL_WORD = r"(?:teorema|art[ií]culo|cl[aá]usula|inciso|secci[oó]n|paso)"
_KIND_WORD = (
    r"teorema|ejemplo|definici[oó]n|proposici[oó]n|lema|corolario|"
    r"art[ií]culo|cl[aá]usula|inciso|secci[oó]n|paso"
)
_KIND_RE = re.compile(rf"(?i)(?<![\w])(?:{_KIND_WORD})(?![\w])")
_NUMBER_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
_SPACED_DOT = re.compile(r"(?<=\d)\s*\.\s*(?=\d)")
_DEMONSTRATION = re.compile(r"(?i)(?:\n+\s*|\s{2,}|(?<=[.!?;])\s*)demostraci[oó]n\b")
_NUMBERED_OPENING = re.compile(rf"(?i)^(?:{_KIND_WORD})\s+\d+(?:\.\d+)+")
_OPENING_CHARS = 80
_STATEMENT_CHARS = 900


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
    return _clip_statement(span)


def _clip_statement(span: str) -> str:
    """Corta en el último punto si el enunciado no cabe, para no partir una frase."""
    if len(span) <= _STATEMENT_CHARS:
        return span.strip()
    clipped = span[:_STATEMENT_CHARS]
    last_sentence = max(clipped.rfind(". "), clipped.rfind(".\n"))
    if last_sentence > 80:
        clipped = clipped[: last_sentence + 1]
    return clipped.strip()


def named_label(question: str) -> tuple[str, str] | None:
    """Un rótulo con clase y número, como «teorema» y «7.4.4».

    Con dos números no elige: la pregunta compara y no nombra una sola sección.
    """
    numbers = required_texts(question)
    if len(numbers) != 1:
        return None
    number = numbers[0]
    match = _kind_number_pattern(number).search(normalize_identifier_text(question))
    if match is None:
        return None
    return match.group(1).casefold(), number


def opens_with_kind(text: str, kind: str, number: str) -> bool:
    """El fragmento abre con esa clase y ese número, no con otro rótulo del mismo número."""
    if not kind.strip() or not number.strip():
        return False
    match = _kind_number_pattern(number).search(normalize_identifier_text(text).lstrip())
    if match is None or match.start() >= _OPENING_CHARS:
        return False
    return match.group(1).casefold() == kind.casefold()


def is_label_only(text: str) -> bool:
    """El texto es el rótulo o el número, sin el enunciado que lo sigue."""
    has_kind = _KIND_RE.search(text) is not None
    has_dotted = _DOTTED_NUMBER.search(normalize_identifier_text(text)) is not None
    if not has_kind and not has_dotted:
        return False
    stripped = _KIND_RE.sub(" ", text)
    stripped = _NUMBER_TOKEN.sub(" ", stripped)
    letters = re.findall(r"[^\W\d_]", stripped, flags=re.UNICODE)
    return len(letters) < 4


def opens_numbered_label(text: str) -> bool:
    """La primera línea es un rótulo con número, como «EJEMPLO 7.4.5»."""
    line = text.lstrip().split("\n", 1)[0].strip()
    return _NUMBERED_OPENING.match(line) is not None


def statement_text(text: str) -> str:
    """Enunciado del primer rótulo del fragmento. Si no hay rótulo, el texto entero."""
    named = named_label(text[:500])
    if named is not None:
        return statement_span(text, (named[1],))
    dotted = _DOTTED_NUMBER.findall(normalize_identifier_text(text[:500]))
    if dotted:
        return statement_span(text, (dotted[0],))
    return text.strip()


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
    return _number_pattern(_LABEL_WORD, item)


def _kind_number_pattern(number: str) -> re.Pattern[str]:
    return _number_pattern(f"({_KIND_WORD})", number)


def _number_pattern(kind: str, item: str) -> re.Pattern[str]:
    parts = item.split(".")
    number = r"\s*\.\s*".join(re.escape(part) for part in parts)
    return re.compile(rf"(?i)(?<![\w]){kind}(?![\w])\s+{number}(?!\d)")


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
