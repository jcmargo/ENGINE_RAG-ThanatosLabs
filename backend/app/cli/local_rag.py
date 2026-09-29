"""Pregunta local: recupera, genera con Ollama y cita o se abstiene."""

import argparse
import logging
import sys
from collections.abc import Sequence
from io import TextIOWrapper
from typing import Protocol

from app.cli.local_search import build_sample_search
from app.config import Settings, get_settings
from app.generation.errors import GenerationError
from app.generation.models import Citation, GroundedRun
from app.generation.ollama import OllamaLLMProvider
from app.generation.service import GroundedRAGService
from app.retrieval.errors import RetrievalError


class RagRunner(Protocol):
    """Generación inyectable para probar el comando sin Ollama ni el modelo."""

    def answer(self, question: str, *, top_k: int) -> GroundedRun: ...


def build_local_rag(settings: Settings) -> GroundedRAGService:
    """Compone la búsqueda de ejemplos, Ollama y el umbral configurado."""
    return GroundedRAGService(
        build_sample_search(settings),
        OllamaLLMProvider(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            connect_timeout_seconds=settings.llm_connect_timeout_seconds,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        min_retrieval_score=settings.min_retrieval_score,
    )


def format_run(run: GroundedRun) -> str:
    """Texto legible: respuesta, abstención, citas, puntuaciones y latencias."""
    lines = [
        f"Respuesta:\t{run.answer.answer}",
        f"Abstención:\t{'sí' if run.answer.abstained else 'no'}",
        "Citas:",
        *_format_citations(run.answer.citations),
        "Puntuaciones:",
        *_format_scores(run),
        "Latencias:",
        f"recuperación_ms\t{run.retrieval_ms:.3f}",
        f"generación_ms\t{run.generation_ms:.3f}",
        f"LLM:\t{'sí' if run.llm_called else 'no'}",
        "Versiones:",
        f"recuperación\t{run.answer.retrieval_version}",
        f"prompt\t{run.answer.prompt_version}",
        f"modelo\t{run.answer.model_version}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, runner: RagRunner | None = None) -> None:
    """Imprime la respuesta fundamentada o un mensaje accionable si Ollama falla."""
    args = _parse_args(argv)
    if not str(args.question).strip():
        print("La pregunta no puede estar vacía", file=sys.stderr)
        raise SystemExit(2)
    if args.top_k < 1:
        print("top-k debe ser un entero mayor que cero", file=sys.stderr)
        raise SystemExit(2)
    engine = runner if runner is not None else _build_default_runner()
    try:
        result = engine.answer(args.question, top_k=args.top_k)
    except (GenerationError, RetrievalError) as error:
        print(error.message, file=sys.stderr)
        raise SystemExit(1) from None
    print(format_run(result))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.local_rag",
        description="Responde una pregunta con los documentos de ejemplo y citas.",
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args(None if argv is None else list(argv))


def _build_default_runner() -> GroundedRAGService:
    _configure_stdio()
    _configure_logging()
    return build_local_rag(get_settings())


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.value,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _configure_stdio() -> None:
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")


def _format_citations(citations: tuple[Citation, ...]) -> tuple[str, ...]:
    if not citations:
        return ("ninguna",)
    return tuple(
        _format_citation(index, citation) for index, citation in enumerate(citations, start=1)
    )


def _format_citation(index: int, citation: Citation) -> str:
    section = citation.section if citation.section else "sin sección"
    pages = (
        str(citation.page_start)
        if citation.page_start == citation.page_end
        else f"{citation.page_start}-{citation.page_end}"
    )
    return (
        f"{index}\t{citation.source}\tpágina {pages}\tsección {section}\t"
        f"id {citation.chunk_id}\textracto: {citation.excerpt}"
    )


def _format_scores(run: GroundedRun) -> tuple[str, ...]:
    if not run.retrieved:
        return ("ninguna",)
    lines: list[str] = []
    for hit in run.retrieved:
        lines.append(
            f"{hit.result.position}\t{hit.result.score:.6f}\t{hit.source}\t"
            f"página {hit.result.chunk.page_start}"
        )
    return tuple(lines)


if __name__ == "__main__":
    main()
