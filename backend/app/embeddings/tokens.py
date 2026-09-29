"""Contador de tokens del tokenizer real, compatible con el chunker."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from app.embeddings.base import DEFAULT_EMBEDDING_MODEL
from app.embeddings.errors import EmbeddingError


class TextTokenizer(Protocol):
    """Tokenizer mínimo: cuenta tokens de un texto sin recortarlo."""

    model_max_length: int

    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        padding: bool,
        truncation: bool,
    ) -> object: ...


class HuggingFaceTokenCounter:
    """Cuenta con el tokenizer del modelo de embeddings.

    `split(texto, 1)` devuelve palabras. El chunker las vuelve a unir con
    espacios y usa `count` para no pasar de `max_tokens`. Una palabra que
    ella sola supera el límite se parte sin perder caracteres.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        cache_dir: Path | None = None,
        tokenizer: TextTokenizer | None = None,
    ) -> None:
        if not model_name.strip():
            message = "El nombre del modelo de embeddings no puede estar vacío"
            raise ValueError(message)
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._tokenizer = tokenizer
        self._counts: dict[str, int] = {}

    def count(self, text: str) -> int:
        if not text.strip():
            return 0
        cached = self._counts.get(text)
        if cached is not None:
            return cached
        total = _input_id_count(
            self._require_tokenizer()(
                text,
                add_special_tokens=False,
                padding=False,
                truncation=False,
            )
        )
        self._counts[text] = total
        return total

    def split(self, text: str, limit: int) -> tuple[str, ...]:
        if limit < 1:
            message = "El límite de tokens debe ser mayor que cero"
            raise ValueError(message)
        words = text.split()
        if not words:
            return ()
        if limit == 1:
            return tuple(words)
        return _split_text_by_budget(text, limit, self.count)

    def _require_tokenizer(self) -> TextTokenizer:
        if self._tokenizer is None:
            self._tokenizer = _load_tokenizer(self._model_name, self._cache_dir)
        return self._tokenizer


def _split_text_by_budget(
    text: str,
    limit: int,
    count_tokens: Callable[[str], int],
) -> tuple[str, ...]:
    words = text.split()
    parts: list[str] = []
    bucket: list[str] = []

    def flush() -> None:
        if bucket:
            parts.append(" ".join(bucket))
            bucket.clear()

    for word in words:
        if count_tokens(word) > limit:
            flush()
            parts.extend(_split_oversized_word(word, limit, count_tokens))
            continue
        if not bucket:
            bucket.append(word)
            continue
        candidate = " ".join((*bucket, word))
        if count_tokens(candidate) > limit:
            flush()
            bucket.append(word)
            continue
        bucket.append(word)
    flush()
    return tuple(parts)


def _split_oversized_word(
    word: str,
    limit: int,
    count_tokens: Callable[[str], int],
) -> tuple[str, ...]:
    parts: list[str] = []
    rest = word
    while rest:
        if count_tokens(rest) <= limit:
            parts.append(rest)
            break
        best = 0
        low = 1
        high = len(rest) - 1
        while low <= high:
            mid = (low + high) // 2
            if count_tokens(rest[:mid]) <= limit:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best < 1:
            best = 1
        parts.append(rest[:best])
        rest = rest[best:]
    return tuple(parts)


def _load_tokenizer(model_name: str, cache_dir: Path | None) -> TextTokenizer:
    from transformers import AutoTokenizer

    cache = None if cache_dir is None else str(cache_dir)
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            cache_dir=cache,
            local_files_only=True,
        )
    except OSError:
        # La caché todavía no tiene el tokenizer; la segunda carga puede descargarlo.
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            cache_dir=cache,
            local_files_only=False,
        )
    if tokenizer is None:
        message = "No se pudo cargar el tokenizer del modelo de embeddings"
        raise EmbeddingError(message)
    tokenizer.model_max_length = 1_000_000_000
    return tokenizer


def _input_id_count(encoded: object) -> int:
    if not isinstance(encoded, Mapping):
        message = "El tokenizer no devolvió un mapeo"
        raise EmbeddingError(message)
    token_ids = encoded.get("input_ids")
    if not isinstance(token_ids, Sequence) or isinstance(token_ids, str | bytes):
        message = "El tokenizer no devolvió input_ids"
        raise EmbeddingError(message)
    if (
        token_ids
        and isinstance(token_ids[0], Sequence)
        and not isinstance(token_ids[0], str | bytes)
    ):
        message = "El tokenizer devolvió un lote inesperado"
        raise EmbeddingError(message)
    return len(token_ids)
