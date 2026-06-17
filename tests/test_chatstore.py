"""Tests for multi-conversation storage (ChatGPT-style chats)."""

from __future__ import annotations

from app.db import ChatStore


def test_create_and_list_chats_newest_first(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    a = s.create_chat()
    b = s.create_chat()
    chats = s.list_chats()
    assert [c["id"] for c in chats] == [b, a]          # newest first
    assert all("title" in c for c in chats)


def test_append_and_read_messages_scoped_to_a_chat(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c1 = s.create_chat()
    c2 = s.create_chat()
    s.append(c1, "user", "hi in one")
    s.append(c2, "user", "hi in two")
    assert [m["text"] for m in s.since(c1, 0)] == ["hi in one"]
    assert [m["text"] for m in s.since(c2, 0)] == ["hi in two"]


def test_first_user_message_auto_titles_the_chat(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    assert s.title_of(c) == "New chat"
    s.append(c, "user", "what's due in STAT 311 this week?")
    assert s.title_of(c) == "what's due in STAT 311 this week?"
    # A later message does not overwrite the title.
    s.append(c, "user", "and my grades?")
    assert s.title_of(c) == "what's due in STAT 311 this week?"


def test_long_first_message_title_is_truncated(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    s.append(c, "user", "x" * 200)
    assert len(s.title_of(c)) <= 60


def test_rename_chat(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    s.rename(c, "Midterm prep")
    assert s.title_of(c) == "Midterm prep"


def test_delete_removes_chat_and_its_messages(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    s.append(c, "user", "hi")
    s.delete(c)
    assert s.title_of(c) is None
    assert s.since(c, 0) == []
    assert all(x["id"] != c for x in s.list_chats())


def test_clear_keeps_chat_but_empties_messages(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    s.append(c, "user", "hi")
    s.clear(c)
    assert s.since(c, 0) == []
    assert s.title_of(c) is not None  # chat still exists


def test_recent_for_brain_returns_role_content_oldest_first(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    c = s.create_chat()
    s.append(c, "user", "what's due?")
    s.append(c, "assistant", "hw4")
    hist = s.recent_for_brain(c, limit=10)
    assert hist == [
        {"role": "user", "content": "what's due?"},
        {"role": "assistant", "content": "hw4"},
    ]


def test_ensure_chat_creates_one_when_empty_else_returns_existing(tmp_path):
    s = ChatStore(tmp_path / "chats.sqlite")
    first = s.ensure_chat()
    assert first is not None
    again = s.ensure_chat()
    assert again == first  # didn't create a second one
