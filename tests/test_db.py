"""Tests for the small conversation memory."""

from __future__ import annotations

from app.db import ConversationStore, StudyPageStore, FileStore


def test_file_store_round_trips_bytes(tmp_path):
    store = FileStore(tmp_path / "f.sqlite")
    store.save("abc", "notes.pdf", "application/pdf", b"PDFBYTES")
    got = store.get("abc")
    assert got == ("notes.pdf", "application/pdf", b"PDFBYTES")
    assert store.get("missing") is None


def test_conversation_clear_empties_history(tmp_path):
    store = ConversationStore(tmp_path / "c.sqlite")
    store.save("user", "hi")
    store.save("assistant", "hey")
    assert store.recent() != []
    store.clear()
    assert store.recent() == []


def test_study_page_clear_removes_pages(tmp_path):
    store = StudyPageStore(tmp_path / "s.sqlite")
    store.save("p1", "T", "<html>x</html>")
    assert store.get("p1") is not None
    store.clear()
    assert store.get("p1") is None


def test_study_page_stores_and_returns_meta(tmp_path):
    store = StudyPageStore(tmp_path / "s.sqlite")
    meta = '{"kind": "exam", "course": "STAT 311", "format": "multiple_choice"}'
    store.save("p1", "Quiz", "<html>x</html>", meta)
    assert store.get_meta("p1") == meta
    assert store.get_meta("missing") is None


def test_study_page_meta_defaults_to_empty(tmp_path):
    store = StudyPageStore(tmp_path / "s.sqlite")
    store.save("p1", "T", "<html>x</html>")  # no meta passed
    assert store.get_meta("p1") == ""


def test_recent_returns_turns_oldest_first(tmp_path):
    store = ConversationStore(tmp_path / "c.sqlite")
    store.save("user", "what's due?")
    store.save("assistant", "hw4 for 163")
    store.save("user", "what's that asking for?")

    turns = store.recent(limit=10)
    assert turns == [
        {"role": "user", "content": "what's due?"},
        {"role": "assistant", "content": "hw4 for 163"},
        {"role": "user", "content": "what's that asking for?"},
    ]


def test_recent_caps_to_the_last_n(tmp_path):
    store = ConversationStore(tmp_path / "c.sqlite")
    for i in range(10):
        store.save("user", f"msg {i}")

    turns = store.recent(limit=3)
    assert [t["content"] for t in turns] == ["msg 7", "msg 8", "msg 9"]


def test_survives_reopen(tmp_path):
    path = tmp_path / "c.sqlite"
    ConversationStore(path).save("user", "remember me")
    reopened = ConversationStore(path)
    assert reopened.recent()[-1]["content"] == "remember me"
