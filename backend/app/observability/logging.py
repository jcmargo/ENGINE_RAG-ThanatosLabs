"""Logs JSON. El traceback interno sale redactado; la respuesta pública no lo ve."""

from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime
from typing import override

from app.observability.context import snapshot
from app.observability.redact import redact


class RedactFilter(logging.Filter):
    """Aplica la redacción al mensaje y al traceback antes de formatear."""

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{''.join(traceback.format_exception(*record.exc_info))}"
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(message)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento, con la correlación que haya en el contexto."""

    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    @override
    def format(self, record: logging.LogRecord) -> str:
        fields = snapshot()
        route = getattr(record, "route", None)
        if isinstance(route, str):
            fields["route"] = route
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "request_id": fields["request_id"],
            "job_id": fields["job_id"],
            "principal": fields["principal"],
            "tenant": fields["tenant"],
            "route": fields["route"],
            "result": getattr(record, "result", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str, *, service: str, environment: str) -> None:
    """Añade el formatter JSON y el filtro de redacción sin quitar el de las pruebas."""
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(item, RedactFilter) for item in root.filters):
        root.addFilter(RedactFilter())
    if not any(getattr(handler, "_engine_rag_json", False) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter(service, environment))
        handler._engine_rag_json = True  # type: ignore[attr-defined]
        root.addHandler(handler)
