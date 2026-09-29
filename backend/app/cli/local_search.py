"""Búsqueda local sobre `data/sample`. No genera una respuesta."""

import argparse
import logging
import sys
from collections.abc import Sequence
from io import TextIOWrapper
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings, repository_root, resolve_model_cache_dir
from app.documents.chunking import TokenCounter
from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.retrieval.sample_search import LocalSearchHit, SampleSearchService


class Searcher(Protocol):
    """Búsqueda inyectable para probar el comando sin cargar el modelo."""

    def search(self, question: str, top_k: int) -> tuple[LocalSearchHit, ...]: ...


def build_sample_search(
    settings: Settings,
    sample_dir: Path | None = None,
    *,
    counter: TokenCounter | None = None,
) -> SampleSearchService:
    """Compone el proveedor local, el tokenizer real y el directorio de ejemplos."""
    cache_dir = resolve_model_cache_dir(settings)
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model,
        cache_dir=cache_dir,
    )
    token_counter = (
        HuggingFaceTokenCounter(settings.embedding_model, cache_dir=cache_dir)
        if counter is None
        else counter
    )
    directory = repository_root() / "data" / "sample" if sample_dir is None else sample_dir
    return SampleSearchService(provider, directory, token_counter)


def format_hit(hit: LocalSearchHit) -> str:
    """Una línea: rango, puntuación, fuente, página y texto."""
    text = " ".join(hit.result.chunk.text.split())
    return (
        f"{hit.result.position}\t{hit.result.score:.6f}\t{hit.source}\t"
        f"{hit.result.chunk.page_start}\t{text}"
    )


def main(argv: Sequence[str] | None = None, searcher: Searcher | None = None) -> None:
    """Imprime los fragmentos recuperados. No imprime una respuesta generada."""
    args = _parse_args(argv)
    if args.top_k < 1:
        print("top-k debe ser un entero mayor que cero", file=sys.stderr)
        raise SystemExit(2)
    engine = searcher if searcher is not None else _build_default_searcher()
    for hit in engine.search(args.question, args.top_k):
        print(format_hit(hit))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.local_search",
        description="Busca fragmentos en los documentos de ejemplo. No genera una respuesta.",
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args(None if argv is None else list(argv))


def _build_default_searcher() -> SampleSearchService:
    _configure_stdio()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.value,
        format="%(levelname)s %(name)s %(message)s",
    )
    return build_sample_search(settings)


def _configure_stdio() -> None:
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    main()
