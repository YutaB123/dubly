"""Tests for the Canvas connection."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app import canvas


# --- html_to_text -----------------------------------------------------------

def test_html_to_text_strips_tags_and_collapses_whitespace():
    html = "<p>Read <b>chapter 3</b>.</p>\n<p>Then do   the exercises.</p>"
    # Each paragraph becomes its own line; inner whitespace collapses.
    assert canvas.html_to_text(html) == "Read chapter 3.\nThen do the exercises."


def test_html_to_text_decodes_entities():
    assert canvas.html_to_text("Tom &amp; Jerry &lt;3") == "Tom & Jerry <3"


def test_html_to_text_turns_list_items_into_lines():
    html = "<ul><li>part a</li><li>part b</li></ul>"
    assert canvas.html_to_text(html) == "part a\npart b"


def test_html_to_text_handles_empty():
    assert canvas.html_to_text("") == ""
    assert canvas.html_to_text(None) == ""


# --- CanvasClient (HTTP, with a mocked transport) ----------------------------

def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://canvas.uw.edu/api/v1")
    return canvas.CanvasClient(base_url="https://canvas.uw.edu/api/v1", token="t", http=http)


def test_list_courses_returns_id_name_code():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/courses"
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json=[
            {"id": 1, "name": "Intermediate Data Programming", "course_code": "CSE 163 A"},
            {"id": 2, "name": "Linear Algebra", "course_code": "MATH 308"},
        ])

    client = make_client(handler)
    courses = client.list_courses()
    assert [c.code for c in courses] == ["CSE 163 A", "MATH 308"]
    assert courses[0].id == 1
    assert courses[0].name == "Intermediate Data Programming"


def test_list_courses_skips_courses_without_a_name():
    # Canvas sometimes returns restricted/empty course stubs.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": 1, "name": "Real Course", "course_code": "CSE 163"},
            {"id": 99, "access_restricted_by_date": True},
        ])

    client = make_client(handler)
    courses = client.list_courses()
    assert len(courses) == 1
    assert courses[0].id == 1


def test_get_upcoming_parses_planner_items_with_due_dates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/planner/items"
        return httpx.Response(200, json=[
            {
                "plannable_type": "assignment",
                "course_id": 1,
                "context_name": "CSE 163 A",
                "html_url": "/courses/1/assignments/55",
                "plannable": {
                    "id": 55,
                    "title": "Homework 4",
                    "due_at": "2026-06-10T06:59:00Z",
                },
            },
            {
                # No due date -> should be skipped.
                "plannable_type": "announcement",
                "context_name": "CSE 163 A",
                "plannable": {"id": 70, "title": "Welcome"},
            },
        ])

    client = make_client(handler)
    items = client.get_upcoming(days=7)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Homework 4"
    assert item.course == "CSE 163 A"
    assert item.ref == "1:55"
    assert item.due_at == datetime(2026, 6, 10, 6, 59, tzinfo=timezone.utc)


def test_get_upcoming_skips_already_done_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {  # still to do — keep
                "plannable_type": "assignment", "course_id": 1, "context_name": "CSE 163 A",
                "plannable": {"id": 55, "title": "Homework 4", "due_at": "2026-06-10T06:59:00Z"},
                "submissions": {"submitted": False, "graded": False, "excused": False},
            },
            {  # submitted — drop
                "plannable_type": "assignment", "course_id": 1, "context_name": "CSE 163 A",
                "plannable": {"id": 56, "title": "Homework 3", "due_at": "2026-06-09T06:59:00Z"},
                "submissions": {"submitted": True, "graded": False, "excused": False},
            },
            {  # graded — drop
                "plannable_type": "assignment", "course_id": 2, "context_name": "MATH 308",
                "plannable": {"id": 70, "title": "Quiz 1", "due_at": "2026-06-09T06:59:00Z"},
                "submissions": {"submitted": False, "graded": True, "excused": False},
            },
            {  # manually checked off in planner — drop
                "plannable_type": "assignment", "course_id": 2, "context_name": "MATH 308",
                "plannable": {"id": 71, "title": "Reading", "due_at": "2026-06-09T06:59:00Z"},
                "submissions": False,
                "planner_override": {"marked_complete": True},
            },
        ])

    items = make_client(handler).get_upcoming(days=7)
    titles = [i.title for i in items]
    assert titles == ["Homework 4"]  # only the not-yet-done one


def test_get_assignment_detail_returns_clean_description():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/courses/1/assignments/55"
        return httpx.Response(200, json={
            "id": 55,
            "name": "Homework 4",
            "due_at": "2026-06-10T06:59:00Z",
            "points_possible": 20,
            "html_url": "https://canvas.uw.edu/courses/1/assignments/55",
            "description": "<p>Implement <b>merge sort</b>.</p>",
        })

    client = make_client(handler)
    detail = client.get_assignment_detail("1:55")
    assert detail.name == "Homework 4"
    assert detail.points == 20
    assert detail.description == "Implement merge sort."
    assert detail.due_at == datetime(2026, 6, 10, 6, 59, tzinfo=timezone.utc)


def test_get_assignment_detail_rejects_bad_ref():
    client = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client.get_assignment_detail("not-a-ref")


def test_get_announcements_maps_course_and_cleans_text():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/courses":
            return httpx.Response(200, json=[
                {"id": 7, "name": "Statistics", "course_code": "STAT 311 A"},
            ])
        assert request.url.path == "/api/v1/announcements"
        # The course context code must be passed through as a repeated query key.
        assert "course_7" in request.url.query.decode()
        return httpx.Response(200, json=[
            {
                "context_code": "course_7",
                "title": "Final exam details",
                "message": "<p>The final is <b>June 9 at 2:30pm</b>.</p>",
                "posted_at": "2026-06-05T18:00:00Z",
            },
        ])

    client = make_client(handler)
    anns = client.get_announcements(days_back=14)
    assert len(anns) == 1
    a = anns[0]
    assert a.course == "STAT 311 A"
    assert a.title == "Final exam details"
    assert a.text == "The final is June 9 at 2:30pm."
    assert a.posted_at == datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)


def test_get_announcements_empty_when_no_courses():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])  # no courses

    assert make_client(handler).get_announcements() == []


def test_get_grades_maps_course_and_skips_ungraded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/courses":
            return httpx.Response(200, json=[
                {"id": 1, "name": "Intermediate Data Programming", "course_code": "CSE 163 A"},
                {"id": 2, "name": "Stats", "course_code": "STAT 311"},
            ])
        assert request.url.path == "/api/v1/users/self/enrollments"
        return httpx.Response(200, json=[
            {"course_id": 1, "grades": {"current_score": 97.87, "current_grade": None}},
            {"course_id": 2, "grades": {"current_score": 92.86, "current_grade": "A-"}},
            {"course_id": 9, "grades": {"current_score": None, "current_grade": None}},  # no grade
        ])

    grades = make_client(handler).get_grades()
    assert len(grades) == 2  # the ungraded course is skipped
    assert grades[0].course == "CSE 163 A"
    assert grades[0].score == 97.87
    assert grades[1].course == "STAT 311"
    assert grades[1].grade == "A-"


def test_get_submission_reads_body_and_comments():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/v1/courses":
            return httpx.Response(200, json=[{"id": 5, "name": "Phil", "course_code": "PHIL 149"}])
        if p == "/api/v1/courses/5/assignments":
            return httpx.Response(200, json=[{"id": 9, "name": "Meaning of Life", "points_possible": 1}])
        if p == "/api/v1/courses/5/assignments/9/submissions/self":
            return httpx.Response(200, json={
                "workflow_state": "graded", "score": 1.0,
                "body": "<p>my essay about <b>passion</b></p>",
                "submission_comments": [{"author": {"display_name": "Prof X"}, "comment": "Be more specific."}],
                "attachments": [],
            })
        return httpx.Response(404)

    s = make_client(handler).get_submission("phil 149", "meaning")
    assert s.assignment == "Meaning of Life"
    assert s.score == 1.0
    assert "passion" in s.text
    assert s.comments == [("Prof X", "Be more specific.")]


def test_get_submission_extracts_uploaded_text_file():
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/v1/courses":
            return httpx.Response(200, json=[{"id": 5, "name": "X", "course_code": "X"}])
        if p == "/api/v1/courses/5/assignments":
            return httpx.Response(200, json=[{"id": 9, "name": "Essay", "points_possible": 10}])
        if p.endswith("/submissions/self"):
            return httpx.Response(200, json={
                "workflow_state": "submitted", "score": None,
                "attachments": [{"filename": "essay.txt", "content-type": "text/plain",
                                 "url": "https://canvas.uw.edu/files/1/download"}],
                "submission_comments": [],
            })
        if p == "/files/1/download":
            return httpx.Response(200, content=b"This is my essay text.")
        return httpx.Response(404)

    s = make_client(handler).get_submission("X", "Essay")
    assert "my essay text" in s.text
    assert s.attachments == ["essay.txt"]


def test_canvas_get_reads_any_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/courses/5/discussion_topics"
        assert "per_page" in request.url.query.decode()  # default page size applied
        return httpx.Response(200, json=[{"id": 1, "title": "Welcome"}])

    data = make_client(handler).canvas_get("/courses/5/discussion_topics")
    assert data == [{"id": 1, "title": "Welcome"}]


def test_get_course_grades_parses_submission_scores():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/courses":
            return httpx.Response(200, json=[
                {"id": 5, "name": "Calculus", "course_code": "MATH 124"},
            ])
        assert request.url.path == "/api/v1/courses/5/assignments"
        return httpx.Response(200, json=[
            {"name": "Curriculum Standard 1A", "points_possible": 1,
             "submission": {"score": 1.0, "workflow_state": "graded"}},
            {"name": "Curriculum Standard 2A", "points_possible": 1,
             "submission": {"score": None, "workflow_state": "unsubmitted"}},
            {"name": "HW 2.5", "points_possible": 43, "submission": {"score": 41.0}},
        ])

    items = make_client(handler).get_course_grades("math 124")  # nickname resolves
    assert [(i.name, i.score, i.points) for i in items] == [
        ("Curriculum Standard 1A", 1.0, 1),
        ("Curriculum Standard 2A", None, 1),
        ("HW 2.5", 41.0, 43),
    ]


def test_get_syllabus_resolves_course_code_and_cleans_html():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/courses":
            return httpx.Response(200, json=[
                {"id": 9, "name": "Existentialism and Film", "course_code": "PHIL 149 Sp 26"},
            ])
        assert request.url.path == "/api/v1/courses/9"
        assert "syllabus_body" in request.url.query.decode()
        return httpx.Response(200, json={
            "id": 9,
            "syllabus_body": "<p>The final is <b>closed book</b>.</p>",
        })

    client = make_client(handler)
    text = client.get_syllabus("phil 149")  # resolves by code substring
    assert text == "The final is closed book."


def test_get_syllabus_unknown_course_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": 9, "name": "Stats", "course_code": "STAT 311"},
        ])

    assert make_client(handler).get_syllabus("art history") == ""


def test_course_web_url_strips_api_path():
    client = make_client(lambda r: httpx.Response(200, json=[]))
    assert client.course_web_url(5) == "https://canvas.uw.edu/courses/5"


def test_syllabus_url_builds_web_link_from_course_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": 9, "name": "Existentialism and Film", "course_code": "PHIL 149 Sp 26"},
        ])

    client = make_client(handler)
    assert client.syllabus_url("phil 149") == (
        "https://canvas.uw.edu/courses/9/assignments/syllabus"
    )


def test_syllabus_url_unknown_course_is_empty():
    client = make_client(lambda r: httpx.Response(200, json=[
        {"id": 1, "name": "Stats", "course_code": "STAT 311"},
    ]))
    assert client.syllabus_url("art history") == ""


def test_get_calendar_events_parses_and_sorts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/courses":
            return httpx.Response(200, json=[
                {"id": 7, "name": "Statistics", "course_code": "STAT 311 A"},
            ])
        assert request.url.path == "/api/v1/calendar_events"
        q = request.url.query.decode()
        assert "type=event" in q
        assert "course_7" in q
        return httpx.Response(200, json=[
            {
                "context_code": "course_7",
                "title": "Final Exam",
                "start_at": "2026-06-09T21:30:00Z",
                "location_name": "Kane 130",
                "description": "<p>Closed book.</p>",
            },
        ])

    events = make_client(handler).get_calendar_events(days_ahead=14)
    assert len(events) == 1
    e = events[0]
    assert e.course == "STAT 311 A"
    assert e.title == "Final Exam"
    assert e.location == "Kane 130"
    assert e.description == "Closed book."
    assert e.start_at == datetime(2026, 6, 9, 21, 30, tzinfo=timezone.utc)


def test_get_inbox_uses_full_message_body_not_preview():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/conversations":
            return httpx.Response(200, json=[
                {
                    "id": 5,
                    "subject": "Final exam reminder",
                    "context_name": "STAT 311 A Sp 26",
                    # The list endpoint only gives a truncated preview:
                    "last_message": "The Final Exam will take place on June 9 at 2:30pm in the le...",
                    "last_message_at": "2026-06-06T17:00:00Z",
                },
            ])
        # The detail endpoint carries the FULL body.
        assert request.url.path == "/api/v1/conversations/5"
        return httpx.Response(200, json={
            "id": 5,
            "messages": [
                {"body": "The Final Exam is on June 9 at 2:30pm in the lecture hall. "
                         "It is closed book, but you may bring one double-sided page of notes."}
            ],
        })

    msgs = make_client(handler).get_inbox(limit=20)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.subject == "Final exam reminder"
    assert m.course == "STAT 311 A Sp 26"
    # The detail that the preview cut off is now present:
    assert "lecture hall" in m.snippet
    assert "closed book" in m.snippet
    assert m.sent_at == datetime(2026, 6, 6, 17, 0, tzinfo=timezone.utc)
