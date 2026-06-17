"""Make a downloadable Word (.docx) document from text and send it to the student.

The brain writes the content; this turns it into a real .docx (openable/editable in
Word or Google Docs), sends it as a downloadable link, and drops a copy in OneDrive.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from xml.sax.saxutils import escape

from fpdf import FPDF
from fpdf.enums import XPos, YPos

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

DOCUMENT_TOOLS = [
    {
        "name": "make_document",
        "description": "Create a downloadable Word (.docx) document from text and SEND it to the "
        "student (also saved to their files folder). Use whenever they ask you to write "
        "something up as a file or document they can download — a study guide, summary, notes, "
        "outline, cheat sheet, essay blueprint, etc. You write the full content yourself in "
        "'content'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title / filename, no extension (e.g. 'STAT 311 Study Guide').",
                },
                "content": {
                    "type": "string",
                    "description": "The full document text. Plain text; blank lines between paragraphs.",
                },
            },
            "required": ["title", "content"],
        },
    }
]

# fpdf2 core fonts are latin-1; map common unicode so content doesn't get mangled.
_UNICODE = {
    "—": "-", "–": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "…": "...", "•": "-",
    " ": " ", "→": "->", "←": "<-",
}


def _latin1(text: str) -> str:
    for k, v in _UNICODE.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(title: str, content: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, _latin1(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 12)
    for line in content.split("\n"):
        pdf.multi_cell(0, 7, _latin1(line) or " ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


# --- Word .docx (a minimal, valid OOXML package, stdlib only) ----------------

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _paragraph(text: str, bold: bool = False, size: int | None = None) -> str:
    rpr = ""
    if bold or size:
        rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + (
            f'<w:sz w:val="{size}"/>' if size else ""
        ) + "</w:rPr>"
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def render_docx(title: str, content: str) -> bytes:
    """A real Word .docx: bold title, then one paragraph per line of content."""
    paras = [_paragraph(title, bold=True, size=32)]
    paras += [_paragraph(line) for line in content.split("\n")]
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(paras)}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _safe_name(title: str) -> str:
    name = re.sub(r"[^\w\- ]", "", title).strip() or "document"
    return f"{name}.docx"


class DocumentService:
    def __init__(self, sms, files, public_base_url: str, onedrive=None):
        self.sms = sms
        self.files = files
        self.public_base_url = public_base_url.rstrip("/")
        self.onedrive = onedrive

    def tool_names(self) -> list[str]:
        return [t["name"] for t in DOCUMENT_TOOLS]

    def schemas(self) -> list[dict]:
        return list(DOCUMENT_TOOLS)

    def dispatch(self, name: str, tool_input: dict) -> str:
        if name != "make_document":
            return f"(unknown document tool: {name})"
        title = (tool_input.get("title") or "document").strip()
        content = tool_input.get("content") or ""
        data = render_docx(title, content)
        filename = _safe_name(title)
        file_id = uuid.uuid4().hex
        self.files.save(file_id, filename, DOCX_MIME, data)
        # Also drop a copy in their OneDrive folder (so it's on the laptop too).
        if self.onedrive is not None:
            try:
                self.onedrive.upload(filename, data, DOCX_MIME)
            except Exception:
                pass
        self.sms.send(
            f"here's {title}:", media_url=[f"{self.public_base_url}/file/{file_id}"]
        )
        return f"created and sent {filename} ({len(data)} bytes) to them."
