"""Parsing de scopes de API key."""

import pytest

from app.auth.service import parse_api_key_scopes
from app.domain.enums import ApiKeyScope
from app.domain.errors import InvalidStateError


def test_parses_allowed_scopes() -> None:
    scopes = parse_api_key_scopes(["query", "documents:read", "documents:write"])

    assert scopes == (
        ApiKeyScope.QUERY,
        ApiKeyScope.DOCUMENTS_READ,
        ApiKeyScope.DOCUMENTS_WRITE,
    )


def test_rejects_unknown_scope() -> None:
    with pytest.raises(InvalidStateError, match="desconocido"):
        parse_api_key_scopes(["query", "admin:all"])


def test_rejects_empty_scopes() -> None:
    with pytest.raises(InvalidStateError, match="al menos"):
        parse_api_key_scopes([])
