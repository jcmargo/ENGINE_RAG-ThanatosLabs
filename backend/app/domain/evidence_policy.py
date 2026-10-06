"""Evidencia por coseno alto, o por un coseno menor anclado a la mejor oración."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

DEFAULT_LEXICAL_FLOOR = 0.80
LEXICAL_SCORE_MARGIN = 0.02
_MIN_CONTENT_LENGTH = 4
_MIN_COVERAGE = 0.5
_WORD = re.compile(r"[a-z0-9]+")
_DECIMAL = re.compile(r"\d+(?:\.\d+)+")
_SENTENCE_SPLIT = re.compile(r"[\n.?!]+")
_DIGIT = re.compile(r"\d")
_MEASURE_WORDS = frozenset(
    {
        "cantidad",
        "importe",
        "maximo",
        "monto",
        "reembolsable",
        "tope",
    }
)
# Cuánto/cuántos solo exige una cifra si nombra una unidad contada.
# "Cuánto tiempo" puede responderse con "el ejercicio fiscal", sin dígito.
_COUNTED_UNITS = frozenset({"ano", "dia", "hora", "mes", "minuto"})
_STOPWORDS = frozenset(
    {
        "algo",
        "alguien",
        "alguna",
        "algunas",
        "alguno",
        "algunos",
        "ante",
        "aquel",
        "aquella",
        "aquellas",
        "aquello",
        "aquellos",
        "cada",
        "como",
        "con",
        "contra",
        "cuando",
        "cual",
        "cuales",
        "cuya",
        "cuyas",
        "cuyo",
        "cuyos",
        "desde",
        "donde",
        "durante",
        "ella",
        "ellas",
        "ellos",
        "esta",
        "estas",
        "este",
        "esto",
        "estos",
        "hacia",
        "hasta",
        "mucho",
        "muchos",
        "nada",
        "nadie",
        "ningun",
        "ninguna",
        "nosotros",
        "nosotras",
        "para",
        "pero",
        "porque",
        "sobre",
        "tambien",
        "todo",
        "toda",
        "todas",
        "todos",
        "usted",
        "ustedes",
        "vosotros",
        "vosotras",
    }
)


@dataclass(frozen=True)
class EvidenceCandidate:
    """Fragmento ya puntuado. La política no sabe de índices ni de modelos."""

    source: str
    score: float
    text: str


@dataclass(frozen=True)
class _SentenceFocus:
    span: str
    choice_count: int
    matched: frozenset[str]
    primary: frozenset[str]
    narrowed: bool


def select_evidence(
    question: str,
    candidates: Sequence[EvidenceCandidate],
    *,
    semantic_floor: float,
    lexical_floor: float,
) -> tuple[EvidenceCandidate, ...]:
    """Devuelve la evidencia aceptada, con el texto recortado a la oración útil.

    El piso semántico entra solo por coseno. Si ninguna oración comparte palabras,
    el fragmento se devuelve igual. Si comparte, el texto queda en la mejor oración.
    Entre los dos pisos solo entra un fragmento cuya cobertura llega al menos a la
    mitad de las palabras de la pregunta. Esa cobertura y el desempate entre archivos
    miran la mejor oración, no la suma del fragmento. Si varias oraciones empatan en
    ese máximo, sus palabras se reúnen para la cobertura y todas se conservan: una
    oración más débil no suma con una más fuerte.

    Si dos archivos traen el mismo número de palabras y no comparten ninguna, no hay
    evidencia aunque el coseno se separe más de `LEXICAL_SCORE_MARGIN`. Si uno cubre
    más palabras, el margen de score se mantiene. Una pregunta de cantidad con una
    unidad contada (minuto, hora, día, mes, año) y sin cifra en la oración se
    descarta. También se descarta una cifra cuyas únicas coincidencias son palabras
    de medida.
    """
    _require_floor(semantic_floor, "semantic_floor")
    _require_floor(lexical_floor, "lexical_floor")
    semantic = tuple(item for item in candidates if item.score >= semantic_floor)
    if semantic:
        return _accepted(question, semantic)
    lexical = tuple(
        item
        for item in candidates
        if lexical_floor <= item.score < semantic_floor and _covers(question, item.text)
    )
    if not lexical:
        return ()
    return _accepted(question, _choose_lexical_source(question, lexical))


def content_words(text: str) -> tuple[str, ...]:
    """Palabras de contenido: sin tildes, sin artículos ni pronombres, de 4 o más letras."""
    seen: set[str] = set()
    words: list[str] = []
    for word in _WORD.findall(_fold(text)):
        if len(word) < _MIN_CONTENT_LENGTH or word in _STOPWORDS or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return tuple(words)


def _accepted(
    question: str,
    items: Sequence[EvidenceCandidate],
) -> tuple[EvidenceCandidate, ...]:
    kept: list[EvidenceCandidate] = []
    for item in items:
        focus = _focus(question, item.text)
        if _rejected(question, focus):
            continue
        kept.append(item if not focus.narrowed else replace(item, text=focus.span))
    return tuple(kept)


def _choose_lexical_source(
    question: str,
    lexical: Sequence[EvidenceCandidate],
) -> tuple[EvidenceCandidate, ...]:
    by_source: dict[str, list[EvidenceCandidate]] = {}
    for item in lexical:
        by_source.setdefault(item.source, []).append(item)
    if len(by_source) == 1:
        return tuple(lexical)
    stats = {source: _source_stat(question, items) for source, items in by_source.items()}
    if _disjoint_tie(tuple(stats.values())):
        return ()
    leader_score = max(_best_score(items) for items in by_source.values())
    contenders = {
        source: items
        for source, items in by_source.items()
        if leader_score - _best_score(items) < LEXICAL_SCORE_MARGIN
    }
    if len(contenders) == 1:
        source = next(iter(contenders))
        return tuple(item for item in lexical if item.source == source)
    counts = {source: stats[source][0] for source in contenders}
    best = max(counts.values())
    winners = [source for source, count in counts.items() if count == best]
    if len(winners) != 1:
        return ()
    source = winners[0]
    return tuple(item for item in lexical if item.source == source)


def _source_stat(
    question: str,
    items: Sequence[EvidenceCandidate],
) -> tuple[int, frozenset[str]]:
    ranked = [(_focus(question, item.text), item.score) for item in items]
    focus, _score = max(ranked, key=lambda pair: (pair[0].choice_count, pair[1]))
    return focus.choice_count, focus.primary


def _disjoint_tie(stats: Sequence[tuple[int, frozenset[str]]]) -> bool:
    if len(stats) < 2:
        return False
    top = max(count for count, _words in stats)
    leaders = [words for count, words in stats if count == top]
    if len(leaders) < 2:
        return False
    return _pairwise_disjoint(leaders)


def _pairwise_disjoint(groups: Sequence[frozenset[str]]) -> bool:
    for index, words in enumerate(groups):
        for other in groups[index + 1 :]:
            if not words.isdisjoint(other):
                return False
    return True


def _best_score(items: Sequence[EvidenceCandidate]) -> float:
    return max(item.score for item in items)


def _covers(question: str, text: str) -> bool:
    words = content_words(question)
    if not words:
        return False
    return len(_focus(question, text).matched) / len(words) >= _MIN_COVERAGE


def _focus(question: str, text: str) -> _SentenceFocus:
    question_words = content_words(question)
    sentences = _sentences(text)
    if not sentences:
        return _SentenceFocus(text, 0, frozenset(), frozenset(), False)
    counted = [(sentence, _matched_words(question_words, sentence)) for sentence in sentences]
    best = max(len(words) for _, words in counted)
    if best == 0:
        return _SentenceFocus(text, 0, frozenset(), frozenset(), False)
    chosen = [(sentence, words) for sentence, words in counted if len(words) == best]
    matched: set[str] = set()
    for _, words in chosen:
        matched.update(words)
    span = chosen[0][0] if len(chosen) == 1 else "\n".join(sentence for sentence, _ in chosen)
    return _SentenceFocus(span, best, frozenset(matched), chosen[0][1], span != text)


def _matched_words(question_words: Sequence[str], sentence: str) -> frozenset[str]:
    folded = _fold(sentence)
    return frozenset(word for word in question_words if _contains_word(folded, word))


def _rejected(question: str, focus: _SentenceFocus) -> bool:
    if focus.choice_count == 0:
        return False
    if _quantity_without_digit(question, focus.span):
        return True
    return _only_measure_words(focus.span, focus.matched)


def _quantity_without_digit(question: str, span: str) -> bool:
    if "cuant" not in _fold(question):
        return False
    if _DIGIT.search(span) is not None:
        return False
    return _asks_counted_unit(question)


def _asks_counted_unit(question: str) -> bool:
    folded = _fold(question)
    return any(
        _has_token(folded, unit)
        or _has_token(folded, f"{unit}s")
        or _has_token(folded, f"{unit}es")
        for unit in _COUNTED_UNITS
    )


def _only_measure_words(span: str, matched: frozenset[str]) -> bool:
    if not matched or _DIGIT.search(span) is None:
        return False
    return all(_as_measure_word(word) in _MEASURE_WORDS for word in matched)


def _as_measure_word(word: str) -> str:
    if word in _MEASURE_WORDS:
        return word
    stem = _singular_stem(word)
    if stem is not None and stem in _MEASURE_WORDS:
        return stem
    return word


def _sentences(text: str) -> tuple[str, ...]:
    masked, tokens = _mask_decimals(text)
    sentences: list[str] = []
    for part in _SENTENCE_SPLIT.split(masked):
        restored = _restore(part, tokens).strip()
        if restored:
            sentences.append(restored)
    return tuple(sentences)


def _mask_decimals(text: str) -> tuple[str, tuple[str, ...]]:
    tokens: list[str] = []

    def hold(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"[[{len(tokens) - 1}]]"

    return _DECIMAL.sub(hold, text), tuple(tokens)


def _restore(text: str, tokens: Sequence[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return tokens[int(match.group(1))]

    return re.sub(r"\[\[(\d+)]]", repl, text)


def _contains_word(folded_text: str, word: str) -> bool:
    if _has_token(folded_text, word):
        return True
    if len(word) < _MIN_CONTENT_LENGTH:
        return False
    if _has_token(folded_text, f"{word}s") or _has_token(folded_text, f"{word}es"):
        return True
    stem = _singular_stem(word)
    if stem is None:
        return False
    return _has_token(folded_text, stem)


def _has_token(folded_text: str, token: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", folded_text) is not None


def _singular_stem(word: str) -> str | None:
    if word.endswith("es"):
        stem = word[:-2]
        if len(stem) >= _MIN_CONTENT_LENGTH:
            return stem
        return None
    if word.endswith("s"):
        stem = word[:-1]
        if len(stem) >= _MIN_CONTENT_LENGTH:
            return stem
    return None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _require_floor(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} debe ser un número entre 0 y 1"
        raise ValueError(message)
    score = float(value)
    if score != score or not 0.0 <= score <= 1.0:
        message = f"{name} debe ser un número entre 0 y 1"
        raise ValueError(message)
