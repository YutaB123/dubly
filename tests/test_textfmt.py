"""Tests for outgoing-text cleanup (no em dashes)."""

from __future__ import annotations

from app.textfmt import no_em_dash


def test_replaces_spaced_em_dash_with_spaced_hyphen():
    assert no_em_dash("CSE 163 — Homework 4") == "CSE 163 - Homework 4"


def test_replaces_bare_em_dash():
    assert no_em_dash("due tue—11:59pm") == "due tue-11:59pm"


def test_replaces_en_dash():
    assert no_em_dash("pages 3–5") == "pages 3-5"


def test_leaves_ordinary_hyphens_and_text_alone():
    assert no_em_dash("multi-part question, all good") == "multi-part question, all good"


def test_handles_empty_and_none():
    assert no_em_dash("") == ""
    assert no_em_dash(None) == ""
