"""Tests for the tool box Claude calls (routing + formatting)."""

from __future__ import annotations

from datetime import datetime, timezone

from app import tools
from app.canvas import AssignmentDetail, Course, Item


class FakeCanvas:
    def __init__(self):
        self.detail_calls = []

    def list_courses(self):
        return [
            Course(id=1, name="Intermediate Data Programming", code="CSE 163 A"),
            Course(id=2, name="Linear Algebra", code="MATH 308"),
            # Not a class (no 3-digit code) — must be filtered out everywhere.
            Course(id=3, name="Informatics Resource Site", code="Informatics Resource"),
        ]

    def get_upcoming(self, days=7):
        return [
            Item(
                course="CSE 163 A",
                title="Homework 4",
                due_at=datetime(2026, 6, 10, 6, 59, tzinfo=timezone.utc),
                ref="1:55",
                html_url="https://canvas.uw.edu/courses/1/assignments/55",
                type="assignment",
            )
        ]

    def get_assignment_detail(self, ref):
        self.detail_calls.append(ref)
        return AssignmentDetail(
            name="Homework 4",
            course="CSE 163 A",
            due_at=datetime(2026, 6, 10, 6, 59, tzinfo=timezone.utc),
            points=20,
            description="Implement merge sort.",
            html_url="https://canvas.uw.edu/courses/1/assignments/55",
        )

    def search_assignments(self, query, course_id=None):
        return []

    def syllabus_url(self, course):
        return "https://canvas.uw.edu/courses/9/assignments/syllabus"

    def canvas_get(self, path, params=None):
        return {"path": path, "params": params, "items": [1, 2, 3]}

    def get_announcements(self, days_back=14):
        from app.canvas import Announcement

        return [
            Announcement(
                course="STAT 311 A",
                title="Final exam details",
                text="The final is June 9 at 2:30pm in the lecture hall.",
                posted_at=datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
            )
        ]

    def get_inbox(self, limit=20):
        from app.canvas import InboxMessage

        return [
            InboxMessage(
                course="PHIL 149 Sp 26",
                subject="Final exam reminder",
                snippet="The Final Exam is Wednesday at 2:30pm, scantron.",
                sent_at=datetime(2026, 6, 6, 17, 0, tzinfo=timezone.utc),
            )
        ]

    def get_calendar_events(self, days_ahead=14):
        from app.canvas import CalendarEvent

        return [
            CalendarEvent(
                course="MATH 124",
                title="Final Exam",
                start_at=datetime(2026, 6, 9, 15, 30, tzinfo=timezone.utc),
                location="Smith 120",
                description="Standards-based final.",
            )
        ]

    def get_syllabus(self, course):
        return f"Syllabus for {course}: the final is closed book, one notes page allowed."

    def get_grades(self):
        from app.canvas import Grade

        return [
            Grade(course="CSE 163 A", score=97.87, grade=None),
            Grade(course="STAT 311", score=92.0, grade="A-"),
            Grade(course="Informatics Resource", score=100.0, grade=None),  # not a class
        ]

    def get_course_grades(self, course):
        from app.canvas import AssignmentScore

        return [
            AssignmentScore(name="Curriculum Standard 1A", score=1.0, points=1.0),
            AssignmentScore(name="Curriculum Standard 2A", score=None, points=1.0),
            AssignmentScore(name="HW 2.5", score=41.0, points=43.0),
        ]

    def get_submission(self, course, assignment):
        from app.canvas import Submission

        return Submission(
            assignment="Meaning of Life", state="graded", score=1.0, points=1.0,
            text="I believe my sources of meaning are passion and purpose.",
            comments=[("Prof X", "Be more specific.")], attachments=["essay.docx"],
        )


NOW = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)


def make_box():
    return tools.ToolBox(canvas=FakeCanvas(), now=lambda: NOW)


def test_schemas_include_phase1_tools():
    names = {t["name"] for t in make_box().schemas()}
    assert {"get_courses", "get_upcoming", "get_assignment_detail",
            "search_assignments"} <= names


def test_schemas_include_inbox_and_announcement_tools():
    names = {t["name"] for t in make_box().schemas()}
    assert {"get_announcements", "check_inbox", "get_calendar", "get_syllabus"} <= names


def test_dispatch_get_syllabus_passes_course_and_returns_text():
    out = make_box().dispatch("get_syllabus", {"course": "PHIL 149"})
    assert "PHIL 149" in out
    assert "closed book" in out.lower()


def test_dispatch_get_grades_formats_scores():
    out = make_box().dispatch("get_grades", {})
    assert "CSE 163" in out
    assert "97.87%" in out
    assert "A-" in out


def test_dispatch_get_grades_excludes_non_classes():
    out = make_box().dispatch("get_grades", {})
    assert "Informatics" not in out  # no 3-digit code -> not a class


def test_dispatch_get_courses_excludes_non_classes():
    out = make_box().dispatch("get_courses", {})
    assert "Informatics" not in out


def test_dispatch_get_submission_includes_text_and_comments():
    out = make_box().dispatch("get_submission", {"course": "PHIL 149", "assignment": "meaning"})
    assert "passion and purpose" in out
    assert "Be more specific" in out
    assert "Prof X" in out


def test_dispatch_get_course_grades_marks_not_done():
    out = make_box().dispatch("get_course_grades", {"course": "MATH 124"})
    assert "Curriculum Standard 2A: —/1 (NOT DONE)" in out
    assert "Curriculum Standard 1A: 1/1 (done)" in out
    assert "(partial)" in out  # HW 41/43


def test_dispatch_get_calendar_includes_event_time_and_location():
    out = make_box().dispatch("get_calendar", {})
    assert "MATH 124" in out
    assert "Final Exam" in out
    assert "Smith 120" in out


def test_dispatch_get_announcements_includes_course_and_text():
    out = make_box().dispatch("get_announcements", {})
    assert "STAT 311 A" in out
    assert "June 9 at 2:30pm" in out
    assert "Final exam details" in out


def test_dispatch_check_inbox_includes_subject_and_snippet():
    out = make_box().dispatch("check_inbox", {})
    assert "Final exam reminder" in out
    assert "PHIL 149" in out
    assert "scantron" in out.lower()


def test_dispatch_get_courses_lists_codes_and_names():
    out = make_box().dispatch("get_courses", {})
    assert "CSE 163" in out               # shortened code (dept + 3 digits)
    assert "Intermediate Data Programming" in out
    assert "MATH 308" in out


def test_dispatch_get_upcoming_includes_due_phrasing_and_ref():
    out = make_box().dispatch("get_upcoming", {"days": 7})
    assert "Homework 4" in out
    assert "CSE 163 A" in out
    assert "Tue 11:59pm" in out  # formatted in Pacific
    assert "1:55" in out          # ref so Claude can fetch detail


def test_dispatch_get_upcoming_includes_assignment_link():
    # Claude needs the Canvas web link so it can offer it to the student.
    out = make_box().dispatch("get_upcoming", {"days": 7})
    assert "https://canvas.uw.edu/courses/1/assignments/55" in out


def test_dispatch_get_assignment_detail_includes_link():
    out = make_box().dispatch("get_assignment_detail", {"ref": "1:55"})
    assert "https://canvas.uw.edu/courses/1/assignments/55" in out


def test_dispatch_get_syllabus_includes_page_link():
    out = make_box().dispatch("get_syllabus", {"course": "PHIL 149"})
    assert "https://canvas.uw.edu/courses/9/assignments/syllabus" in out


def test_dispatch_get_assignment_detail_passes_ref_through():
    box = make_box()
    out = box.dispatch("get_assignment_detail", {"ref": "1:55"})
    assert box.canvas.detail_calls == ["1:55"]
    assert "Implement merge sort." in out
    assert "20" in out  # points


def test_dispatch_canvas_api_returns_json_for_any_path():
    out = make_box().dispatch("canvas_api", {"path": "/users/self/todo"})
    assert "/users/self/todo" in out
    assert "items" in out


def test_dispatch_unknown_tool_is_reported_not_raised():
    out = make_box().dispatch("nonexistent", {})
    assert "unknown tool" in out.lower()


def test_dispatch_swallows_backend_errors_into_a_message():
    class Broken(FakeCanvas):
        def get_upcoming(self, days=7):
            raise RuntimeError("canvas down")

    box = tools.ToolBox(canvas=Broken(), now=lambda: NOW)
    out = box.dispatch("get_upcoming", {})
    assert "couldn't" in out.lower() or "error" in out.lower()


def test_empty_upcoming_reads_clearly():
    class Empty(FakeCanvas):
        def get_upcoming(self, days=7):
            return []

    box = tools.ToolBox(canvas=Empty(), now=lambda: NOW)
    out = box.dispatch("get_upcoming", {"days": 7})
    assert "nothing" in out.lower()
