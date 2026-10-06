"""Almacén local: staging, promoción y rechazo de rutas escapadas."""

import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.documents.store import iter_chunks
from app.domain.errors import InvalidStateError, PayloadTooLargeError
from app.infrastructure.local_files import LocalFileStore


def test_promote_writes_under_uuid_key_and_reads_back(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    staged = asyncio.run(store.stage(iter_chunks([b"hola documento"]), max_bytes=100))
    key = f"{uuid4()}/{uuid4()}/{uuid4()}"

    stored = store.promote(staged, key)

    assert stored == key
    assert store.exists(key)
    with store.open_read(key) as handle:
        assert handle.read() == b"hola documento"
    assert "hola documento" not in key


def test_stage_stops_when_the_stream_exceeds_the_limit(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with pytest.raises(PayloadTooLargeError):
        asyncio.run(store.stage(iter_chunks([b"abcdef", b"ghijk"]), max_bytes=8))
    assert _no_files(tmp_path)


def test_stage_rejects_an_empty_stream(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    with pytest.raises(InvalidStateError):
        asyncio.run(store.stage(iter_chunks([b""]), max_bytes=100))
    assert _no_files(tmp_path)


def test_promote_rejects_a_traversal_key(tmp_path: Path) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalFileStore(root)
    staged = asyncio.run(store.stage(iter_chunks([b"secreto"]), max_bytes=100))

    with pytest.raises(InvalidStateError):
        store.promote(staged, "../../otro-tenant/secreto.pdf")

    store.discard(staged)
    assert not (outside / "secreto.pdf").exists()
    assert _no_files(outside)
    assert _no_files(root)


def test_promote_rejects_a_symlink_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = LocalFileStore(root)
    staged = asyncio.run(store.stage(iter_chunks([b"secreto"]), max_bytes=100))
    tenant = uuid4()
    _link_directory(root / str(tenant), outside)

    with pytest.raises(InvalidStateError):
        store.promote(staged, f"{tenant}/{uuid4()}/{uuid4()}")

    assert _no_files(outside)


def _link_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("el sistema no permite crear enlaces simbólicos")


def _no_files(root: Path) -> bool:
    return not any(path.is_file() for path in root.rglob("*"))
