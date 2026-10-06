"""Contador de ventana fija compartido por las réplicas de la API."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.models.base import Base


class RateLimitBucketModel(Base):
    """Golpes de un bucket en una ventana. La clave es un HMAC, no un id."""

    __tablename__ = "rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False)
