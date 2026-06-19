"""Transcribe an uploaded lecture recording with OpenAI Whisper.

Optional: only used when a student uploads an audio/video file on the "Add
lecture" screen. If no OPENAI_API_KEY is set, the transcript paste/upload paths
still work and audio uploads get a graceful message instead.
"""

from __future__ import annotations

import io

WHISPER_MODEL = "whisper-1"
WHISPER_MAX_BYTES = 25 * 1024 * 1024  # OpenAI's hard limit per request


class TranscribeError(Exception):
    """Carries a short, user-facing reason (shown in the Add-lecture dialog)."""


class Transcriber:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, filename: str, data: bytes) -> str:
        if not self.enabled:
            raise TranscribeError(
                "audio transcription isn't set up here — paste or upload the "
                "lecture transcript/captions instead."
            )
        if len(data) > WHISPER_MAX_BYTES:
            mb = len(data) / (1024 * 1024)
            raise TranscribeError(
                f"that recording is {mb:.0f}MB — transcription maxes out at 25MB. "
                "grab the transcript from Panopto's captions panel and paste it instead."
            )
        try:
            from openai import OpenAI

            buf = io.BytesIO(data)
            buf.name = filename or "lecture.mp3"  # the SDK infers format from the name
            client = OpenAI(api_key=self.api_key)
            text = client.audio.transcriptions.create(
                model=WHISPER_MODEL, file=buf, response_format="text"
            )
            return (text or "").strip()
        except TranscribeError:
            raise
        except Exception as exc:  # network / API / decode error
            raise TranscribeError(f"couldn't transcribe that recording ({str(exc)[:120]}).")
