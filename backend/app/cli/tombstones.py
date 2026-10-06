"""Republica los tombstones: `python -m app.cli.tombstones`."""

from __future__ import annotations

import asyncio

from app.backup.service import publish_tombstones
from app.backup.wiring import publisher_for
from app.config import get_settings
from app.infrastructure.database import create_database_engine, create_session_factory


def main() -> int:
    """Sube el conjunto vigente. No imprime el HMAC ni el secreto."""
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        published = asyncio.run(publish_tombstones(factory, publisher_for(settings)))
    finally:
        asyncio.run(engine.dispose())
    print("tombstones status=ok" if published else "tombstones status=skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
