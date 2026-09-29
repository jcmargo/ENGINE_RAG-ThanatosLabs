"""El comando local imprime la respuesta fundamentada."""

import pytest

from app.cli.local_rag import format_run, main
from app.documents.models import Chunk
from app.generation.errors import LLMUnavailableError
from app.generation.models import (
    PROMPT_VERSION,
    PUBLIC_ABSTENTION,
    RETRIEVAL_VERSION,
    Citation,
    GroundedAnswer,
    GroundedRun,
)
from app.retrieval.models import SearchResult
from app.retrieval.sample_search import LocalSearchHit


def _run(*, called: bool) -> GroundedRun:
    chunk = Chunk(
        id="chunk-real",
        text="ThanatosLabs otorga 18 días hábiles de vacaciones.",
        page_start=1,
        page_end=1,
        section="Política de vacaciones",
        position=0,
        chunker_version="chunker-v1",
    )
    hit = LocalSearchHit(
        result=SearchResult(chunk=chunk, score=0.866878, position=1),
        source="politica-vacaciones.md",
    )
    citations = ()
    answer = PUBLIC_ABSTENTION
    if called:
        answer = "Son 18 días hábiles."
        citations = (
            Citation(
                chunk_id=chunk.id,
                source=hit.source,
                page_start=1,
                page_end=1,
                section=chunk.section,
                excerpt=chunk.text,
            ),
        )
    return GroundedRun(
        answer=GroundedAnswer(
            answer=answer,
            abstained=not called,
            citations=citations,
            retrieval_version=RETRIEVAL_VERSION,
            prompt_version=PROMPT_VERSION,
            model_version="qwen2.5:7b",
        ),
        retrieved=(hit,),
        retrieval_ms=12.5,
        generation_ms=30.0 if called else 0.0,
        llm_called=called,
    )


class _FixedRunner:
    def __init__(self, run: GroundedRun) -> None:
        self._run = run

    def answer(self, question: str, *, top_k: int) -> GroundedRun:
        assert question == "¿Cuántos días de descanso anual tengo?"
        assert top_k == 5
        return self._run


class _DownRunner:
    def answer(self, question: str, *, top_k: int) -> GroundedRun:
        del question, top_k
        message = (
            "Ollama no está disponible en http://127.0.0.1:11434. "
            "Inicia Ollama y comprueba que el modelo qwen2.5:7b está descargado."
        )
        raise LLMUnavailableError(message)


def test_format_includes_answer_citations_scores_and_latencies() -> None:
    text = format_run(_run(called=True))

    assert "Respuesta:\tSon 18 días hábiles." in text
    assert "Abstención:\tno" in text
    assert "politica-vacaciones.md" in text
    assert "0.866878" in text
    assert "recuperación_ms\t12.500" in text
    assert "generación_ms\t30.000" in text
    assert "LLM:\tsí" in text
    assert "prompt-v4" in text


def test_main_prints_an_abstention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(
        ["--question", "¿Cuántos días de descanso anual tengo?", "--top-k", "5"],
        runner=_FixedRunner(_run(called=False)),
    )

    captured = capsys.readouterr()
    assert PUBLIC_ABSTENTION in captured.out
    assert "Abstención:\tsí" in captured.out
    assert "LLM:\tno" in captured.out
    assert "ninguna" in captured.out
    assert captured.err == ""


def test_main_when_ollama_is_down_is_actionable_and_has_no_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            ["--question", "¿Cuántos días de descanso anual tengo?"],
            runner=_DownRunner(),
        )

    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Inicia Ollama" in captured.err
    assert "Traceback" not in captured.err
    assert "18 días" not in captured.err
    assert "politica-vacaciones.md" not in captured.err


def test_main_rejects_a_non_positive_top_k(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--question", "¿Cuántos días?", "--top-k", "0"], runner=_DownRunner())

    assert caught.value.code == 2
    assert "top-k" in capsys.readouterr().err
