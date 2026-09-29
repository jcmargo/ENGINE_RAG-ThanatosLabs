"""Normalización conservadora: conserva listas, títulos y párrafos."""

import re
import unicodedata

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")


def normalize_text(text: str) -> str:
    """Unifica Unicode y espacios sin borrar la estructura del texto."""
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_WHITESPACE.sub(" ", line).strip() for line in normalized.split("\n")]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run == 1:
                collapsed.append("")
            continue
        blank_run = 0
        collapsed.append(line)
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed)
