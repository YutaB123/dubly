"""The demo has final exams on the calendar, and the calendar tool reports an
explicit day count so 'how long until my math final?' can be answered in days."""

from __future__ import annotations

from app.demo_canvas import DemoCanvasClient
from app.tools import ToolBox


def test_demo_has_finals_on_calendar():
    events = DemoCanvasClient().get_calendar_events(days_ahead=14)
    titles = {e.course: e.title for e in events}
    assert "MATH 126" in titles
    assert "final" in titles["MATH 126"].lower()
    assert all(e.start_at is not None for e in events)


def test_calendar_tool_includes_day_count():
    tb = ToolBox(canvas=DemoCanvasClient())
    out = tb.dispatch("get_calendar", {"days_ahead": 14})
    assert "MATH 126" in out
    assert "in 13 days" in out          # the cumulative final, 13 days out
    assert "Smith Hall 205" in out       # location carried through


def test_far_window_still_surfaces_math_final():
    # even a tight default window catches it (it's inside 14 days)
    out = ToolBox(canvas=DemoCanvasClient()).dispatch("get_calendar", {})
    assert "Final Exam (cumulative)" in out
