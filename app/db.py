"""A small local memory: the recent conversation, and generated study pages.

Plain SQLite via the standard library. Single user (the whitelist guarantees
that), so no per-user keys are needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class ConversationStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                role    TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def save(self, role: str, content: str) -> None:
        self._db.execute(
            "INSERT INTO conversation (role, content) VALUES (?, ?)",
            (role, content),
        )
        self._db.commit()

    def recent(self, limit: int = 12) -> list[dict]:
        rows = self._db.execute(
            "SELECT role, content FROM conversation ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        # Pulled newest-first for the LIMIT; hand back oldest-first.
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def clear(self) -> None:
        """Forget the whole conversation."""
        self._db.execute("DELETE FROM conversation")
        self._db.commit()


class StudyPageStore:
    """Stores generated flashcard / exam pages, served later at /study/{id}."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS study_page (
                id    TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                html  TEXT NOT NULL,
                meta  TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Add the meta column to databases created before regeneration existed.
        try:
            self._db.execute("ALTER TABLE study_page ADD COLUMN meta TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._db.commit()

    def save(self, page_id: str, title: str, html: str, meta: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO study_page (id, title, html, meta) VALUES (?, ?, ?, ?)",
            (page_id, title, html, meta),
        )
        self._db.commit()

    def get(self, page_id: str) -> str | None:
        row = self._db.execute(
            "SELECT html FROM study_page WHERE id = ?", (page_id,)
        ).fetchone()
        return row[0] if row else None

    def get_meta(self, page_id: str) -> str | None:
        """The JSON recipe used to build the page (for regeneration); None if no page."""
        row = self._db.execute(
            "SELECT meta FROM study_page WHERE id = ?", (page_id,)
        ).fetchone()
        return row[0] if row else None

    def clear(self) -> None:
        """Delete all generated study pages."""
        self._db.execute("DELETE FROM study_page")
        self._db.commit()


class WebChatStore:
    """The visible transcript for the web chat app — what the browser shows.

    Every message (yours and the assistant's, including document links and
    reminders) lands here with an incrementing id, so the page can load the
    history and poll for anything new (like a reminder firing later).
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS web_chat (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role      TEXT NOT NULL,
                text      TEXT NOT NULL,
                media_url TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._db.commit()

    def append(self, role: str, text: str, media_url: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO web_chat (role, text, media_url) VALUES (?, ?, ?)",
            (role, text, media_url),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def since(self, after_id: int = 0) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, role, text, media_url FROM web_chat WHERE id > ? ORDER BY id",
            (after_id,),
        ).fetchall()
        return [
            {"id": i, "role": r, "text": t, "media_url": m} for i, r, t, m in rows
        ]

    def max_id(self) -> int:
        row = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM web_chat").fetchone()
        return int(row[0])

    def clear(self) -> None:
        self._db.execute("DELETE FROM web_chat")
        self._db.commit()


class ChatStore:
    """Multiple named conversations (ChatGPT-style), each with its own messages.

    The web app uses this instead of a single transcript: the message list IS the
    brain's memory for that chat, so switching chats switches context too.
    """

    _TITLE_MAX = 60

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS chat ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title TEXT NOT NULL DEFAULT 'New chat')"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS message ("
            " mid INTEGER PRIMARY KEY AUTOINCREMENT,"
            " chat_id INTEGER NOT NULL,"
            " role TEXT NOT NULL,"
            " text TEXT NOT NULL,"
            " media_url TEXT NOT NULL DEFAULT '')"
        )
        self._db.commit()

    # --- chats ---------------------------------------------------------------

    def create_chat(self, title: str = "New chat") -> int:
        cur = self._db.execute("INSERT INTO chat (title) VALUES (?)", (title,))
        self._db.commit()
        return int(cur.lastrowid)

    def list_chats(self) -> list[dict]:
        rows = self._db.execute("SELECT id, title FROM chat ORDER BY id DESC").fetchall()
        return [{"id": i, "title": t} for i, t in rows]

    def title_of(self, chat_id: int) -> str | None:
        row = self._db.execute("SELECT title FROM chat WHERE id = ?", (chat_id,)).fetchone()
        return row[0] if row else None

    def rename(self, chat_id: int, title: str) -> None:
        title = (title or "").strip()[: self._TITLE_MAX] or "New chat"
        self._db.execute("UPDATE chat SET title = ? WHERE id = ?", (title, chat_id))
        self._db.commit()

    def delete(self, chat_id: int) -> None:
        self._db.execute("DELETE FROM message WHERE chat_id = ?", (chat_id,))
        self._db.execute("DELETE FROM chat WHERE id = ?", (chat_id,))
        self._db.commit()

    def ensure_chat(self) -> int:
        """Return the most recent chat id, creating one if there are none."""
        row = self._db.execute("SELECT id FROM chat ORDER BY id DESC LIMIT 1").fetchone()
        return int(row[0]) if row else self.create_chat()

    # --- messages ------------------------------------------------------------

    def append(self, chat_id: int, role: str, text: str, media_url: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO message (chat_id, role, text, media_url) VALUES (?, ?, ?, ?)",
            (chat_id, role, text, media_url),
        )
        # Auto-title a fresh chat from its first user message.
        if role == "user" and self.title_of(chat_id) == "New chat":
            title = " ".join((text or "").split())[: self._TITLE_MAX].strip()
            if title:
                self._db.execute(
                    "UPDATE chat SET title = ? WHERE id = ?", (title, chat_id)
                )
        self._db.commit()
        return int(cur.lastrowid)

    def since(self, chat_id: int, after_id: int = 0) -> list[dict]:
        rows = self._db.execute(
            "SELECT mid, role, text, media_url FROM message "
            "WHERE chat_id = ? AND mid > ? ORDER BY mid",
            (chat_id, after_id),
        ).fetchall()
        return [{"id": i, "role": r, "text": t, "media_url": m} for i, r, t, m in rows]

    def max_id(self, chat_id: int) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(mid), 0) FROM message WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return int(row[0])

    def clear(self, chat_id: int) -> None:
        self._db.execute("DELETE FROM message WHERE chat_id = ?", (chat_id,))
        self._db.commit()

    def search(self, query: str, limit: int = 40) -> list[dict]:
        """Chats whose title or any message matches `query` (newest first), with a snippet."""
        q = f"%{(query or '').lower()}%"
        rows = self._db.execute(
            "SELECT c.id, c.title, "
            "  (SELECT m.text FROM message m WHERE m.chat_id = c.id AND lower(m.text) LIKE ? "
            "   ORDER BY m.mid LIMIT 1) AS snip "
            "FROM chat c "
            "WHERE lower(c.title) LIKE ? "
            "   OR EXISTS (SELECT 1 FROM message m2 WHERE m2.chat_id = c.id AND lower(m2.text) LIKE ?) "
            "ORDER BY c.id DESC LIMIT ?",
            (q, q, q, limit),
        ).fetchall()
        return [{"id": i, "title": t, "snippet": (s or "")[:90]} for i, t, s in rows]

    def recent_for_brain(self, chat_id: int, limit: int = 12) -> list[dict]:
        rows = self._db.execute(
            "SELECT role, text FROM message WHERE chat_id = ? ORDER BY mid DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [{"role": r, "content": t} for r, t in reversed(rows)]


class StudyProgressStore:
    """Per-page study state: flashcard boxes (Leitner spaced repetition) + quiz attempts."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS card_box ("
            " page_id TEXT, card INTEGER, box INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (page_id, card))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS quiz_attempt ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, page_id TEXT NOT NULL,"
            " score INTEGER, total INTEGER, missed INTEGER)"
        )
        self._db.commit()

    # --- flashcards (spaced repetition) --------------------------------------

    def get_boxes(self, page_id: str) -> dict:
        rows = self._db.execute(
            "SELECT card, box FROM card_box WHERE page_id = ?", (page_id,)
        ).fetchall()
        return {int(c): int(b) for c, b in rows}

    def rate_card(self, page_id: str, card: int, knew: bool) -> int:
        """Known -> box +1 (capped at 5, longer interval). Missed -> back to box 0."""
        row = self._db.execute(
            "SELECT box FROM card_box WHERE page_id = ? AND card = ?", (page_id, card)
        ).fetchone()
        box = row[0] if row else 0
        box = min(box + 1, 5) if knew else 0
        self._db.execute(
            "INSERT OR REPLACE INTO card_box (page_id, card, box) VALUES (?, ?, ?)",
            (page_id, card, box),
        )
        self._db.commit()
        return box

    # --- quiz attempts -------------------------------------------------------

    def add_attempt(self, page_id: str, score: int, total: int, missed: int) -> None:
        self._db.execute(
            "INSERT INTO quiz_attempt (page_id, score, total, missed) VALUES (?, ?, ?, ?)",
            (page_id, score, total, missed),
        )
        self._db.commit()

    def attempts(self, page_id: str) -> dict:
        rows = self._db.execute(
            "SELECT score, total, missed FROM quiz_attempt WHERE page_id = ? ORDER BY id",
            (page_id,),
        ).fetchall()
        best = max((s for s, _, _ in rows), default=0)
        return {
            "count": len(rows),
            "best": best,
            "list": [{"score": s, "total": t, "missed": m} for s, t, m in rows],
        }


class AlertStore:
    """Remembers what we've already alerted on, so proactive pushes don't repeat."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS grade_seen (course TEXT PRIMARY KEY, score REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS due_alerted (ref TEXT PRIMARY KEY)")
        self._db.commit()

    def grade_for(self, course: str):
        row = self._db.execute("SELECT score FROM grade_seen WHERE course = ?", (course,)).fetchone()
        return row[0] if row else None

    def set_grade(self, course: str, score: float) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO grade_seen (course, score) VALUES (?, ?)", (course, score)
        )
        self._db.commit()

    def was_due_alerted(self, ref: str) -> bool:
        return self._db.execute("SELECT 1 FROM due_alerted WHERE ref = ?", (ref,)).fetchone() is not None

    def mark_due_alerted(self, ref: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO due_alerted (ref) VALUES (?)", (ref,))
        self._db.commit()


class PushStore:
    """Browser push subscriptions, so the app can notify you when it's closed."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS push_sub (
                endpoint TEXT PRIMARY KEY,
                p256dh   TEXT NOT NULL,
                auth     TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def save(self, subscription: dict) -> None:
        keys = subscription.get("keys", {})
        self._db.execute(
            "INSERT OR REPLACE INTO push_sub (endpoint, p256dh, auth) VALUES (?, ?, ?)",
            (subscription.get("endpoint", ""), keys.get("p256dh", ""), keys.get("auth", "")),
        )
        self._db.commit()

    def all(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT endpoint, p256dh, auth FROM push_sub"
        ).fetchall()
        return [
            {"endpoint": e, "keys": {"p256dh": p, "auth": a}} for e, p, a in rows
        ]

    def remove(self, endpoint: str) -> None:
        self._db.execute("DELETE FROM push_sub WHERE endpoint = ?", (endpoint,))
        self._db.commit()

    def clear(self) -> None:
        self._db.execute("DELETE FROM push_sub")
        self._db.commit()


class FileStore:
    """Temporarily holds a file's bytes so it can be fetched at a public URL
    (Twilio needs a reachable media_url; the web app links to the same route)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS outbound_file (
                id           TEXT PRIMARY KEY,
                filename     TEXT NOT NULL,
                content_type TEXT NOT NULL,
                data         BLOB NOT NULL
            )
            """
        )
        self._db.commit()

    def save(self, file_id: str, filename: str, content_type: str, data: bytes) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO outbound_file (id, filename, content_type, data) "
            "VALUES (?, ?, ?, ?)",
            (file_id, filename, content_type, data),
        )
        self._db.commit()

    def get(self, file_id: str) -> tuple[str, str, bytes] | None:
        row = self._db.execute(
            "SELECT filename, content_type, data FROM outbound_file WHERE id = ?",
            (file_id,),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None


class LectureStore:
    """Saved lecture transcripts (from a pasted/uploaded transcript or a
    Whisper-transcribed recording) so the student can ask about them and make
    study material from them. One lecture fits Claude's context, so the full
    transcript is stored and handed back whole — no chunking/embeddings."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS lecture (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                transcript TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'transcript',
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._db.commit()

    def save(self, lecture_id: str, title: str, transcript: str,
             source: str = "transcript", created_at: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO lecture (id, title, transcript, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lecture_id, title, transcript, source, created_at),
        )
        self._db.commit()

    def get(self, lecture_id: str) -> tuple[str, str] | None:
        """(title, transcript) for an exact id, or None."""
        row = self._db.execute(
            "SELECT title, transcript FROM lecture WHERE id = ?", (lecture_id,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def find_by_title(self, query: str) -> tuple[str, str, str] | None:
        """Newest lecture whose title loosely matches `query` -> (id, title, transcript)."""
        q = f"%{(query or '').strip().lower()}%"
        row = self._db.execute(
            "SELECT id, title, transcript FROM lecture "
            "WHERE lower(title) LIKE ? ORDER BY created_at DESC LIMIT 1",
            (q,),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def list(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, title, source, length(transcript) FROM lecture "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [{"id": r[0], "title": r[1], "source": r[2], "chars": r[3]} for r in rows]

    def clear(self) -> None:
        self._db.execute("DELETE FROM lecture")
        self._db.commit()
