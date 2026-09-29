"""Reconstrucción de glifos matemáticos y superíndices en el parser PDF."""

from app.documents.parsers.pdf import (
    PdfTextSpan,
    decode_span_text,
    reconstruct_line,
    unicode_for_glyph,
)
from app.documents.parsers.registry import PDF_MIME, parse_document


def test_code_51_named_multiply_returns_times() -> None:
    assert unicode_for_glyph(51, "multiply") == "\u00d7"


def test_code_53_named_equal_returns_equals() -> None:
    assert unicode_for_glyph(53, "equal") == "="


def test_normal_font_span_keeps_digit_three() -> None:
    assert decode_span_text("3", differences=None, font="TimesNewRomanMTStd") == "3"
    assert decode_span_text("3", differences={}, font="Times") == "3"


def test_wwpi01_digit_names_are_the_drawn_signs() -> None:
    lying = {51: "three", 53: "five"}
    assert decode_span_text("3", lying, font="WWPI01") == "\u00d7"
    assert decode_span_text("5", lying, font="EHEICN+WWPI01") == "="
    assert decode_span_text("3", lying, font="WWPI01-SC700") == "\u00d7"
    assert decode_span_text("3", lying, font="TimesNewRomanMTStd") == "3"


def test_superscript_t_after_q_returns_q_transpose() -> None:
    spans = [
        PdfTextSpan(text="Q", font="Times", flags=0, bbox=(0.0, 0.0, 8.0, 10.0), size=10.0),
        PdfTextSpan(text="T", font="Times", flags=1, bbox=(8.0, -2.0, 14.0, 6.0), size=7.0),
    ]
    assert reconstruct_line(spans, font_differences={}) == "Q\u1d40"


def test_math_differences_remap_codes_without_guessing_context() -> None:
    differences = {51: "multiply", 53: "equal"}
    assert decode_span_text("3", differences) == "\u00d7"
    assert decode_span_text("5", differences) == "="
    # Un 3 de fuente normal (sin Differences) no se reescribe por contexto.
    assert decode_span_text("n3n", differences=None) == "n3n"


def test_adjacent_math_spans_do_not_insert_space() -> None:
    differences = {"CMSY10": {51: "multiply"}}
    spans = [
        PdfTextSpan(text="n", font="Times", flags=0, bbox=(0.0, 0.0, 6.0, 10.0), size=10.0),
        PdfTextSpan(text="3", font="CMSY10", flags=0, bbox=(6.2, 0.0, 12.0, 10.0), size=10.0),
        PdfTextSpan(text="n", font="Times", flags=0, bbox=(12.2, 0.0, 18.0, 10.0), size=10.0),
    ]
    assert reconstruct_line(spans, font_differences=differences) == "n\u00d7n"


def test_real_word_space_is_preserved() -> None:
    spans = [
        PdfTextSpan(text="hola", font="Helvetica", flags=0, bbox=(0.0, 0.0, 24.0, 10.0), size=10.0),
        PdfTextSpan(
            text="pdf",
            font="Helvetica",
            flags=0,
            bbox=(30.0, 0.0, 48.0, 10.0),
            size=10.0,
        ),
    ]
    assert reconstruct_line(spans, font_differences={}) == "hola pdf"


def test_unknown_glyph_name_keeps_pymupdf_character() -> None:
    assert decode_span_text("3", differences={51: "notInOurTable"}) == "3"


def _pdf_with_bad_tounicode_and_math_differences() -> bytes:
    """PDF cuyo ToUnicode miente (3/=) pero Differences dice multiply/equal."""
    tounicode = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<33> <0033>
<35> <0035>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""
    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets = [0]

    def add_obj(number: int, body: bytes) -> None:
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    )
    content = b"BT /F1 12 Tf 50 100 Td (\x33\x35) Tj ET"
    add_obj(4, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    add_obj(
        5,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /CMSY10"
        b" /Encoding << /Type /Encoding /Differences [51 /multiply 53 /equal] >>"
        b" /ToUnicode 6 0 R >>",
    )
    add_obj(6, f"<< /Length {len(tounicode)} >>\nstream\n".encode() + tounicode + b"\nendstream")
    xref_pos = sum(len(part) for part in parts)
    parts.append(b"xref\n0 7\n")
    parts.append(b"0000000000 65535 f \n")
    for index in range(1, 7):
        parts.append(f"{offsets[index]:010d} 00000 n \n".encode())
    parts.append(f"trailer<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return b"".join(parts)


def test_generated_pdf_remaps_multiply_and_equal_via_differences() -> None:
    document = parse_document("math.pdf", _pdf_with_bad_tounicode_and_math_differences(), PDF_MIME)
    assert document.parser_version == "parser-v2"
    assert "\u00d7" in document.pages[0].text
    assert "=" in document.pages[0].text
    assert "3" not in document.pages[0].text
    assert "5" not in document.pages[0].text
