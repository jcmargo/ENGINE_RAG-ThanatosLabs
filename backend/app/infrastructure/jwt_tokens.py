"""Emisión y validación de JWT de acceso. Vive fuera del dominio."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import jwt

from app.domain.errors import AuthenticationError


class Clock(Protocol):
    """Reloj inyectable para pruebas de expiración sin dormir."""

    def now(self) -> datetime:
        """Instante actual timezone-aware en UTC."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Claims mínimos del access token tras validación."""

    subject: UUID
    jti: str
    expires_at: datetime
    tenant_id: UUID | None = None


class SystemClock:
    """Reloj del sistema en UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class JwtAccessTokenService:
    """Firma y valida JWT de acceso con iss, aud, sub, jti y exp."""

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        lifetime: timedelta,
        clock: Clock | None = None,
        jti_factory: Callable[[], str] | None = None,
    ) -> None:
        if len(secret.encode("utf-8")) < 32:
            message = "El secreto JWT debe tener al menos 32 bytes"
            raise ValueError(message)
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._lifetime = lifetime
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._jti_factory = jti_factory if jti_factory is not None else (lambda: str(uuid4()))

    def issue(self, *, subject: UUID, tenant_id: UUID | None = None) -> tuple[str, datetime]:
        """Emite un access token y su instante de expiración."""
        now = self._clock.now()
        expires_at = now + self._lifetime
        payload: dict[str, object] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(subject),
            "jti": self._jti_factory(),
            "exp": expires_at,
            "iat": now,
        }
        if tenant_id is not None:
            payload["tid"] = str(tenant_id)
        token = jwt.encode(payload, self._secret, algorithm="HS256")
        return token, expires_at

    def decode(self, token: str) -> AccessTokenClaims:
        """Valida firma, issuer, audience y expiración; no autoriza por sí solo."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["iss", "aud", "sub", "jti", "exp"],
                    "verify_exp": False,
                },
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError from error

        try:
            subject = UUID(str(payload["sub"]))
        except (KeyError, ValueError, TypeError) as error:
            raise AuthenticationError from error

        raw_tid = payload.get("tid")
        tenant_id: UUID | None = None
        if raw_tid is not None:
            try:
                tenant_id = UUID(str(raw_tid))
            except (ValueError, TypeError) as error:
                raise AuthenticationError from error

        exp_raw = payload["exp"]
        if isinstance(exp_raw, int | float):
            expires_at = datetime.fromtimestamp(exp_raw, tz=UTC)
        elif isinstance(exp_raw, datetime):
            expires_at = exp_raw if exp_raw.tzinfo is not None else exp_raw.replace(tzinfo=UTC)
        else:
            raise AuthenticationError

        if expires_at <= self._clock.now():
            raise AuthenticationError

        return AccessTokenClaims(
            subject=subject,
            jti=str(payload["jti"]),
            expires_at=expires_at,
            tenant_id=tenant_id,
        )
