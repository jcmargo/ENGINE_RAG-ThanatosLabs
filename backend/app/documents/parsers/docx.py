"""Parser de DOCX mediante python-docx."""

from io import BytesIO

from docx import Document

from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.parsers.base import DocumentParseError
from app.documents.versions import PARSER_VERSION


class DocxParser:
    """Lee párrafos de un DOCX y los conserva como una página."""

    def __init__(self, mime_type: str) -> None:
        self._mime_type = mime_type

    def parse(self, name: str, content: bytes) -> ExtractedDocument:
        try:
            document = Document(BytesIO(content))
            paragraphs = [
                paragraph.text.strip()
                for paragraph in document.paragraphs
                if paragraph.text.strip()
            ]
        except Exception as error:
            message = "No se pudo leer el DOCX"
            raise DocumentParseError(message) from error
        page = ExtractedPage(number=1, text="\n\n".join(paragraphs), section=None)
        return ExtractedDocument(
            name=name,
            mime_type=self._mime_type,
            pages=(page,),
            parser_version=PARSER_VERSION,
        )
