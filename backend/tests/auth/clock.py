"""Helpers compartidos de autenticación en pruebas."""

from datetime import datetime, timedelta


class FakeClock:
    """Avanza el tiempo sin dormir."""

    def __init__(self, instant: datetime) -> None:
        self._now = instant

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta
