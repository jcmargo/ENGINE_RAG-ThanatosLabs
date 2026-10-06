"""Barrido de archivos huérfanos: gracia por mtime y claves referenciadas."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.documents.orphans import ORPHAN_GRACE, plan_orphan_deletions
from app.documents.store import iter_chunks
from app.infrastructure.local_files import LocalFileStore


def test_unreferenced_old_file_is_selected() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    key = f"{uuid4()}/{uuid4()}/{uuid4()}"
    plan = plan_orphan_deletions(
        stored_mtimes={key: now - ORPHAN_GRACE - timedelta(seconds=1)},
        staged_mtimes={},
        referenced_keys=frozenset(),
        now=now,
    )
    assert plan.storage_keys == (key,)
    assert plan.staged_names == ()


def test_unreferenced_fresh_file_inside_grace_is_kept() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    key = f"{uuid4()}/{uuid4()}/{uuid4()}"
    plan = plan_orphan_deletions(
        stored_mtimes={key: now - timedelta(seconds=30)},
        staged_mtimes={},
        referenced_keys=frozenset(),
        now=now,
    )
    assert plan.storage_keys == ()
    assert plan.total == 0


def test_referenced_key_is_kept_regardless_of_age() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    key = f"{uuid4()}/{uuid4()}/{uuid4()}"
    plan = plan_orphan_deletions(
        stored_mtimes={key: now - ORPHAN_GRACE - timedelta(hours=1)},
        staged_mtimes={},
        referenced_keys=frozenset({key}),
        now=now,
    )
    assert plan.storage_keys == ()


def test_old_staged_file_is_selected() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    name = uuid4().hex
    plan = plan_orphan_deletions(
        stored_mtimes={},
        staged_mtimes={name: now - ORPHAN_GRACE - timedelta(seconds=1)},
        referenced_keys=frozenset(),
        now=now,
    )
    assert plan.staged_names == (name,)


def test_local_store_sweep_deletes_old_orphans_and_keeps_fresh_and_referenced(
    tmp_path: Path,
) -> None:
    store = LocalFileStore(tmp_path)
    old_orphan = f"{uuid4()}/{uuid4()}/{uuid4()}"
    fresh_orphan = f"{uuid4()}/{uuid4()}/{uuid4()}"
    referenced = f"{uuid4()}/{uuid4()}/{uuid4()}"

    _write_promoted(store, old_orphan, b"viejo")
    _write_promoted(store, fresh_orphan, b"fresco")
    _write_promoted(store, referenced, b"referenciado")
    abandoned = asyncio.run(store.stage(iter_chunks([b"staging"]), max_bytes=100))

    now = datetime.now(UTC)
    _set_mtime(tmp_path.joinpath(*old_orphan.split("/")), now - ORPHAN_GRACE - timedelta(minutes=1))
    _set_mtime(tmp_path.joinpath(*fresh_orphan.split("/")), now - timedelta(seconds=10))
    _set_mtime(
        tmp_path.joinpath(*referenced.split("/")),
        now - ORPHAN_GRACE - timedelta(hours=2),
    )
    _set_mtime(tmp_path / ".staging" / abandoned.name, now - ORPHAN_GRACE - timedelta(minutes=1))

    plan = plan_orphan_deletions(
        stored_mtimes=store.list_stored_mtimes(),
        staged_mtimes=store.list_staged_mtimes(),
        referenced_keys=frozenset({referenced}),
        now=now,
    )
    for key in plan.storage_keys:
        store.delete(key)
    for name in plan.staged_names:
        store.discard_staged_name(name)

    assert not store.exists(old_orphan)
    assert store.exists(fresh_orphan)
    assert store.exists(referenced)
    assert abandoned.name not in store.list_staged_mtimes()
    assert plan.storage_keys == (old_orphan,)
    assert plan.staged_names == (abandoned.name,)


def test_commit_failure_cleanup_still_leaves_no_orphans(tmp_path: Path) -> None:
    """Réplica unitaria del fallo promote→commit: el except del servicio limpia."""
    store = LocalFileStore(tmp_path)
    staged = asyncio.run(store.stage(iter_chunks([b"sin fila"]), max_bytes=100))
    key = f"{uuid4()}/{uuid4()}/{uuid4()}"
    promoted = store.promote(staged, key)
    assert store.exists(promoted)
    store.delete(promoted)
    assert store.list_stored_mtimes() == {}
    assert store.list_staged_mtimes() == {}


def _write_promoted(store: LocalFileStore, key: str, payload: bytes) -> None:
    staged = asyncio.run(store.stage(iter_chunks([payload]), max_bytes=len(payload) + 1))
    store.promote(staged, key)


def _set_mtime(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
