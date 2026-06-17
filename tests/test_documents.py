"""Tests for the make-a-document (Word .docx) tool."""

from __future__ import annotations

import io
import zipfile

from app.documents import DocumentService, render_docx, render_pdf
from app.db import FileStore

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FakeSms:
    def __init__(self):
        self.sent = []

    def send(self, text, to=None, media_url=None):
        self.sent.append((text, media_url))


class FakeOneDrive:
    def __init__(self):
        self.uploaded = []

    def upload(self, name, data, content_type):
        self.uploaded.append((name, content_type, len(data)))


def test_render_pdf_returns_pdf_bytes_and_handles_unicode():
    data = render_pdf("My Title", "first line\n\nsecond — with “smart” quotes and …")
    assert data[:4] == b"%PDF"
    assert len(data) > 200


def test_render_docx_is_a_valid_word_zip_with_the_text():
    data = render_docx("My Title", "Hello world\n\nSecond paragraph")
    assert data[:2] == b"PK"  # zip magic
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names
    doc = z.read("word/document.xml").decode("utf-8")
    assert "My Title" in doc
    assert "Hello world" in doc
    assert "Second paragraph" in doc


def test_render_docx_escapes_xml_special_characters():
    data = render_docx("T", "a < b & c > d")
    doc = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
    assert "&lt;" in doc and "&amp;" in doc and "&gt;" in doc


def test_make_document_sends_downloadable_docx_and_copies_to_onedrive(tmp_path):
    sms = FakeSms()
    files = FileStore(tmp_path / "f.sqlite")
    od = FakeOneDrive()
    svc = DocumentService(sms=sms, files=files, public_base_url="https://app.example", onedrive=od)

    out = svc.dispatch("make_document", {
        "title": "STAT 311 Study Guide",
        "content": "point one\npoint two",
    })
    assert "sent" in out.lower()

    # Sent as a media link.
    text, media_url = sms.sent[0]
    assert "STAT 311 Study Guide" in text
    assert media_url[0].startswith("https://app.example/file/")

    # Stored as a servable Word document.
    fid = media_url[0].rsplit("/", 1)[1]
    filename, ctype, data = files.get(fid)
    assert filename == "STAT 311 Study Guide.docx"
    assert ctype == DOCX_MIME
    assert data[:2] == b"PK"

    # Also copied into the OneDrive folder.
    assert od.uploaded and od.uploaded[0][0] == "STAT 311 Study Guide.docx"


def test_make_document_works_without_onedrive(tmp_path):
    sms = FakeSms()
    svc = DocumentService(sms=sms, files=FileStore(tmp_path / "f.sqlite"),
                          public_base_url="https://app.example", onedrive=None)
    svc.dispatch("make_document", {"title": "Notes", "content": "stuff"})
    assert sms.sent and sms.sent[0][1][0].startswith("https://app.example/file/")
