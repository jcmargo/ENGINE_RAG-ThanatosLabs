"""Escribe el OpenAPI sin arrancar lifespan, bases ni modelos."""

from __future__ import annotations

import json

from app.config import Environment, LogLevel, Settings, repository_root
from app.main import create_app


def main() -> None:
    """Genera `frontend/openapi.json` desde la aplicación de prueba."""
    application = create_app(
        Settings(environment=Environment.TEST, log_level=LogLevel.INFO),
    )
    destination = repository_root() / "frontend" / "openapi.json"
    schema = json.dumps(application.openapi(), ensure_ascii=False, indent=2)
    destination.write_text(f"{schema}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
