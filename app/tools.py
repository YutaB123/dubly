"""The tools Claude can call, plus how Canvas data is formatted for it.

ToolBox holds the backing services (Canvas now; reminders and the study-page
maker plug in for later phases). It exposes:
  - schemas():  the tool definitions to hand Claude
  - dispatch(): run one tool call and return a short text result

Backend errors are turned into readable messages rather than raised, so one
flaky Canvas call never crashes a whole text reply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app import timefmt
from app.canvas import (
    AssignmentDetail,
    Course,
    Item,
    is_real_class,
    short_course_code,
)


# --- Tool schemas (Phase 1: Canvas Q&A) --------------------------------------

CANVAS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_courses",
        "description": "List the student's active courses (codes and full names). "
        "Use when you need to know what classes they're taking or to map a "
        "course nickname like '163' to a real course.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_upcoming",
        "description": "List upcoming assignments across all courses with their due "
        "dates, sorted soonest first. Already-submitted, graded, or checked-off items "
        "are automatically excluded — this is only stuff the student still needs to do. "
        "Use for 'what's due', 'what's my next thing', or 'what's my workload this "
        "week'. Each line ends with a [ref] you can pass to get_assignment_detail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 7).",
                }
            },
        },
    },
    {
        "name": "get_assignment_detail",
        "description": "Get the full description of one assignment (what it's "
        "asking for), its due date and points. Pass the ref shown by "
        "get_upcoming or search_assignments, e.g. '1:55'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Assignment ref 'courseId:assignmentId'."}
            },
            "required": ["ref"],
        },
    },
    {
        "name": "canvas_api",
        "description": "Read ANY data from the student's Canvas (read-only). This is the "
        "catch-all — use it for anything the other tools don't directly cover: discussion "
        "boards, files/documents, pages, modules, quizzes, people/classmates, to-do items, "
        "groups, rubrics, a specific assignment's submission and comments, course settings, "
        "etc. 'path' is a Canvas REST API v1 path. Examples: '/courses/123/discussion_topics', "
        "'/users/self/todo', '/courses/123/modules?include[]=items', "
        "'/courses/123/assignments/456/submissions/self?include[]=submission_comments', "
        "'/courses/123/files'. ALWAYS get real course ids from get_courses first. Returns "
        "JSON; keep it small with a specific path or a small per_page. If the data you need "
        "exists anywhere in Canvas, you can get it here — so always try this before telling "
        "the student you can't find something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Canvas API v1 path, e.g. '/courses/123/files' or '/users/self/todo'.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional query params, e.g. {\"per_page\": 20, \"include[]\": \"submission\"}.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_assignments",
        "description": "Find assignments by name (optionally within one course id). "
        "Use when the student names a specific assignment that may not be in "
        "the upcoming list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Part of the assignment name."},
                "course_id": {"type": "integer", "description": "Optional course id to limit to."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_announcements",
        "description": "Read recent course announcements from the student's classes. "
        "Crucial for exam dates/times/locations, schedule changes, and anything an "
        "instructor posted that is NOT a Canvas assignment — final-exam logistics "
        "are very often here rather than in the assignment list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to look (default 14).",
                }
            },
        },
    },
    {
        "name": "check_inbox",
        "description": "Read the student's recent Canvas inbox messages (conversations "
        "from instructors and TAs). Use for exam details, instructions, or anything "
        "messaged directly that isn't a posted assignment or announcement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent threads to read (default 20).",
                }
            },
        },
    },
    {
        "name": "get_submission",
        "description": "Read what the student actually SUBMITTED for an assignment — the "
        "text they wrote, INCLUDING the contents of uploaded Word/PDF/text files, plus the "
        "instructor's feedback comments and score. Use when they ask what they turned in, "
        "what they wrote, their feedback, the professor's comment on something, or want help "
        "building on a past submission. Pass the course and the assignment name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Course name, code, or nickname."},
                "assignment": {
                    "type": "string",
                    "description": "Assignment name, e.g. 'Meaning of Life'.",
                },
            },
            "required": ["course", "assignment"],
        },
    },
    {
        "name": "get_grades",
        "description": "Get the student's current grade in each class (their percentage "
        "so far, and letter grade if the course shows one). Use when they ask about "
        "grades, their score, how they're doing in a class, or 'what's my grade in X'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_course_grades",
        "description": "Get the student's score on EVERY assignment in ONE course. Use "
        "for 'which assignments/standards have I done or still need to do', 'what did I "
        "get on X', or standards-based courses (e.g. MATH 124's Curriculum Standards, "
        "each worth 1 point, scored 1 when credited and blank when not). Pass the course "
        "name, code, or nickname.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course name, code, or nickname (e.g. 'MATH 124', '124').",
                }
            },
            "required": ["course"],
        },
    },
    {
        "name": "get_syllabus",
        "description": "Read a course's syllabus — the description, policies, grading, "
        "exam format/dates, and schedule. Often where the real details live (e.g. how "
        "an exam works, what's allowed, late policy). Pass the course name, code, or "
        "nickname, e.g. 'PHIL 149' or '163'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course name, code, or nickname (e.g. 'PHIL 149', '163').",
                }
            },
            "required": ["course"],
        },
    },
    {
        "name": "get_grade_breakdown",
        "description": "Get a course's grade breakdown: each assignment GROUP, its WEIGHT "
        "(% of the final grade), and the student's score on every assignment (including "
        "what's still ungraded). Use this for 'what do I need on the final to get a 3.5 / "
        "90%', 'how is my grade weighted', or any target-grade math. Pass the course name "
        "or code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Course name, code, or nickname."},
            },
            "required": ["course"],
        },
    },
    {
        "name": "get_calendar",
        "description": "Read scheduled course calendar events (exams, review sessions, "
        "special meetings) in the next couple of weeks. Finals and exams are often on "
        "the calendar even when they aren't a submittable assignment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 14).",
                }
            },
        },
    },
]


# One saved lecture fits Claude's context, so these return the whole transcript.
_LECTURE_MAX = 40000

LECTURE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_lectures",
        "description": "List the lectures the student has saved (id + title). Use this "
        "when they mention 'my lecture', 'the lecture I added', or want to study from a "
        "lecture but didn't say which. If none are saved, tell them to tap the menu (⋯) "
        "-> 'Add lecture' and paste the Panopto transcript (or upload the recording).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_lecture",
        "description": "Get a saved lecture's full transcript to answer a question about "
        "it. Accepts the lecture id from list_lectures, or a loose title like 'bio lecture'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id_or_title": {
                    "type": "string",
                    "description": "Lecture id (from list_lectures) or a fuzzy title.",
                }
            },
            "required": ["id_or_title"],
        },
    },
]


class ToolBox:
    def __init__(
        self,
        canvas,
        reminders=None,
        study=None,
        documents=None,
        lectures=None,
        now: Callable[[], datetime] | None = None,
    ):
        self.canvas = canvas
        self.reminders = reminders
        self.study = study
        self.documents = documents
        self.lectures = lectures
        self._now = now or (lambda: datetime.now(timezone.utc))

    # --- schema assembly -----------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        schemas = list(CANVAS_TOOLS)
        if self.reminders is not None:
            schemas += self.reminders.schemas()
        if self.study is not None:
            schemas += self.study.schemas()
        if self.documents is not None:
            schemas += self.documents.schemas()
        if self.lectures is not None:
            schemas += LECTURE_TOOLS
        return schemas

    # --- dispatch ------------------------------------------------------------

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        handler = {
            "get_courses": self._get_courses,
            "get_upcoming": self._get_upcoming,
            "get_assignment_detail": self._get_assignment_detail,
            "search_assignments": self._search_assignments,
            "canvas_api": self._canvas_api,
            "get_announcements": self._get_announcements,
            "check_inbox": self._check_inbox,
            "get_calendar": self._get_calendar,
            "get_syllabus": self._get_syllabus,
            "get_grades": self._get_grades,
            "get_course_grades": self._get_course_grades,
            "get_grade_breakdown": self._get_grade_breakdown,
            "get_submission": self._get_submission,
            "list_lectures": self._list_lectures,
            "get_lecture": self._get_lecture,
        }.get(name)

        if handler is None:
            # Reminder / study services own their tool names.
            if self.reminders is not None and name in self.reminders.tool_names():
                handler = lambda i: self.reminders.dispatch(name, i)
            elif self.study is not None and name in self.study.tool_names():
                handler = lambda i: self.study.dispatch(name, i)
            elif self.documents is not None and name in self.documents.tool_names():
                handler = lambda i: self.documents.dispatch(name, i)

        if handler is None:
            return f"(unknown tool: {name})"

        try:
            return handler(tool_input)
        except Exception as exc:  # one bad call shouldn't crash the reply
            return f"(couldn't do that — error: {exc})"

    # --- Canvas handlers -----------------------------------------------------

    def _get_courses(self, _: dict) -> str:
        courses: list[Course] = self.canvas.list_courses()
        # Only real classes (department + 3-digit number); drop resource sites etc.
        courses = [c for c in courses if is_real_class(c.code)]
        if not courses:
            return "No active courses found."
        return "\n".join(f"[{c.id}] {short_course_code(c.code)}: {c.name}" for c in courses)

    def _get_upcoming(self, tool_input: dict) -> str:
        days = int(tool_input.get("days") or 7)
        items: list[Item] = self.canvas.get_upcoming(days=days)
        if not items:
            return f"Nothing due in the next {days} days."
        return "\n".join(self._format_item(i) for i in items)

    def _get_assignment_detail(self, tool_input: dict) -> str:
        ref = tool_input["ref"]
        d: AssignmentDetail = self.canvas.get_assignment_detail(ref)
        due = timefmt.human_due(d.due_at, now=self._now())
        pts = f", {d.points:g} pts" if d.points is not None else ""
        header = f"{d.name} ({d.course}) — due {due}{pts}"
        link = f"\nlink: {d.html_url}" if d.html_url else ""
        body = d.description or "(no description provided)"
        return f"{header}{link}\n{body}"

    def _canvas_api(self, tool_input: dict) -> str:
        import json

        path = tool_input.get("path", "")
        params = tool_input.get("params") or {}
        try:
            data = self.canvas.canvas_get(path, params)
        except Exception as exc:
            return f"(canvas error for {path!r}: {exc})"
        text = json.dumps(data, default=str, ensure_ascii=False)
        if len(text) > 6000:
            text = text[:6000] + "… (truncated — narrow the path or use a smaller per_page)"
        return text

    def _search_assignments(self, tool_input: dict) -> str:
        items: list[Item] = self.canvas.search_assignments(
            tool_input["query"], course_id=tool_input.get("course_id")
        )
        if not items:
            return "No matching assignments found."
        return "\n".join(self._format_item(i) for i in items)

    def _get_announcements(self, tool_input: dict) -> str:
        days = int(tool_input.get("days_back") or 14)
        anns = self.canvas.get_announcements(days_back=days)
        if not anns:
            return f"No announcements in the last {days} days."
        return "\n\n".join(self._format_announcement(a) for a in anns)

    def _check_inbox(self, tool_input: dict) -> str:
        limit = int(tool_input.get("limit") or 20)
        msgs = self.canvas.get_inbox(limit=limit)
        if not msgs:
            return "Inbox is empty."
        return "\n\n".join(self._format_inbox(m) for m in msgs)

    def _get_submission(self, tool_input: dict) -> str:
        s = self.canvas.get_submission(
            tool_input.get("course", ""), tool_input.get("assignment", "")
        )
        if s is None:
            return "Couldn't find that assignment."
        head = s.assignment
        if s.score is not None and s.points:
            head += f" — {s.score:g}/{s.points:g}"
        elif s.state:
            head += f" — {s.state}"
        parts = [head]
        for author, text in s.comments:
            parts.append(f"feedback from {author}: {text}")
        if s.text:
            body = s.text if len(s.text) <= 4000 else s.text[:4000] + "…"
            parts.append("what they submitted:\n" + body)
        elif s.attachments:
            parts.append("submitted file(s): " + ", ".join(s.attachments))
        else:
            parts.append("nothing submitted yet.")
        return "\n\n".join(parts)

    def _get_grades(self, _: dict) -> str:
        grades = self.canvas.get_grades()
        # Only real classes count toward grades; skip resource sites and the like.
        grades = [g for g in grades if is_real_class(g.course)]
        if not grades:
            return "No grades posted yet."
        lines = []
        for g in grades:
            score = f"{g.score:g}%" if g.score is not None else "-"
            letter = f" ({g.grade})" if g.grade else ""
            lines.append(f"{short_course_code(g.course)}: {score}{letter}")
        return "\n".join(lines)

    def _get_course_grades(self, tool_input: dict) -> str:
        items = self.canvas.get_course_grades(tool_input.get("course", ""))
        if not items:
            return "No assignments/grades found for that course."
        lines = []
        for it in items:
            pts = f"{it.points:g}" if it.points is not None else "?"
            if it.score is None:
                lines.append(f"{it.name}: —/{pts} (NOT DONE)")
            elif it.points and it.score >= it.points:
                lines.append(f"{it.name}: {it.score:g}/{pts} (done)")
            else:
                lines.append(f"{it.name}: {it.score:g}/{pts} (partial)")
        return "\n".join(lines)

    def _get_grade_breakdown(self, tool_input: dict) -> str:
        groups = self.canvas.get_grade_breakdown(tool_input.get("course", ""))
        if not groups:
            return "No grade breakdown found for that course."
        lines = []
        for g in groups:
            weight = f" ({g.weight:g}% of grade)" if g.weight else ""
            lines.append(f"{g.name}{weight}:")
            for name, score, pts in g.items:
                s = f"{score:g}" if score is not None else "ungraded"
                p = f"{pts:g}" if pts is not None else "?"
                lines.append(f"  - {name}: {s}/{p}")
        return "\n".join(lines)

    def _get_syllabus(self, tool_input: dict) -> str:
        course = tool_input.get("course", "")
        text = self.canvas.get_syllabus(course)
        if not text:
            return f"No syllabus found for {course!r}."
        if len(text) > 2500:
            text = text[:2500] + "…"
        get_url = getattr(self.canvas, "syllabus_url", None)
        url = get_url(course) if get_url else ""
        if url:
            text += f"\nlink: {url}"
        return text

    def _get_calendar(self, tool_input: dict) -> str:
        days = int(tool_input.get("days_ahead") or 14)
        events = self.canvas.get_calendar_events(days_ahead=days)
        if not events:
            return f"No calendar events in the next {days} days."
        return "\n".join(self._format_event(e) for e in events)

    def _format_item(self, item: Item) -> str:
        due = timefmt.human_due(item.due_at, now=self._now())
        ref = f" [{item.ref}]" if item.ref else ""
        link = f" {item.html_url}" if item.html_url else ""
        return f"{item.course} — {item.title} — due {due}{ref}{link}"

    def _format_event(self, e) -> str:
        when = timefmt.human_due(e.start_at, now=self._now())
        loc = f" @ {e.location}" if e.location else ""
        course = f"{e.course} — " if e.course else ""
        return f"{course}{e.title} — {when}{loc}"

    def _format_announcement(self, a) -> str:
        when = timefmt.human_when(a.posted_at, now=self._now())
        body = (a.text or "").strip()
        if len(body) > 700:
            body = body[:700] + "…"
        course = f"[{a.course}] " if a.course else ""
        return f"{course}{a.title} (posted {when})\n{body}"

    def _format_inbox(self, m) -> str:
        when = timefmt.human_when(m.sent_at, now=self._now())
        body = (m.snippet or "").strip()
        if len(body) > 1500:
            body = body[:1500] + "…"
        course = f"[{m.course}] " if m.course else ""
        return f"{course}{m.subject} ({when})\n{body}"

    # --- Lecture handlers ----------------------------------------------------

    def _list_lectures(self, _: dict) -> str:
        items = self.lectures.list() if self.lectures is not None else []
        if not items:
            return ("No lectures saved yet. Tell the student to tap the menu (⋯) -> "
                    "'Add lecture' and paste the Panopto transcript (or upload the recording).")
        return "\n".join(f"[{it['id']}] {it['title']}" for it in items)

    def _get_lecture(self, tool_input: dict) -> str:
        key = (tool_input.get("id_or_title") or "").strip()
        if not key or self.lectures is None:
            return "No lecture specified. Use list_lectures to see saved lectures."
        hit = self.lectures.get(key)
        if hit:
            title, transcript = hit
        else:
            found = self.lectures.find_by_title(key)
            if not found:
                return f"No saved lecture matching '{key}'. Use list_lectures to see what's saved."
            _id, title, transcript = found
        text = transcript if len(transcript) <= _LECTURE_MAX else transcript[:_LECTURE_MAX] + "…"
        return f"Lecture: {title}\n\n{text}"
