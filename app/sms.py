"""The phone connection: receiving and sending texts through Twilio.

Only your number is allowed. Outgoing replies and reminders go out through the
Twilio REST API. Long replies are split into multiple texts.
"""

from __future__ import annotations

import re

from app.textfmt import no_em_dash

MAX_SMS_LEN = 600  # comfortably long; keeps each send to a few segments


def normalize_number(num: str) -> str:
    """Reduce a phone number to its last 10 digits for comparison."""
    digits = re.sub(r"\D", "", num or "")
    return digits[-10:]


def numbers_match(a: str, b: str) -> bool:
    return normalize_number(a) == normalize_number(b) and normalize_number(a) != ""


def _split(text: str, limit: int = MAX_SMS_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class SmsClient:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        my_number: str,
        client=None,
        channel: str = "sms",
        whatsapp_from: str = "",
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.my_number = my_number
        # channel is "sms" or "whatsapp"; whatsapp_from is the Twilio WhatsApp
        # sender, e.g. "whatsapp:+14155238886" (the sandbox number).
        self.channel = channel
        self.whatsapp_from = whatsapp_from
        if client is not None:
            self._client = client
        else:  # pragma: no cover - exercised only with real credentials
            from twilio.rest import Client

            self._client = Client(account_sid, auth_token)

    def is_allowed(self, from_number: str) -> bool:
        # Handles plain numbers and "whatsapp:+1..." (digits are compared).
        return numbers_match(from_number, self.my_number)

    def _route(self, to: str | None) -> tuple[str, str]:
        """Pick the (from, to) pair for the active channel."""
        recipient = to or self.my_number
        if self.channel == "whatsapp":
            if not recipient.startswith("whatsapp:"):
                recipient = "whatsapp:" + recipient
            return self.whatsapp_from, recipient
        return self.from_number, recipient

    def send(
        self, text: str, to: str | None = None, media_url: list[str] | None = None
    ) -> None:
        from_, recipient = self._route(to)
        chunks = _split(no_em_dash(text)) or [""]
        for i, chunk in enumerate(chunks):
            kwargs = {"body": chunk, "from_": from_, "to": recipient}
            # Attach the media to the first message only.
            if media_url and i == 0:
                kwargs["media_url"] = media_url
            self._client.messages.create(**kwargs)

    def send_typing(self, message_sid: str) -> bool:
        """Show WhatsApp's 'typing…' animation for the message we're answering.

        References the inbound Twilio message SID; Twilio shows the animation
        (and marks the message read) until our reply lands or ~25s passes.
        Returns True if Twilio accepted it. No-op (False) off the WhatsApp
        channel or without a SID. Never raises — it must not block a reply.
        """
        if self.channel != "whatsapp" or not message_sid:
            return False
        import httpx

        try:
            resp = httpx.post(
                "https://messaging.twilio.com/v2/Indicators/Typing.json",
                auth=(self.account_sid, self.auth_token),
                data={"messageId": message_sid, "channel": "whatsapp"},
                timeout=10.0,
            )
            return resp.status_code < 300 and bool(resp.json().get("success", True))
        except Exception:
            return False

    def download_media(self, url: str) -> tuple[bytes, str]:
        """Download a Twilio media attachment (needs the account's basic auth)."""
        import httpx

        resp = httpx.get(
            url,
            auth=(self.account_sid, self.auth_token),
            follow_redirects=True,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

    def validate_signature(self, url: str, form: dict, signature: str) -> bool:
        """Confirm an incoming webhook really came from Twilio."""
        from twilio.request_validator import RequestValidator

        return RequestValidator(self.auth_token).validate(url, form, signature)
