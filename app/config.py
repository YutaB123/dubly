"""Central settings, loaded once from environment variables (and a .env file).

Everything secret or environment-specific lives here so the rest of the code
never reads os.environ directly. Importing this module loads .env automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load variables from a .env file in the project root, if present.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Claude
    anthropic_api_key: str
    anthropic_model: str   # used for study-page generation (flashcards / exams)
    brain_model: str       # used for the conversational brain — kept fast for snappy texts

    # Canvas
    canvas_base_url: str
    canvas_token: str

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str

    # Channel: "sms" or "whatsapp". WhatsApp avoids US A2P 10DLC registration.
    channel: str
    whatsapp_from: str  # e.g. "whatsapp:+14155238886" (Twilio WhatsApp sandbox)

    # You
    my_phone_number: str

    # Web chat app: passcode that gates the private chat page (when channel="web")
    web_chat_secret: str
    # Web push (notifications when the app is closed) — VAPID keys
    vapid_public_key: str
    vapid_private_key: str   # raw base64url EC private key (pywebpush format)
    vapid_claim_email: str

    # This app's public web address (for study-page links)
    public_base_url: str

    # OneDrive (Microsoft Graph) — bridges WhatsApp files to/from the laptop folder
    onedrive_client_id: str
    onedrive_tenant: str
    onedrive_refresh_token: str
    onedrive_folder: str

    # Where local data files live
    data_dir: Path

    # Demo mode: serve a fake sample student instead of a real Canvas account
    # (for a public "try it live" link). When on, CANVAS_TOKEN isn't required.
    demo_mode: bool = False


def _get(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(
            f"Missing required setting {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def load_settings(require_secrets: bool = True) -> Settings:
    """Build a Settings object from the environment.

    When require_secrets is False, missing secrets fall back to empty strings.
    This lets tests and offline tools import the app without a full .env.
    """

    def secret(name: str) -> str:
        return _get(name) if require_secrets else os.environ.get(name, "")

    data_dir = Path(os.environ.get("DATA_DIR", "./data")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    demo_mode = os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")

    return Settings(
        anthropic_api_key=secret("ANTHROPIC_API_KEY"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        brain_model=os.environ.get("BRAIN_MODEL", "claude-sonnet-4-6"),
        canvas_base_url=os.environ.get(
            "CANVAS_BASE_URL", "https://canvas.uw.edu/api/v1"
        ).rstrip("/"),
        # In demo mode there's no real account, so the token isn't required.
        canvas_token=(os.environ.get("CANVAS_TOKEN", "") if demo_mode else secret("CANVAS_TOKEN")),
        # Twilio is only used in sms/whatsapp mode; optional so a web-only deploy
        # doesn't need it (its absence used to crash startup on Render).
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
        twilio_from_number=os.environ.get("TWILIO_FROM_NUMBER", ""),
        channel=os.environ.get("CHANNEL", "sms").lower(),
        whatsapp_from=os.environ.get("WHATSAPP_FROM", "whatsapp:+14155238886"),
        my_phone_number=os.environ.get("MY_PHONE_NUMBER", ""),
        web_chat_secret=os.environ.get("WEB_CHAT_SECRET", ""),
        vapid_public_key=os.environ.get("VAPID_PUBLIC_KEY", ""),
        # Raw base64url EC private key (what pywebpush expects), single-line.
        vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", ""),
        vapid_claim_email=os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@example.com"),
        # On Render, RENDER_EXTERNAL_URL is set automatically, so a deploy needs no
        # manual PUBLIC_BASE_URL. Explicit PUBLIC_BASE_URL still wins (local tunnels).
        public_base_url=(
            os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("RENDER_EXTERNAL_URL")
            or "http://localhost:8000"
        ).rstrip("/"),
        onedrive_client_id=os.environ.get(
            "ONEDRIVE_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e"
        ),
        onedrive_tenant=os.environ.get("ONEDRIVE_TENANT", "common"),
        onedrive_refresh_token=os.environ.get("ONEDRIVE_REFRESH_TOKEN", ""),
        onedrive_folder=os.environ.get("ONEDRIVE_FOLDER", "Documents/whatsapp"),
        data_dir=data_dir,
        demo_mode=demo_mode,
    )
