"""Cursor de paginación estable."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.errors import InvalidStateError
from app.domain.pagination import decode_page_cursor, encode_page_cursor


def test_cursor_roundtrip_keeps_utc() -> None:
    created_at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    entity_id = uuid4()
    decoded = decode_page_cursor(encode_page_cursor(created_at, entity_id))
    assert decoded.entity_id == entity_id
    assert decoded.created_at == created_at


def test_cursor_rejects_naive_and_garbage() -> None:
    with pytest.raises(InvalidStateError):
        encode_page_cursor(datetime(2026, 9, 29, 12, 0), uuid4())
    with pytest.raises(InvalidStateError):
        encode_page_cursor(
            datetime(2026, 9, 29, 12, 0, tzinfo=timezone(timedelta(hours=-5))),
            uuid4(),
        )
    with pytest.raises(InvalidStateError):
        decode_page_cursor("no-es-un-cursor")
