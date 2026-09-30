"""Base declarativa de SQLAlchemy."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Metadatos compartidos por los modelos ORM."""
