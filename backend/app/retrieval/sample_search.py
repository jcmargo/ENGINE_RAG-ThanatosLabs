"""Indexa `data/sample` y busca fragmentos, sin generar una respuesta."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.documents.chunking import TokenCounter, chunk_document, document_content_hash
from app.documents.models import Chunk
from app.documents.parsers.registry import (
    DOCX_MIME,
    MARKDOWN_MIME,
    PDF_MIME,
    TEXT_MIME,
    parse_document,
)
from app.embeddings.base import EmbeddingProvider
from app.retrieval.errors import EmptySampleCorpusError, RetrievalError
from app.retrieval.memory import InMemoryVectorIndex
from app.retrieval.models import SearchResult

SAMPLE_DOCUMENT_VERSION = "sample-v1"

_MIME_BY_SUFFIX: dict[str, str] = {
    ".pdf": PDF_MIME,
    ".docx": DOCX_MIME,
    ".txt": TEXT_MIME,
    ".md": MARKDOWN_MIME,
    ".markdown": MARKDOWN_MIME,
}


@dataclass(frozen=True)
class LocalSearchHit:
    """Resultado rankeado junto con el nombre del archivo de origen."""

    result: SearchResult
    source: str


class SampleSearchService:
    """Extrae, fragmenta e indexa en memoria los documentos admitidos de un directorio."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        sample_dir: Path,
        counter: TokenCounter,
    ) -> None:
        prepared = _load_chunks(sample_dir, counter)
        vectors = provider.embed_documents([chunk.text for chunk, _source in prepared])
        if len(vectors) != len(prepared):
            message = "El proveedor devolvió un número de vectores distinto al de fragmentos"
            raise RetrievalError(message)
        index = InMemoryVectorIndex(provider.dimension)
        sources: dict[str, str] = {}
        for (chunk, source), vector in zip(prepared, vectors, strict=True):
            index.add(chunk, vector)
            sources[chunk.id] = source
        self._provider = provider
        self._index = index
        self._sources = sources

    def search(
        self,
        question: str,
        top_k: int,
        *,
        containing: Sequence[str] = (),
    ) -> tuple[LocalSearchHit, ...]:
        """Busca la pregunta ya vectorizada por el proveedor, sin prefijos externos."""
        results = self._index.search(
            self._provider.embed_query(question),
            top_k,
            containing=containing,
        )
        return tuple(
            LocalSearchHit(result=result, source=self._sources[result.chunk.id])
            for result in results
        )


def _load_chunks(
    sample_dir: Path,
    counter: TokenCounter,
) -> tuple[tuple[Chunk, str], ...]:
    paths = _supported_documents(sample_dir)
    prepared: list[tuple[Chunk, str]] = []
    for path in paths:
        content = path.read_bytes()
        mime_type = _MIME_BY_SUFFIX[path.suffix.lower()]
        document = parse_document(path.name, content, mime_type)
        chunks = chunk_document(
            document,
            document_hash=document_content_hash(content),
            document_version=SAMPLE_DOCUMENT_VERSION,
            counter=counter,
        )
        prepared.extend((chunk, path.name) for chunk in chunks)
    if not prepared:
        message = f"No hay documentos admitidos en {sample_dir}"
        raise EmptySampleCorpusError(message)
    return tuple(prepared)


def _supported_documents(sample_dir: Path) -> tuple[Path, ...]:
    if not sample_dir.is_dir():
        message = f"No existe el directorio de ejemplos: {sample_dir}"
        raise FileNotFoundError(message)
    return tuple(
        path
        for path in sorted(sample_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.suffix.lower() in _MIME_BY_SUFFIX
    )
