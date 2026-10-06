"""El porcentaje de ingesta sube durante el embebido y el upsert."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from app.embeddings.fake import DeterministicFakeEmbeddingProvider
from app.ingestion.pipeline import (
    DocumentPipeline,
    embedding_progress,
    upsert_progress,
)


def test_embedding_progress_climbs_through_the_long_phase() -> None:
    assert embedding_progress(0, 6509) == 0
    assert embedding_progress(1, 6509) == 1
    previous = 0
    moves = 0
    for done in range(16, 6509, 16):
        progress = embedding_progress(done, 6509)
        assert 1 <= progress <= 80
        assert progress >= previous
        if progress > previous:
            moves += 1
        previous = progress
    assert embedding_progress(6509, 6509) == 80
    assert moves > 50


def test_upsert_progress_continues_after_embeddings() -> None:
    assert embedding_progress(10, 10) == 80
    assert upsert_progress(1, 100) == 81
    assert upsert_progress(100, 100) == 89
    assert upsert_progress(0, 100) == 0


def test_batches_publish_a_rising_percentage() -> None:
    asyncio.run(_assert_batches_publish())


async def _assert_batches_publish() -> None:
    store = _Store()
    pipeline = DocumentPipeline(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        store,  # type: ignore[arg-type]
        DeterministicFakeEmbeddingProvider(dimension=4),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        embed_batch_size=16,
        clock=_Clock(),
    )
    seen: list[int] = []

    async def report(progress: int) -> None:
        seen.append(progress)

    vectors = await pipeline._embed_vectors(["fragmento"] * 40, report)
    assert len(vectors) == 40
    assert seen == [32, 64, 80]
    await pipeline._upsert_records(None, _placeholders(100), report)  # type: ignore[arg-type]
    assert store.batches == [64, 36]
    assert seen[-2:] == [86, 89]
    assert seen == sorted(seen)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.batches: list[int] = []

    async def upsert(self, context: object, records: Sequence[object]) -> None:
        del context
        self.batches.append(len(records))


def _placeholders(count: int) -> list[object]:
    return [object() for _ in range(count)]
