"""Consulta de un seguimiento. Solo une textos; no llama a red ni a SQL."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

RECENT_USER_QUESTIONS = 6
RECENT_TOPICS = 2
FOLLOW_UP_PARTS = frozenset({"definicion", "demostracion", "ejemplo"})

_TopicOf = Callable[[str], tuple[str, str] | None]
_FILLERS = frozenset(
    {
        "a",
        "al",
        "con",
        "cual",
        "dame",
        "de",
        "del",
        "dime",
        "el",
        "en",
        "es",
        "esa",
        "ese",
        "esta",
        "este",
        "favor",
        "la",
        "las",
        "lo",
        "los",
        "me",
        "muestra",
        "muestrame",
        "por",
        "que",
        "quiero",
        "significa",
        "su",
        "un",
        "una",
        "ver",
        "y",
    }
)
_NUMBER = re.compile(r"\d+(?:\.\d+)+")


@dataclass(frozen=True)
class FollowUpPlan:
    """Búsqueda de un turno. `part` vacío deja la unión de la pregunta anterior."""

    query: str
    part: str | None
    topics: tuple[tuple[str, str], ...]


def follow_up_query(current: str, previous_user: str | None, *, max_chars: int) -> str:
    """Une la pregunta anterior con la actual, o devuelve la actual sin tocarla.

    La anterior vacía, o igual a la actual tras colapsar espacios, no cambia la
    consulta. Si la unión supera `max_chars`, se recorta la anterior por el
    principio y la actual queda entera.
    """
    if previous_user is None or _same_or_blank(previous_user, current):
        return current
    room = max_chars - len(current) - 1
    if room < 1:
        return current
    earlier = previous_user if len(previous_user) <= room else previous_user[-room:]
    if _collapsed(earlier) == "":
        return current
    return f"{earlier}\n{current}"


def requested_part(question: str, *, topic_of: _TopicOf) -> str | None:
    """«definición», «demostración» o «ejemplo» cuando no introducen otro tema.

    «dime la demostración» y «y un ejemplo» cuentan. Una frase que además nombra
    un rótulo sigue siendo esa parte, de ese rótulo. Cualquier otra pregunta no.
    """
    content = [token for token in _fold(question).split() if token not in _FILLERS]
    parts = [token for token in content if token in FOLLOW_UP_PARTS]
    if len(parts) != 1:
        return None
    rest = [
        token
        for token in content
        if token not in FOLLOW_UP_PARTS and _NUMBER.fullmatch(token) is None
    ]
    if not rest:
        return parts[0]
    named = topic_of(question)
    if named is None or len(rest) != 1 or rest[0] != _fold(named[0]):
        return None
    return parts[0]


def plan_follow_up(
    current: str,
    recent: Sequence[str],
    *,
    max_chars: int,
    topic_of: _TopicOf,
) -> FollowUpPlan:
    """Arma la consulta. Los temas van del más nuevo al más viejo, como máximo dos.

    `recent` son preguntas de usuario ya ordenadas así, sin respuestas. Una parte
    suelta usa esos rótulos. Si la pregunta nombra el suyo, no mira los demás.
    Una pregunta que no pide parte solo se une con la más reciente.
    """
    part = requested_part(current, topic_of=topic_of)
    if part is None:
        if topic_of(current) is not None:
            return FollowUpPlan(query=current, part=None, topics=())
        previous = recent[0] if recent else None
        return FollowUpPlan(
            query=follow_up_query(current, previous, max_chars=max_chars),
            part=None,
            topics=(),
        )
    own = topic_of(current)
    topics = (own,) if own is not None else _topics(recent, topic_of)
    seed = None if own is not None else _seed(recent, topics, topic_of)
    return FollowUpPlan(
        query=follow_up_query(current, seed, max_chars=max_chars),
        part=part,
        topics=topics,
    )


def _topics(recent: Sequence[str], topic_of: _TopicOf) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for question in recent[:RECENT_USER_QUESTIONS]:
        named = topic_of(question)
        if named is None or named in found:
            continue
        found.append(named)
        if len(found) == RECENT_TOPICS:
            break
    return tuple(found)


def _seed(
    recent: Sequence[str],
    topics: tuple[tuple[str, str], ...],
    topic_of: _TopicOf,
) -> str | None:
    if not topics:
        return None
    wanted = topics[0]
    for question in recent[:RECENT_USER_QUESTIONS]:
        if topic_of(question) == wanted:
            return question
    return None


def _same_or_blank(previous_user: str, current: str) -> bool:
    earlier = _collapsed(previous_user)
    return earlier == "" or earlier == _collapsed(current)


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(stripped.split())
