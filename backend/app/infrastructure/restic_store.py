"""Adaptador de restic. La contraseña se lee de un archivo y no se registra."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.errors import InvalidStateError
from app.domain.lifecycle import (
    RESTIC_KEEP_DAILY,
    RESTIC_KEEP_MONTHLY,
    RESTIC_KEEP_WEEKLY,
    assert_repository_location,
)


class ResticRepository:
    """Repositorio cifrado. `forget` puede usar otra credencial si el proveedor lo permite."""

    def __init__(
        self,
        repository: str,
        password_file: Path,
        *,
        binary: str = "restic",
        prune_password_file: Path | None = None,
        protected_paths: tuple[str, ...] = (),
    ) -> None:
        assert_repository_location(repository, protected_paths)
        self._repository = repository
        self._password_file = password_file
        self._binary = binary
        self._prune_password_file = prune_password_file

    def publish(self, directory: Path, *, tags: tuple[str, ...]) -> str:
        """Inicializa si hace falta, sube el directorio y devuelve el snapshot."""
        self._ensure_initialized()
        stdout = self._run(["backup", str(directory), "--json", *_tag_args(tags)])
        return _snapshot_id(stdout)

    def verify(self) -> None:
        """Comprueba la estructura del repositorio."""
        self._run(["check"])

    def apply_retention(self) -> None:
        """Conserva 7 diarios, 4 semanales y 6 mensuales."""
        password = self._prune_password_file or self._password_file
        self._run(
            [
                "forget",
                "--keep-daily",
                str(RESTIC_KEEP_DAILY),
                "--keep-weekly",
                str(RESTIC_KEEP_WEEKLY),
                "--keep-monthly",
                str(RESTIC_KEEP_MONTHLY),
                "--prune",
            ],
            password_file=password,
        )

    def publish_bytes(self, name: str, payload: bytes, *, tags: tuple[str, ...]) -> None:
        """Sube un archivo suelto con sus etiquetas."""
        self._ensure_initialized()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / name
            path.write_bytes(payload)
            self._run(["backup", str(path), "--json", *_tag_args(tags)])

    def latest_bytes(self, name: str, *, tag: str) -> bytes:
        """Extrae un archivo del snapshot más reciente de esa etiqueta."""
        completed = self._run_raw(["dump", "latest", name, "--tag", tag])
        if completed.returncode != 0:
            message = "No hay tombstones en el repositorio"
            raise InvalidStateError(message)
        return completed.stdout

    def restore_snapshot(self, snapshot_id: str, destination: Path) -> None:
        """Materializa el snapshot."""
        destination.mkdir(parents=True, exist_ok=True)
        self._run(["restore", snapshot_id, "--target", str(destination)])

    def _ensure_initialized(self) -> None:
        probe = self._run_raw(["snapshots", "--json"])
        if probe.returncode == 0:
            return
        self._run(["init"])

    def _run(self, args: list[str], *, password_file: Path | None = None) -> str:
        completed = self._run_raw(args, password_file=password_file)
        if completed.returncode != 0:
            message = "restic falló"
            raise InvalidStateError(message)
        return completed.stdout.decode("utf-8", errors="replace")

    def _run_raw(
        self,
        args: list[str],
        *,
        password_file: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["RESTIC_REPOSITORY"] = self._repository
        env["RESTIC_PASSWORD_FILE"] = str(password_file or self._password_file)
        env.pop("RESTIC_PASSWORD", None)
        return subprocess.run(
            [self._binary, *args],
            env=env,
            capture_output=True,
            check=False,
        )


def _tag_args(tags: tuple[str, ...]) -> list[str]:
    args: list[str] = []
    for tag in tags:
        args.extend(["--tag", tag])
    return args


class DockerResticRepository:
    """restic dentro de la imagen fijada. El simulacro no necesita el binario en el host."""

    def __init__(
        self,
        repository: Path,
        password_file: Path,
        *,
        image: str,
        protected_paths: tuple[str, ...] = (),
    ) -> None:
        assert_repository_location(str(repository), protected_paths)
        self._repository = repository
        self._password_file = password_file
        self._image = image

    def publish(self, directory: Path, *, tags: tuple[str, ...]) -> str:
        """Sube el directorio montado en `/data`."""
        self._ensure_initialized()
        completed = self._docker(["backup", "/data", "--json", *_tag_args(tags)], data=directory)
        return _snapshot_id(_text(completed))

    def verify(self) -> None:
        """Comprueba el repositorio."""
        self._docker(["check"])

    def apply_retention(self) -> None:
        """Misma política inicial: 7 diarios, 4 semanales y 6 mensuales."""
        self._docker(
            [
                "forget",
                "--keep-daily",
                str(RESTIC_KEEP_DAILY),
                "--keep-weekly",
                str(RESTIC_KEEP_WEEKLY),
                "--keep-monthly",
                str(RESTIC_KEEP_MONTHLY),
                "--prune",
            ]
        )

    def publish_bytes(self, name: str, payload: bytes, *, tags: tuple[str, ...]) -> None:
        """Sube un archivo suelto."""
        self._ensure_initialized()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / name
            path.write_bytes(payload)
            self._docker(["backup", "/data", "--json", *_tag_args(tags)], data=Path(raw))

    def latest_bytes(self, name: str, *, tag: str) -> bytes:
        """Lee el archivo del snapshot más reciente."""
        completed = self._docker(["dump", "latest", name, "--tag", tag], check=False)
        if completed.returncode != 0:
            message = "No hay tombstones en el repositorio"
            raise InvalidStateError(message)
        return completed.stdout

    def restore_snapshot(self, snapshot_id: str, destination: Path) -> None:
        """Materializa el snapshot en el directorio montado."""
        destination.mkdir(parents=True, exist_ok=True)
        self._docker(["restore", snapshot_id, "--target", "/restore"], restore=destination)

    def _ensure_initialized(self) -> None:
        probe = self._docker(["snapshots", "--json"], check=False)
        if probe.returncode == 0:
            return
        self._docker(["init"])

    def _docker(
        self,
        args: list[str],
        *,
        data: Path | None = None,
        restore: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{self._repository}:/repo",
            "-v",
            f"{self._password_file}:/password:ro",
            "-e",
            "RESTIC_REPOSITORY=/repo",
            "-e",
            "RESTIC_PASSWORD_FILE=/password",
        ]
        if data is not None:
            command.extend(["-v", f"{data}:/data"])
        if restore is not None:
            command.extend(["-v", f"{restore}:/restore"])
        command.append(self._image)
        command.extend(args)
        completed = subprocess.run(command, capture_output=True, check=False)
        if check and completed.returncode != 0:
            message = "restic falló"
            raise InvalidStateError(message)
        return completed


def _text(completed: subprocess.CompletedProcess[bytes]) -> str:
    return completed.stdout.decode("utf-8", errors="replace")


def _snapshot_id(stdout: str) -> str:
    found = ""
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if isinstance(payload, dict) and payload.get("message_type") == "summary":
            snapshot = payload.get("snapshot_id")
            if isinstance(snapshot, str):
                found = snapshot
    if not found:
        message = "restic no devolvió el snapshot"
        raise InvalidStateError(message)
    return found
