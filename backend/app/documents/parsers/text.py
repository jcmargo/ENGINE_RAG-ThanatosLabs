"""Parser UTF-8 para TXT y Markdown."""

from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.parsers.base import DocumentParseError
from app.documents.versions import PARSER_VERSION


class TextParser:
    """Lee texto UTF-8 y lo deja en una sola página."""

    def __init__(self, mime_type: str) -> None:
        self._mime_type = mime_type

    def parse(self, name: str, content: bytes) -> ExtractedDocument:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            message = "El texto debe estar en UTF-8"
            raise DocumentParseError(message) from error
        page = ExtractedPage(number=1, text=text, section=None)
        return ExtractedDocument(
            name=name,
            mime_type=self._mime_type,
            pages=(page,),
            parser_version=PARSER_VERSION,
        )
