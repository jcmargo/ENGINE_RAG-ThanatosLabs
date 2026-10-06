"""pg_dump y pg_restore. La contraseña va en el entorno, no en la línea de comandos."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.domain.errors import InvalidStateError


class PgDumpClient:
    """Cliente de PostgreSQL 17. No registra la URL ni la contraseña."""

    def __init__(
        self, database_url: str, *, dump_bin: str = "pg_dump", restore_bin: str = "pg_restore"
    ) -> None:
        self._database_url = database_url
        self._dump_bin = dump_bin
        self._restore_bin = restore_bin

    def dump(self, destination: Path) -> None:
        """Escribe un dump custom."""
        env, args = _connection(self._database_url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run([self._dump_bin, *args, "-Fc", "--no-owner", "--no-acl", "-f", str(destination)], env)

    def restore(self, source: Path) -> None:
        """Restaura el dump custom."""
        env, args = _connection(self._database_url)
        _run(
            [
                self._restore_bin,
                *args,
                "--no-owner",
                "--no-acl",
                "--clean",
                "--if-exists",
                "--exit-on-error",
                str(source),
            ],
            env,
        )


class ComposePostgres:
    """pg_dump dentro del contenedor, para el simulacro aislado."""

    def __init__(self, compose: tuple[str, ...], *, user: str, database: str) -> None:
        self._compose = compose
        self._user = user
        self._database = database

    def dump(self, destination: Path) -> None:
        """Escribe el dump custom en el host."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            completed = subprocess.run(
                [
                    *self._compose,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "-U",
                    self._user,
                    "-d",
                    self._database,
                    "-Fc",
                    "--no-owner",
                    "--no-acl",
                ],
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            message = "pg_dump falló"
            raise InvalidStateError(message)

    def restore(self, source: Path) -> None:
        """Vacía el esquema público y aplica el dump."""
        reset = subprocess.run(
            [
                *self._compose,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                self._user,
                "-d",
                self._database,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
            ],
            capture_output=True,
            check=False,
        )
        if reset.returncode != 0:
            message = "No se pudo vaciar la base del simulacro"
            raise InvalidStateError(message)
        with source.open("rb") as handle:
            completed = subprocess.run(
                [
                    *self._compose,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "-U",
                    self._user,
                    "-d",
                    self._database,
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                ],
                stdin=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            message = "pg_restore falló"
            raise InvalidStateError(message)


def _connection(database_url: str) -> tuple[dict[str, str], list[str]]:
    raw = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parts = urlsplit(raw)
    if not parts.hostname or not parts.username or parts.password is None or not parts.path:
        message = "La URL de PostgreSQL no alcanza para pg_dump"
        raise InvalidStateError(message)
    env = os.environ.copy()
    env["PGPASSWORD"] = unquote(parts.password)
    database = unquote(parts.path.lstrip("/"))
    args = [
        "-h",
        parts.hostname,
        "-p",
        str(parts.port or 5432),
        "-U",
        unquote(parts.username),
        "-d",
        database,
    ]
    return env, args


def _run(argv: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(argv, env=env, capture_output=True, check=False)
    if completed.returncode != 0:
        message = "pg_dump o pg_restore falló"
        raise InvalidStateError(message)
