"""Punto de entrada local: `uv run python -m app`."""

import subprocess
import sys

from app.config import Settings, get_settings


def server_command(settings: Settings) -> list[str]:
    """Comando uvicorn que publica la fábrica `app.main:app`."""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        settings.api_host,
        "--port",
        str(settings.api_port),
    ]


def main() -> None:
    """Arranca la API con el host y el puerto configurados."""
    try:
        completed = subprocess.run(server_command(get_settings()), check=False)
    except KeyboardInterrupt as error:
        raise SystemExit(130) from error
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
