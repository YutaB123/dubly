"""Tests for turning uploaded files into Claude content blocks."""

from __future__ import annotations

import base64

from app import attachments


def test_image_becomes_an_image_block_with_base64_data():
    data = b"\x89PNG fake-bytes"
    blocks, extracted, unsupported = attachments.to_content_blocks(
        [("photo.png", "image/png", data)]
    )
    assert len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "image"
    assert b["source"]["type"] == "base64"
    assert b["source"]["media_type"] == "image/png"
    assert base64.b64decode(b["source"]["data"]) == data
    assert extracted == []
    assert unsupported == []


def test_jpeg_extension_classifies_as_image_even_without_content_type():
    blocks, _, _ = attachments.to_content_blocks([("pic.jpeg", "", b"xx")])
    assert blocks[0]["source"]["media_type"] == "image/jpeg"


def test_pdf_becomes_a_document_block():
    blocks, _, _ = attachments.to_content_blocks(
        [("hw.pdf", "application/pdf", b"%PDF-1.4 fake")]
    )
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"


def test_txt_is_extracted_as_text_not_a_block():
    blocks, extracted, unsupported = attachments.to_content_blocks(
        [("notes.txt", "text/plain", b"hello world")]
    )
    assert blocks == []
    assert extracted == [("notes.txt", "hello world")]
    assert unsupported == []


def test_unreadable_binary_is_marked_unsupported():
    blocks, extracted, unsupported = attachments.to_content_blocks(
        [("archive.zip", "application/zip", b"PK\x03\x04")]
    )
    assert blocks == []
    assert extracted == []
    assert unsupported == ["archive.zip"]


def test_build_user_content_with_no_files_returns_plain_string():
    assert attachments.build_user_content("hi there", []) == "hi there"


def test_build_user_content_puts_media_first_and_text_last():
    content = attachments.build_user_content(
        "what is this?", [("p.png", "image/png", b"x")]
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    assert content[-1]["type"] == "text"
    assert "what is this?" in content[-1]["text"]


def test_build_user_content_folds_extracted_file_text_into_the_text_block():
    content = attachments.build_user_content(
        "summarize this", [("essay.txt", "text/plain", b"my essay body")]
    )
    assert isinstance(content, list)
    assert content[-1]["type"] == "text"
    assert "summarize this" in content[-1]["text"]
    assert "my essay body" in content[-1]["text"]
    assert "essay.txt" in content[-1]["text"]
