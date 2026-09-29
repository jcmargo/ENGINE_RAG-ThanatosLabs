"""El comando local imprime fragmentos y no una respuesta generada."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.cli.local_search import format_hit, main
from app.config import Environment, LogLevel, Settings
from app.documents.chunking import WordTokenCounter
from app.documents.models import Chunk
from app.retrieval.models import SearchResult
from app.retrieval.sample_search import LocalSearchHit


def _hit() -> LocalSearchHit:
    chunk = Chunk(
        id="abc",
        text="ThanatosLabs otorga 18 días hábiles de vacaciones.",
        page_start=1,
        page_end=1,
        section="Política de vacaciones",
        position=0,
        chunker_version="chunker-v1",
    )
    return LocalSearchHit(
        result=SearchResult(chunk=chunk, score=0.8123456, position=1),
        source="politica-vacaciones.md",
    )


class FixedSearcher:
    def search(self, question: str, top_k: int) -> tuple[LocalSearchHit, ...]:
        assert question == "¿Cuántos días de descanso anual tengo?"
        assert top_k == 5
        return (_hit(),)


def test_format_hit_prints_rank_score_source_page_and_text() -> None:
    line = format_hit(_hit())

    assert line == (
        "1\t0.812346\tpolitica-vacaciones.md\t1\tThanatosLabs otorga 18 días hábiles de vacaciones."
    )
    assert "Respuesta:" not in line


def test_main_prints_hits_without_a_generated_answer(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        ["--question", "¿Cuántos días de descanso anual tengo?", "--top-k", "5"],
        searcher=FixedSearcher(),
    )
    captured = capsys.readouterr()

    assert "18 días hábiles" in captured.out
    assert "politica-vacaciones.md" in captured.out
    assert "Respuesta:" not in captured.out
    assert "abstención" not in captured.out.lower()
    assert captured.out.count("\n") == 1


class _OfflineProvider:
    def __init__(self, model_name: str, *, cache_dir: Path | None = None) -> None:
        del model_name, cache_dir

    @property
    def dimension(self) -> int:
        return 4

    @property
    def model_version(self) -> str:
        return "cli-offline"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0, 0.0]


def _offline_counter(model_name: str, *, cache_dir: Path | None = None) -> WordTokenCounter:
    del model_name, cache_dir
    return WordTokenCounter()


def _fail_model_load(*args: object, **kwargs: object) -> None:
    del args, kwargs
    message = "la suite normal no carga el modelo"
    raise AssertionError(message)


def test_main_searches_samples_without_loading_the_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
        embedding_model="intfloat/multilingual-e5-base",
        model_cache_dir=None,
    )
    monkeypatch.setattr("app.cli.local_search.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.cli.local_search.SentenceTransformerEmbeddingProvider", _OfflineProvider
    )
    monkeypatch.setattr("app.cli.local_search.HuggingFaceTokenCounter", _offline_counter)
    monkeypatch.setattr(
        "app.embeddings.sentence_transformer._load_sentence_transformer",
        _fail_model_load,
    )

    main(["--question", "¿Cuántos días de descanso anual tengo?", "--top-k", "5"])
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line]

    assert lines
    assert len(lines) <= 5
    for line in lines:
        rank, score, source, page, text = line.split("\t", maxsplit=4)
        assert rank.isdigit()
        float(score)
        assert source.endswith(".md")
        assert page.isdigit()
        assert text
    assert "Respuesta:" not in captured.out
    assert "abstención" not in captured.out.lower()


def test_main_rejects_non_positive_top_k() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--question", "vacaciones", "--top-k", "0"], searcher=FixedSearcher())

    assert caught.value.code == 2
