"""Small text cleanups applied to everything we send the student.

Right now: strip em dashes (—) and en dashes (–), which the model likes to
sprinkle in. We replace them with a plain hyphen so the chat reads naturally.
"""

from __future__ import annotations


def no_em_dash(text: str | None) -> str:
    """Replace em/en dashes with a plain hyphen. Empty string for None."""
    if not text:
        return ""
    # Spaced dash -> spaced hyphen (keeps "a — b" reading as "a - b");
    # then any remaining bare dash -> hyphen.
    return (
        text.replace(" — ", " - ")
        .replace(" – ", " - ")
        .replace("—", "-")
        .replace("–", "-")
    )
