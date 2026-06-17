"""Turn user-uploaded files into Claude content blocks.

The web chat lets the student attach pictures and documents. This module maps
each uploaded file to the right Anthropic content:

  - images (png/jpg/gif/webp)  -> an "image" block (Claude sees it)
  - PDFs                        -> a "document" block (Claude reads it natively)
  - Word/txt/html              -> extracted plain text, folded into the question
  - anything else              -> noted as unreadable (never silently dropped)

`build_user_content(text, files)` returns either a plain string (no files) or a
list of content blocks ready to drop into a user message.
"""

from __future__ import annotations

import base64
import html as html_module
import io
import re
import zipfile

from app.canvas import html_to_text

# File extension / content-type -> image media type Claude accepts.
_IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

# How much extracted document text to carry (keeps the prompt bounded).
_MAX_DOC_CHARS = 6000


def _ext(filename: str) -> str:
    return (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def _image_media_type(filename: str, content_type: str) -> str | None:
    ct = (content_type or "").lower()
    for key, media in _IMAGE_TYPES.items():
        if ct == media:
            return media
    return _IMAGE_TYPES.get(_ext(filename))


def _is_pdf(filename: str, content_type: str) -> bool:
    return _ext(filename) == "pdf" or "pdf" in (content_type or "").lower()


def extract_text(filename: str, content_type: str, data: bytes) -> str:
    """Pull readable text out of a document (txt / docx / pdf / html). Empty if none."""
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    if name.endswith(".txt") or "text/plain" in ct:
        return data.decode("utf-8", "replace")
    if name.endswith(".docx"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            xml = z.read("word/document.xml").decode("utf-8", "replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S)
            return html_module.unescape("".join(parts))
        except Exception:
            return ""
    if name.endswith(".pdf") or "pdf" in ct:
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            return ""
    if name.endswith((".html", ".htm")) or "html" in ct:
        return html_to_text(data.decode("utf-8", "replace"))
    return ""


def to_content_blocks(files):
    """Map uploaded files to (media_blocks, extracted_texts, unsupported_names).

    `files` is an iterable of (filename, content_type, data: bytes).
    """
    media: list[dict] = []
    extracted: list[tuple[str, str]] = []
    unsupported: list[str] = []

    for filename, content_type, data in files:
        image_media = _image_media_type(filename, content_type)
        if image_media:
            media.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
            continue
        if _is_pdf(filename, content_type):
            media.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
            continue
        text = extract_text(filename, content_type, data)
        if text.strip():
            extracted.append((filename, text))
        else:
            unsupported.append(filename)

    return media, extracted, unsupported


def build_user_content(text: str, files):
    """Build the user message content for Claude from the typed text + uploads.

    Returns a plain string when there are no files (cheapest path), otherwise a
    list of content blocks: media first, then a single text block carrying the
    question plus any extracted document text.
    """
    if not files:
        return text

    media, extracted, unsupported = to_content_blocks(files)

    parts: list[str] = []
    if text.strip():
        parts.append(text)
    for name, doc in extracted:
        parts.append(f"[Attached file '{name}']\n{doc[:_MAX_DOC_CHARS]}")
    if unsupported:
        parts.append("[Attached but couldn't be read: " + ", ".join(unsupported) + "]")

    combined = "\n\n".join(parts) if parts else "(see attached)"
    return media + [{"type": "text", "text": combined}]
