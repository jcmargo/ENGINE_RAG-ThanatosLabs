"""Identidad del principal, título y hash canónico de una pregunta."""

from __future__ import annotations

import hashlib
import json

from app.domain.entities import AuthPrincipal
from app.domain.enums import ConversationOwnerKind, PrincipalType
from app.domain.errors import AuthorizationError, InvalidStateError
from app.domain.history import AUTO_TITLE_CHARS, TITLE_MAX_CHARS, OwnerRef
from app.questions.models import QuestionRequest
from app.retrieval.service import normalize_question

_KEY_MAX = 200
_EMPTY_TITLE = "El título está vacío"
_LONG_TITLE = "El título supera el máximo"
_EMPTY_KEY = "La clave de idempotencia está vacía"
_LONG_KEY = "La clave de idempotencia supera el máximo"
_LONG_REASON = "La razón supera el máximo"
_OWNER_DENIED = "La identidad no puede usar conversaciones"


def owner_from(principal: AuthPrincipal) -> OwnerRef:
    """El dueño es quien pregunta: el usuario o la API key, nunca la empresa."""
    if principal.type is PrincipalType.API_KEY:
        if principal.api_key_id is None:
            raise AuthorizationError(_OWNER_DENIED)
        return OwnerRef(kind=ConversationOwnerKind.API_KEY, owner_id=principal.api_key_id)
    if (
        principal.type in {PrincipalType.TENANT_ADMIN, PrincipalType.MEMBER}
        and principal.user_id is not None
    ):
        return OwnerRef(kind=ConversationOwnerKind.USER, owner_id=principal.user_id)
    raise AuthorizationError(_OWNER_DENIED)


def clean_idempotency_key(value: str) -> str:
    """Normaliza la clave HTTP. El valor en claro no se guarda."""
    cleaned = value.strip()
    if not cleaned:
        raise InvalidStateError(_EMPTY_KEY)
    if len(cleaned) > _KEY_MAX:
        raise InvalidStateError(_LONG_KEY)
    return cleaned


def hash_key(value: str) -> str:
    """SHA-256 de la clave ya limpia."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_payload_hash(request: QuestionRequest) -> str:
    """Hash de la pregunta normalizada, el tope, los documentos y la conversación."""
    payload = {
        "conversation_id": (
            None if request.conversation_id is None else str(request.conversation_id)
        ),
        "document_ids": [str(item) for item in sorted(request.document_ids)],
        "question": normalize_question(request.question),
        "top_k": request.top_k,
    }
    if request.llm is not None and request.llm.strip():
        payload["llm"] = request.llm.strip()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clean_title(value: str) -> str:
    """Título visible, sin espacios sobrantes."""
    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidStateError(_EMPTY_TITLE)
    if len(cleaned) > TITLE_MAX_CHARS:
        raise InvalidStateError(_LONG_TITLE)
    return cleaned


def title_from_question(question: str) -> str:
    """Título corto para una conversación creada al preguntar."""
    cleaned = " ".join(question.split())
    if not cleaned:
        return "Conversación"
    if len(cleaned) <= AUTO_TITLE_CHARS:
        return cleaned
    return cleaned[:AUTO_TITLE_CHARS].rstrip()


def clean_reason(reason: str | None, *, max_chars: int) -> str | None:
    """Razón opcional. El vacío se guarda como ausencia."""
    if reason is None:
        return None
    cleaned = reason.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_chars:
        raise InvalidStateError(_LONG_REASON)
    return cleaned
