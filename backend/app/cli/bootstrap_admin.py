"""Bootstrap del primer provider admin. No imprime la contraseña."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from app.config import get_settings
from app.domain.entities import User
from app.domain.enums import SystemRole, UserStatus
from app.domain.normalize import normalize_email
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.passwords import PasswordHasher
from app.persistence.unit_of_work import UnitOfWork

_PASSWORD_ENV = "RAG_BOOTSTRAP_ADMIN_PASSWORD"


def main(argv: Sequence[str] | None = None) -> None:
    """Crea el primer provider admin o termina con error si ya existe uno."""
    args = _parse_args(argv)
    password = _resolve_password()
    try:
        asyncio.run(_bootstrap(email=args.email, password=password))
    except BootstrapError as error:
        print(error.message, file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Provider admin creado: {normalize_email(args.email)}")


class BootstrapError(Exception):
    """Error controlado del bootstrap."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.bootstrap_admin",
        description="Crea el primer provider admin. No imprime la contraseña.",
    )
    parser.add_argument("--email", required=True, help="Email del provider admin")
    return parser.parse_args(None if argv is None else list(argv))


def _resolve_password() -> str:
    env_password = os.environ.get(_PASSWORD_ENV)
    if env_password is not None and env_password != "":
        return env_password
    password = getpass.getpass("Contraseña del provider admin: ")
    if not password:
        message = "La contraseña no puede estar vacía"
        raise BootstrapError(message)
    confirm = getpass.getpass("Confirmar contraseña: ")
    if password != confirm:
        message = "Las contraseñas no coinciden"
        raise BootstrapError(message)
    return password


async def _bootstrap(*, email: str, password: str) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    hasher = PasswordHasher()
    try:
        async with UnitOfWork(session_factory) as uow:
            if await uow.users.exists_provider_admin():
                message = "Ya existe un provider admin; no se crea otro"
                raise BootstrapError(message)
            normalized = normalize_email(email)
            if await uow.users.exists_by_email(normalized):
                message = f"Ya existe un usuario con email {normalized}"
                raise BootstrapError(message)
            now = datetime.now(UTC)
            user = User(
                id=uuid4(),
                email=normalized,
                password_hash=hasher.hash(password),
                status=UserStatus.ACTIVE,
                system_role=SystemRole.PROVIDER_ADMIN,
                created_at=now,
                updated_at=now,
            )
            await uow.users.add(user)
            await uow.commit()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
