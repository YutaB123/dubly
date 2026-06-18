"""Tests for proactive alerts (new grades + due-soon pushes)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.alerts import AlertService
from app.db import AlertStore
from app.canvas import Grade, Item


class FakeCanvas:
    def __init__(self, grades=None, upcoming=None):
        self._grades = grades or []
        self._upcoming = upcoming or []

    def get_grades(self):
        return self._grades

    def get_upcoming(self, days=2):
        return self._upcoming


class FakePush:
    def __init__(self):
        self.notes = []

    def notify(self, title, body, url="/chat", force=False):
        self.notes.append((title, body))


def _svc(tmp_path, canvas):
    return AlertService(canvas=canvas, push=FakePush(), store=AlertStore(tmp_path / "a.sqlite"))


def test_grade_change_alerts_but_first_sighting_is_silent(tmp_path):
    canvas = FakeCanvas(grades=[Grade(course="CSE 163 A", score=90.0, grade=None)])
    s = _svc(tmp_path, canvas)
    s.check_once()                       # baseline — no alert
    assert s.push.notes == []
    canvas._grades = [Grade(course="CSE 163 A", score=95.0, grade=None)]
    s.check_once()                       # changed — alert
    assert s.push.notes and "95" in s.push.notes[0][1] and "CSE 163" in s.push.notes[0][1]


def test_due_soon_alerts_only_once(tmp_path):
    item = Item(course="MATH 124", title="HW5", due_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
                ref="1:55", html_url="", type="assignment")
    s = _svc(tmp_path, FakeCanvas(upcoming=[item]))
    s.check_once(); s.check_once()
    due = [n for n in s.push.notes if "Due" in n[0]]
    assert len(due) == 1 and "HW5" in due[0][1]


def test_non_class_grades_are_ignored(tmp_path):
    canvas = FakeCanvas(grades=[Grade(course="Informatics Resource", score=100.0, grade=None)])
    s = _svc(tmp_path, canvas)
    s.check_once()
    canvas._grades = [Grade(course="Informatics Resource", score=50.0, grade=None)]
    s.check_once()
    assert s.push.notes == []
