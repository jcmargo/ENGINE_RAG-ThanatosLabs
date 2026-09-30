"""JWT de acceso con reloj inyectable."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.errors import AuthenticationError
from app.infrastructure.jwt_tokens import JwtAccessTokenService


class FakeClock:
    def __init__(self, instant: datetime) -> None:
        self._now = instant

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _service(clock: FakeClock) -> JwtAccessTokenService:
    return JwtAccessTokenService(
        secret="unit-test-jwt-secret-at-least-32b",
        issuer="engine-rag-test",
        audience="engine-rag-api-test",
        lifetime=timedelta(minutes=15),
        clock=clock,
    )


def test_issue_and_decode_roundtrip() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    service = _service(clock)
    subject = uuid4()
    tenant_id = uuid4()

    token, expires_at = service.issue(subject=subject, tenant_id=tenant_id)
    claims = service.decode(token)

    assert claims.subject == subject
    assert claims.tenant_id == tenant_id
    assert claims.expires_at == expires_at
    assert claims.jti


def test_expired_token_is_rejected_without_sleep() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    service = _service(clock)
    token, _ = service.issue(subject=uuid4())

    clock.advance(timedelta(minutes=16))

    with pytest.raises(AuthenticationError):
        service.decode(token)
