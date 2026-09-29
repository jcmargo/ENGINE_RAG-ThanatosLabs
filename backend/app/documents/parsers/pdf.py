"""Parser de PDF textual mediante PyMuPDF."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import pymupdf

from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.parsers.base import DocumentParseError
from app.documents.versions import PDF_PARSER_VERSION

# Bit 0 de flags del span: superíndice (TEXT_FONT_SUPERSCRIPT).
_SUPERSCRIPT_FLAG = 1
# Hueco relativo al tamaño de fuente a partir del cual hay espacio entre palabras.
_WORD_GAP_RATIO = 0.15

_DIFFERENCES_RE = re.compile(r"/Differences\s*\[(.*?)\]", re.DOTALL)
_DIFF_TOKEN_RE = re.compile(r"/([A-Za-z][A-Za-z0-9._]*)|(-?\d+)")
_PI_FAMILY_RE = re.compile(r"WWPI\d+")

# Mathematical Pi: el nombre del glifo miente (three/five) y el dibujo es el signo.
# Códigos leídos de la fuente WWPI01 y WWPI04, no del texto vecino.
_WWPI01_CODES: dict[int, str] = {
    0x23: "\u2264",
    0x24: "\u2265",
    0x2C: "<",
    0x2E: ">",
    0x2F: "/",
    0x30: '"',
    0x31: "+",
    0x32: "\u2212",
    0x33: "\u00d7",
    0x35: "=",
    0x36: "\u00b1",
    0x39: "\u2032",
    0x3F: ".",
    0x44: "\u0394",
    0x53: "\u03a3",
    0x5B: "\u2205",
    0x61: "\u03b1",
    0x62: "\u03b2",
    0x64: "\u03b4",
    0x65: "\u03b5",
    0x67: "\u03b3",
    0x69: "\u03b9",
    0x6C: "\u03bb",
    0x6D: "\u03bc",
    0x6E: "\u03bd",
    0x70: "\u03c0",
    0x72: "\u03c1",
    0x73: "\u03c3",
    0x75: "\u03b8",
    0x76: "\u03c9",
    0x77: "\u03c6",
    0x78: "\u03c7",
    0x79: "\u03c5",
    0x7A: "\u03b6",
}
_WWPI04_CODES: dict[int, str] = {
    0x2C: "\u2282",
    0x31: "+",
    0x32: "\u2212",
    0x33: "\u00d7",
    0x35: "=",
    0x39: "\u2032",
    0x3F: ".",
    0x61: "\u03b1",
    0x72: "\u03c1",
    0x75: "\u03b8",
}
_PI_FONT_CODES: dict[str, dict[int, str]] = {
    "WWPI01": _WWPI01_CODES,
    "WWPI04": _WWPI04_CODES,
}

# Nombres Adobe habituales en fuentes matemáticas → Unicode.
MATH_GLYPH_TO_UNICODE: dict[str, str] = {
    "multiply": "\u00d7",
    "equal": "=",
    "minus": "\u2212",
    "hyphen": "-",
    "plus": "+",
    "plusminus": "\u00b1",
    "minusplus": "\u2213",
    "divide": "\u00f7",
    "lessequal": "\u2264",
    "greaterequal": "\u2265",
    "notequal": "\u2260",
    "approxequal": "\u2248",
    "equivalence": "\u2261",
    "similar": "\u223c",
    "congruent": "\u2245",
    "proportional": "\u221d",
    "infinity": "\u221e",
    "partialdiff": "\u2202",
    "summation": "\u2211",
    "product": "\u220f",
    "integral": "\u222b",
    "radical": "\u221a",
    "element": "\u2208",
    "notelement": "\u2209",
    "suchthat": "\u220b",
    "intersection": "\u2229",
    "union": "\u222a",
    "subset": "\u2282",
    "superset": "\u2283",
    "propersubset": "\u2282",
    "propersuperset": "\u2283",
    "reflexsubset": "\u2286",
    "reflexsuperset": "\u2287",
    "logicaland": "\u2227",
    "logicalor": "\u2228",
    "logicalnot": "\u00ac",
    "arrowright": "\u2192",
    "arrowleft": "\u2190",
    "arrowboth": "\u2194",
    "arrowdblright": "\u21d2",
    "arrowdblleft": "\u21d0",
    "therefore": "\u2234",
    "perpendicular": "\u22a5",
    "parallel": "\u2225",
    "angle": "\u2220",
    "periodcentered": "\u00b7",
    "bullet": "\u2022",
    "asteriskmath": "\u2217",
    "dotmath": "\u22c5",
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "gamma": "\u03b3",
    "delta": "\u03b4",
    "epsilon": "\u03b5",
    "zeta": "\u03b6",
    "eta": "\u03b7",
    "theta": "\u03b8",
    "iota": "\u03b9",
    "kappa": "\u03ba",
    "lambda": "\u03bb",
    "mu": "\u03bc",
    "nu": "\u03bd",
    "xi": "\u03be",
    "pi": "\u03c0",
    "rho": "\u03c1",
    "sigma": "\u03c3",
    "tau": "\u03c4",
    "upsilon": "\u03c5",
    "phi": "\u03c6",
    "chi": "\u03c7",
    "psi": "\u03c8",
    "omega": "\u03c9",
    "Gamma": "\u0393",
    "Delta": "\u0394",
    "Theta": "\u0398",
    "Lambda": "\u039b",
    "Xi": "\u039e",
    "Pi": "\u03a0",
    "Sigma": "\u03a3",
    "Upsilon": "\u03a5",
    "Phi": "\u03a6",
    "Psi": "\u03a8",
    "Omega": "\u03a9",
}

_SUPERSCRIPT_CHARS: dict[str, str] = {
    "0": "\u2070",
    "1": "\u00b9",
    "2": "\u00b2",
    "3": "\u00b3",
    "4": "\u2074",
    "5": "\u2075",
    "6": "\u2076",
    "7": "\u2077",
    "8": "\u2078",
    "9": "\u2079",
    "+": "\u207a",
    "-": "\u207b",
    "\u2212": "\u207b",
    "=": "\u207c",
    "(": "\u207d",
    ")": "\u207e",
    "n": "\u207f",
    "i": "\u2071",
    "a": "\u1d43",
    "b": "\u1d47",
    "c": "\u1d9c",
    "d": "\u1d48",
    "e": "\u1d49",
    "f": "\u1da0",
    "g": "\u1d4d",
    "h": "\u02b0",
    "j": "\u02b2",
    "k": "\u1d4f",
    "l": "\u02e1",
    "m": "\u1d50",
    "o": "\u1d52",
    "p": "\u1d56",
    "r": "\u02b3",
    "s": "\u02e2",
    "t": "\u1d57",
    "u": "\u1d58",
    "v": "\u1d5b",
    "w": "\u02b7",
    "x": "\u02e3",
    "y": "\u02b8",
    "z": "\u1dbb",
    "A": "\u1d2c",
    "B": "\u1d2e",
    "D": "\u1d30",
    "E": "\u1d31",
    "G": "\u1d33",
    "H": "\u1d34",
    "I": "\u1d35",
    "J": "\u1d36",
    "K": "\u1d37",
    "L": "\u1d38",
    "M": "\u1d39",
    "N": "\u1d3a",
    "O": "\u1d3c",
    "P": "\u1d3e",
    "R": "\u1d3f",
    "T": "\u1d40",
    "U": "\u1d41",
    "V": "\u2c7d",
    "W": "\u1d42",
}


@dataclass(frozen=True)
class PdfTextSpan:
    """Span de texto de prueba o de extracción (fuente, flags y caja)."""

    text: str
    font: str
    flags: int
    bbox: tuple[float, float, float, float]
    size: float = 11.0


class _PdfPage(Protocol):
    def get_text(self, option: str) -> object:
        """Texto o diccionario de la página."""
        ...

    def get_fonts(self, full: bool = False) -> list[tuple[object, ...]]:
        """Fuentes referenciadas en la página."""
        ...


class _PdfDocument(Protocol):
    needs_pass: int

    def close(self) -> None:
        """Cierra el documento."""
        ...

    def __iter__(self) -> Iterator[_PdfPage]:
        """Recorre las páginas."""
        ...

    def xref_get_key(self, xref: int, key: str) -> tuple[str, str]:
        """Lee una clave del objeto xref."""
        ...

    def xref_object(self, xref: int) -> str:
        """Serializa el objeto xref."""
        ...


def _open_pdf(content: bytes) -> _PdfDocument:
    # PyMuPDF no anota `Document`: la llamada y la iteración quedan fuera del tipo.
    opened = pymupdf.open(stream=content, filetype="pdf")  # type: ignore[no-untyped-call]
    return cast(_PdfDocument, opened)


def unicode_for_glyph(code: int, glyph_name: str) -> str:
    """Traduce un nombre de glifo conocido; si no está en la tabla, usa el código."""
    mapped = MATH_GLYPH_TO_UNICODE.get(glyph_name)
    if mapped is not None:
        return mapped
    return chr(code)


def pi_font_family(font: str) -> str | None:
    """Familia WWPI del nombre, aunque venga con prefijo de subconjunto."""
    match = _PI_FAMILY_RE.search(font)
    if match is None:
        return None
    return match.group(0)


def decode_span_text(
    text: str,
    differences: Mapping[int, str] | None,
    font: str = "",
) -> str:
    """Traduce glifos matemáticos. Una fuente de texto conserva el 3."""
    family = pi_font_family(font)
    pi_codes = _PI_FONT_CODES.get(family) if family is not None else None
    if not differences and not pi_codes:
        return text
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if pi_codes is not None and code in pi_codes:
            chars.append(pi_codes[code])
            continue
        glyph_name = differences.get(code) if differences else None
        if glyph_name is None:
            chars.append(char)
            continue
        mapped = MATH_GLYPH_TO_UNICODE.get(glyph_name)
        chars.append(mapped if mapped is not None else char)
    return "".join(chars)


def to_superscript(text: str) -> str:
    """Convierte caracteres a formas Unicode de superíndice cuando existen."""
    return "".join(_SUPERSCRIPT_CHARS.get(char, char) for char in text)


def reconstruct_line(
    spans: list[PdfTextSpan],
    font_differences: Mapping[str, Mapping[int, str]],
) -> str:
    """Une spans de una línea: glifos matemáticos, superíndices y espacios reales."""
    parts: list[str] = []
    previous: PdfTextSpan | None = None
    for span in spans:
        differences = font_differences.get(span.font)
        decoded = decode_span_text(span.text, differences, font=span.font)
        if not decoded:
            continue
        is_superscript = bool(span.flags & _SUPERSCRIPT_FLAG)
        if is_superscript:
            raised = to_superscript(decoded)
            if parts:
                parts[-1] = f"{parts[-1]}{raised}"
            else:
                parts.append(raised)
            previous = span
            continue
        if previous is not None and _needs_word_space(previous, span):
            parts.append(" ")
        parts.append(decoded)
        previous = span
    return "".join(parts)


def _needs_word_space(previous: PdfTextSpan, current: PdfTextSpan) -> bool:
    if previous.text.endswith((" ", "\t")) or current.text.startswith((" ", "\t")):
        return False
    gap = current.bbox[0] - previous.bbox[2]
    size = current.size if current.size > 0 else previous.size
    return gap > size * _WORD_GAP_RATIO


def _parse_differences_array(body: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    code = 0
    for match in _DIFF_TOKEN_RE.finditer(body):
        name = match.group(1)
        number = match.group(2)
        if number is not None:
            code = int(number)
            continue
        if name is not None:
            mapping[code] = name
            code += 1
    return mapping


def _encoding_differences(document: _PdfDocument, font_xref: int) -> dict[int, str] | None:
    kind, value = document.xref_get_key(font_xref, "Encoding")
    if kind in {"null", "name"}:
        # WinAnsiEncoding y similares: fuente de texto normal.
        return None
    raw = value
    if kind == "xref":
        enc_xref = int(value.split()[0])
        raw = document.xref_object(enc_xref)
    match = _DIFFERENCES_RE.search(raw)
    if match is None:
        return None
    return _parse_differences_array(match.group(1))


def _font_differences_by_name(document: _PdfDocument, page: _PdfPage) -> dict[str, dict[int, str]]:
    by_name: dict[str, dict[int, str]] = {}
    for entry in page.get_fonts():
        xref = int(cast(int, entry[0]))
        basefont = str(entry[3])
        resource_name = str(entry[4])
        differences = _encoding_differences(document, xref)
        if differences is None:
            continue
        by_name[basefont] = differences
        by_name[resource_name] = differences
    return by_name


def _span_from_dict(raw: Mapping[str, object]) -> PdfTextSpan | None:
    text = raw.get("text")
    if not isinstance(text, str) or not text:
        return None
    font = raw.get("font")
    flags = raw.get("flags")
    bbox = raw.get("bbox")
    size = raw.get("size")
    if not isinstance(font, str) or not isinstance(flags, int):
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    font_size = float(size) if isinstance(size, (int, float)) else 11.0
    box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return PdfTextSpan(text=text, font=font, flags=flags, bbox=box, size=font_size)


def _extract_page_text(document: _PdfDocument, page: _PdfPage) -> str:
    payload = page.get_text("dict")
    if not isinstance(payload, dict):
        return ""
    font_differences = _font_differences_by_name(document, page)
    lines_out: list[str] = []
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != 0:
            continue
        lines = block.get("lines")
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict):
                continue
            raw_spans = line.get("spans")
            if not isinstance(raw_spans, list):
                continue
            spans = [
                span
                for raw in raw_spans
                if isinstance(raw, dict)
                for span in (_span_from_dict(raw),)
                if span is not None
            ]
            reconstructed = reconstruct_line(spans, font_differences)
            if reconstructed:
                lines_out.append(reconstructed)
    return "\n".join(lines_out)


class PdfParser:
    """Extrae el texto de cada página. No aplica OCR ni abre PDF con contraseña."""

    def __init__(self, mime_type: str) -> None:
        self._mime_type = mime_type

    def parse(self, name: str, content: bytes) -> ExtractedDocument:
        try:
            document = _open_pdf(content)
        except Exception as error:
            message = "No se pudo leer el PDF"
            raise DocumentParseError(message) from error
        try:
            if document.needs_pass:
                message = "El PDF protegido con contraseña no está admitido"
                raise DocumentParseError(message)
            pages = tuple(
                ExtractedPage(
                    number=index,
                    text=_extract_page_text(document, page),
                    section=None,
                )
                for index, page in enumerate(document, start=1)
            )
        except DocumentParseError:
            raise
        except Exception as error:
            message = "No se pudo leer el PDF"
            raise DocumentParseError(message) from error
        finally:
            document.close()
        return ExtractedDocument(
            name=name,
            mime_type=self._mime_type,
            pages=pages,
            parser_version=PDF_PARSER_VERSION,
        )
