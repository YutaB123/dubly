"""The web channel: a stand-in for SmsClient that writes into a ChatStore.

The rest of the app (brain, documents, reminders) only knows how to call
`sms.send(...)`. For the web app we hand them this WebClient instead: every
"send" appends an assistant message to the *active* chat.

Which chat is "active" is request-scoped (a ContextVar set by the /chat/send
handler), so a document link or reminder produced mid-request lands in the chat
the student is actually looking at. Outside a request (e.g. a scheduled reminder
firing) it falls back to the most recent chat.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.textfmt import no_em_dash

# The chat id the current request is operating on (None outside a request).
_active_chat: ContextVar[int | None] = ContextVar("active_chat_id", default=None)


def set_active_chat(chat_id: int):
    """Mark which chat assistant messages should be written to; returns a token."""
    return _active_chat.set(chat_id)


def reset_active_chat(token) -> None:
    _active_chat.reset(token)


class WebClient:
    channel = "web"

    def __init__(self, chats: Any, push: Any = None):
        # chats is a ChatStore; push is an optional PushService.
        self.chats = chats
        self.push = push

    def send(
        self, text: str, to: str | None = None, media_url: list[str] | None = None
    ) -> None:
        """Deliver an assistant message to the active chat (and fire a push)."""
        text = no_em_dash(text)
        chat_id = _active_chat.get()
        if chat_id is None:
            chat_id = self.chats.ensure_chat()
        self.chats.append(chat_id, "assistant", text, (media_url or [""])[0])
        if self.push is not None and (text or media_url):
            preview = (text or "sent you a file").strip()
            if len(preview) > 120:
                preview = preview[:117] + "…"
            self.push.notify("Study Assistant", preview)

    def send_typing(self, message_sid: str) -> bool:
        # The web UI shows its own typing dots while it waits for the reply.
        return False

    def download_media(self, url: str) -> tuple[bytes, str]:
        # Inbound files over the web aren't routed through here (v1).
        return b"", "application/octet-stream"

    def is_allowed(self, from_number: str) -> bool:
        # The web app authenticates with a shared secret, not a phone number.
        return False

    def validate_signature(self, url: str, form: dict, signature: str) -> bool:
        return False
