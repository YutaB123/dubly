"""The Canvas connection.

Fetches your real classes, assignments, due dates, and assignment details from
UW Canvas using your token. Returns simple data objects; formatting for the
texts happens elsewhere (in the tools layer).
"""

from __future__ import annotations

import html as html_module
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

# How long fetched Canvas data stays fresh. Keeps follow-up questions in a
# conversation snappy (no repeat round-trips) without serving stale due dates.
CACHE_TTL_SECONDS = 120


# --- What counts as a "class" ------------------------------------------------

# A real class has a department code + a 3-digit number: PHIL 149, MATH 124,
# CSE 163. Resource sites, career guides, placement pages, and similar have no
# such code and are NOT classes — they must never be listed, counted, or graded.
_CLASS_CODE_RE = re.compile(r"[A-Z&]{2,}\s?\d{3}")


def short_course_code(code: str) -> str:
    """'CSE 163 A Sp 26' -> 'CSE 163'. Falls back to the trimmed input if no match."""
    m = _CLASS_CODE_RE.search(code or "")
    return m.group(0) if m else (code or "").strip()


def is_real_class(code: str) -> bool:
    """True only for real classes (department + 3-digit number), never archived ones."""
    text = code or ""
    if "archived" in text.lower():
        return False
    return bool(_CLASS_CODE_RE.search(text))


# --- Data shapes -------------------------------------------------------------

@dataclass(frozen=True)
class Course:
    id: int
    name: str
    code: str


@dataclass(frozen=True)
class Item:
    """An upcoming assignment / thing with a due date."""

    course: str
    title: str
    due_at: datetime | None
    ref: str  # "courseId:assignmentId", passed back to get_assignment_detail
    html_url: str
    type: str


@dataclass(frozen=True)
class AssignmentDetail:
    name: str
    course: str
    due_at: datetime | None
    points: float | None
    description: str
    html_url: str


@dataclass(frozen=True)
class Announcement:
    """A course announcement (often where exam logistics get posted)."""

    course: str
    title: str
    text: str
    posted_at: datetime | None


@dataclass(frozen=True)
class InboxMessage:
    """A recent Canvas inbox conversation (message from an instructor/TA)."""

    course: str
    subject: str
    snippet: str
    sent_at: datetime | None


@dataclass(frozen=True)
class CalendarEvent:
    """A scheduled course calendar event (exams, review sessions, meetings)."""

    course: str
    title: str
    start_at: datetime | None
    location: str
    description: str


@dataclass(frozen=True)
class Grade:
    """The student's current standing in a course."""

    course: str
    score: float | None   # current percentage so far
    grade: str | None      # letter grade, if the course shows one


@dataclass(frozen=True)
class AssignmentScore:
    """The student's score on one assignment within a course."""

    name: str
    score: float | None    # None = not submitted/graded yet
    points: float | None   # points possible


@dataclass(frozen=True)
class Submission:
    """What the student turned in for an assignment, plus feedback."""

    assignment: str
    state: str
    score: float | None
    points: float | None
    text: str                  # the submitted text (incl. extracted file content)
    comments: list             # list of (author, comment_text)
    attachments: list          # filenames


# --- HTML -> plain text ------------------------------------------------------

_BLOCK_BREAK = re.compile(r"(?i)</(p|div|h[1-6]|li|tr)>")
_LIST_ITEM = re.compile(r"(?i)<li[^>]*>")
_BR = re.compile(r"(?i)<br\s*/?>")
_TAG = re.compile(r"<[^>]+>")


def html_to_text(html: str | None) -> str:
    """Turn Canvas's HTML descriptions into clean text for the model."""
    if not html:
        return ""
    text = html
    # Each list item starts on its own line.
    text = _LIST_ITEM.sub("\n", text)
    # Block-level closes and <br> become line breaks.
    text = _BLOCK_BREAK.sub("\n", text)
    text = _BR.sub("\n", text)
    # Drop all remaining tags.
    text = _TAG.sub("", text)
    # Decode HTML entities (&amp; -> &).
    text = html_module.unescape(text)
    # Collapse runs of spaces/tabs; trim each line; drop blank lines.
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


# --- Date parsing ------------------------------------------------------------

def parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    # Canvas returns ISO 8601 in UTC, e.g. "2026-06-10T06:59:00Z".
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_done(planner_entry: dict) -> bool:
    """Has the student already finished this planner item?

    True if they submitted/were graded/excused, or manually checked it off
    (or dismissed it) in the Canvas planner.
    """
    override = planner_entry.get("planner_override") or {}
    if override.get("marked_complete") or override.get("dismissed"):
        return True
    subs = planner_entry.get("submissions")
    if isinstance(subs, dict):
        if subs.get("submitted") or subs.get("graded") or subs.get("excused"):
            return True
    return False


# --- The client --------------------------------------------------------------

class CanvasClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        # The student-facing web origin (no "/api/v1"), used to build clickable
        # Canvas page links the assistant can hand back, e.g. a syllabus page.
        self.web_base = self.base_url.removesuffix("/api/v1")
        self.token = token
        self._http = http or httpx.Client(base_url=self.base_url, timeout=20.0)
        self._cache: dict = {}

    def course_web_url(self, course_id) -> str:
        """The student-facing Canvas URL for a course's home page."""
        return f"{self.web_base}/courses/{course_id}"

    def syllabus_url(self, course: str) -> str:
        """The student-facing Canvas URL for a course's syllabus page (or "")."""
        cid = self._resolve_course_id(course)
        if cid is None:
            return ""
        return f"{self.web_base}/courses/{cid}/assignments/syllabus"

    def _cached(self, key, fetch):
        """Return a recent cached value for `key`, or fetch and store it."""
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
        value = fetch()
        self._cache[key] = (now, value)
        return value

    # Low-level GET with pagination (Canvas uses Link headers).
    def _get(self, path: str, params: dict | None = None) -> list | dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        results: list = []
        url: str | None = path
        merged_params = dict(params or {})
        merged_params.setdefault("per_page", 100)

        pages = 0
        while url and pages < 20:
            resp = self._http.get(url, params=merged_params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return data  # single object, no pagination
            results.extend(data)
            # Follow the "next" link if present.
            url = resp.links.get("next", {}).get("url")
            merged_params = {}  # the next URL already carries its params
            pages += 1
        return results

    def canvas_get(self, path: str, params: dict | None = None) -> list | dict:
        """Read-only GET against any Canvas API v1 path — the catch-all that lets
        the assistant answer questions the specific tools don't cover. Single page
        (bounded); the caller can paginate via a 'page' param."""
        if not path.startswith("/"):
            path = "/" + path
        headers = {"Authorization": f"Bearer {self.token}"}
        p = dict(params or {})
        p.setdefault("per_page", 30)
        resp = self._http.get(path, params=p, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def list_courses(self) -> list[Course]:
        def _fetch() -> list[Course]:
            # Include completed enrollments too: near a quarter's end, current
            # classes flip from "active" to "completed" in Canvas. We then keep
            # only THIS term's real classes (drops last quarter + resource sites).
            raw = self._get(
                "/courses",
                {
                    "enrollment_state[]": ["active", "completed"],
                    "include[]": "term",
                    "per_page": 100,
                },
            )
            real: list[tuple[dict, str]] = []
            for c in raw:
                name = c.get("name")
                if not name:
                    continue  # restricted/empty stub
                code = c.get("course_code") or name
                if not is_real_class(code):
                    continue  # resource site / guide / archived — not a class
                term = c.get("term") or {}
                real.append((c, term.get("start_at") or ""))
            if not real:
                return []
            # Keep only the most recent term (ISO dates sort correctly; "" sorts low).
            latest = max(start for _, start in real)
            keep = [c for c, start in real if start == latest] if latest else [c for c, _ in real]
            return [
                Course(id=c["id"], name=c["name"], code=c.get("course_code") or c["name"])
                for c in keep
            ]

        return self._cached("courses", _fetch)

    def _resolve_course_id(self, course: str) -> int | None:
        """Map a course id, code, nickname, or name to a real course id."""
        course = (course or "").strip()
        if course.isdigit():
            return int(course)
        q = course.lower()
        for c in self.list_courses():
            if q and (q in c.code.lower() or q in c.name.lower()):
                return c.id
        return None

    def get_syllabus(self, course: str) -> str:
        """The course syllabus text (description, policies, exam info, schedule)."""
        cid = self._resolve_course_id(course)
        if cid is None:
            return ""
        return self._cached(("syllabus", cid), lambda: self._fetch_syllabus(cid))

    def _fetch_syllabus(self, cid: int) -> str:
        data = self._get(f"/courses/{cid}", {"include[]": "syllabus_body"})
        if not isinstance(data, dict):
            return ""
        return html_to_text(data.get("syllabus_body"))

    def get_study_material(self, course: str) -> tuple[str, str]:
        """Gather a course-wide 'study packet' for exam prep: syllabus + the topic
        outline (modules and their items) + the assignment list. Returns
        (course_label, source_text); source_text is length-budgeted. This is the
        source for flashcards / practice exams, so a 'final exam' deck reflects the
        whole course rather than one thin assignment description."""
        cid = self._resolve_course_id(course)
        if cid is None:
            return course, ""
        label = next((c.code for c in self.list_courses() if c.id == cid), course)
        material = self._cached(
            ("study_material", cid), lambda: self._build_study_material(cid)
        )
        return label, material

    def _build_study_material(self, cid: int) -> str:
        parts: list[str] = []

        syllabus = self._fetch_syllabus(cid)
        if syllabus:
            parts.append("## Syllabus\n" + syllabus[:4000])

        try:
            modules = self.canvas_get(
                f"/courses/{cid}/modules", {"include[]": "items", "per_page": 50}
            )
        except Exception:
            modules = []
        topic_lines: list[str] = []
        if isinstance(modules, list):
            for m in modules:
                mname = (m.get("name") or "").strip()
                if mname:
                    topic_lines.append(f"- {mname}")
                for it in m.get("items") or []:
                    title = (it.get("title") or "").strip()
                    if title:
                        topic_lines.append(f"  - {title}")
        if topic_lines:
            parts.append("## Topic outline (course modules)\n" + "\n".join(topic_lines[:200]))

        try:
            assignments = self.canvas_get(
                f"/courses/{cid}/assignments", {"per_page": 100}
            )
        except Exception:
            assignments = []
        a_lines: list[str] = []
        if isinstance(assignments, list):
            for a in assignments:
                an = (a.get("name") or "").strip()
                if not an:
                    continue
                desc = " ".join(html_to_text(a.get("description") or "").split())[:200]
                a_lines.append(f"- {an}" + (f": {desc}" if desc else ""))
        if a_lines:
            parts.append("## Assignments\n" + "\n".join(a_lines[:120]))

        return "\n\n".join(parts)[:12000]

    def get_grades(self) -> list[Grade]:
        """The student's current grade in each graded course."""
        return self._cached("grades", self._fetch_grades)

    def _fetch_grades(self) -> list[Grade]:
        codes = {c.id: c.code for c in self.list_courses()}
        # Active + completed: a just-finished course still has a grade the student
        # wants. Non-current courses get filtered out by the code map below.
        raw = self._get(
            "/users/self/enrollments",
            {"type[]": "StudentEnrollment", "state[]": ["active", "completed"]},
        )
        out: list[Grade] = []
        for e in raw:
            g = e.get("grades") or {}
            score = g.get("current_score")
            grade = g.get("current_grade")
            if score is None and not grade:
                continue  # no grade in this course (e.g. resource sites)
            cid = e.get("course_id")
            out.append(Grade(course=codes.get(cid, str(cid)), score=score, grade=grade))
        return out

    def _download(self, url: str) -> tuple[bytes, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = self._http.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "")

    def _attachment_text(self, filename: str, content_type: str, data: bytes) -> str:
        """Pull readable text out of a submitted file (docx / pdf / txt / html)."""
        # Shared with the web-upload path; imported lazily to avoid a circular import
        # (app.attachments imports html_to_text from this module).
        from app.attachments import extract_text

        return extract_text(filename, content_type, data)

    def get_submission(self, course: str, assignment: str) -> Submission | None:
        """Read what the student submitted for an assignment, plus feedback."""
        cid = self._resolve_course_id(course)
        if cid is None:
            return None
        raw = self._get(
            f"/courses/{cid}/assignments", {"search_term": assignment, "per_page": 30}
        )
        if not isinstance(raw, list) or not raw:
            raw = self._get(f"/courses/{cid}/assignments", {"per_page": 100})
        q = (assignment or "").lower()
        match = next((a for a in raw if q and q in (a.get("name") or "").lower()), None)
        if match is None and raw:
            match = raw[0]
        if match is None:
            return None
        aid = match["id"]
        sub = self._get(
            f"/courses/{cid}/assignments/{aid}/submissions/self",
            {"include[]": "submission_comments"},
        )
        text = html_to_text(sub.get("body")) if sub.get("body") else ""
        for att in sub.get("attachments") or []:
            if not att.get("url"):
                continue
            try:
                data, ct = self._download(att["url"])
                extracted = self._attachment_text(
                    att.get("filename", ""), att.get("content-type") or ct, data
                )
                if extracted:
                    text += ("\n\n" if text else "") + extracted
            except Exception:
                pass
        comments = [
            (
                (cm.get("author") or {}).get("display_name") or "instructor",
                html_to_text(cm.get("comment")),
            )
            for cm in (sub.get("submission_comments") or [])
        ]
        return Submission(
            assignment=match.get("name") or "(assignment)",
            state=sub.get("workflow_state") or "",
            score=sub.get("score"),
            points=match.get("points_possible"),
            text=text.strip(),
            comments=comments,
            attachments=[a.get("filename") for a in (sub.get("attachments") or [])],
        )

    def get_course_grades(self, course: str) -> list[AssignmentScore]:
        """Every assignment in one course with the student's score on it.

        Useful for 'which assignments/standards have I done or still need' — e.g.
        MATH 124's curriculum standards (1 point each, scored 1 when credited).
        """
        cid = self._resolve_course_id(course)
        if cid is None:
            return []
        return self._cached(
            ("coursegrades", cid), lambda: self._fetch_course_grades(cid)
        )

    def _fetch_course_grades(self, cid: int) -> list[AssignmentScore]:
        raw = self._get(
            f"/courses/{cid}/assignments", {"include[]": "submission"}
        )
        out: list[AssignmentScore] = []
        for a in raw:
            sub = a.get("submission") or {}
            out.append(
                AssignmentScore(
                    name=a.get("name") or "(untitled)",
                    score=sub.get("score"),
                    points=a.get("points_possible"),
                )
            )
        return out

    def get_upcoming(self, days: int = 7) -> list[Item]:
        return self._cached(("upcoming", days), lambda: self._fetch_upcoming(days))

    def _fetch_upcoming(self, days: int) -> list[Item]:
        from datetime import timedelta

        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        raw = self._get(
            "/planner/items",
            {
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
            },
        )
        items: list[Item] = []
        for entry in raw:
            plannable = entry.get("plannable") or {}
            due = parse_due(plannable.get("due_at") or entry.get("plannable_date"))
            if due is None:
                continue
            if is_done(entry):
                continue  # already submitted / graded / checked off — skip it
            course_id = entry.get("course_id")
            assignment_id = plannable.get("id")
            ref = f"{course_id}:{assignment_id}" if course_id and assignment_id else ""
            items.append(
                Item(
                    course=entry.get("context_name") or "",
                    title=plannable.get("title") or "(untitled)",
                    due_at=due,
                    ref=ref,
                    html_url=entry.get("html_url") or "",
                    type=entry.get("plannable_type") or "item",
                )
            )
        items.sort(key=lambda i: i.due_at or datetime.max.replace(tzinfo=timezone.utc))
        return items

    def get_assignment_detail(self, ref: str) -> AssignmentDetail:
        try:
            course_id, assignment_id = ref.split(":")
        except ValueError as exc:
            raise ValueError(
                f"Bad assignment ref {ref!r}; expected 'courseId:assignmentId'"
            ) from exc
        data = self._get(f"/courses/{course_id}/assignments/{assignment_id}")
        return AssignmentDetail(
            name=data.get("name") or "(untitled)",
            course=str(course_id),
            due_at=parse_due(data.get("due_at")),
            points=data.get("points_possible"),
            description=html_to_text(data.get("description")),
            html_url=data.get("html_url") or "",
        )

    def get_announcements(self, days_back: int = 14) -> list[Announcement]:
        """Recent announcements across the student's active courses, newest first."""
        return self._cached(
            ("announcements", days_back), lambda: self._fetch_announcements(days_back)
        )

    def _fetch_announcements(self, days_back: int) -> list[Announcement]:
        from datetime import timedelta

        courses = self.list_courses()
        if not courses:
            return []
        code_by_ctx = {f"course_{c.id}": c.code for c in courses}
        start = datetime.now(timezone.utc) - timedelta(days=days_back)
        # httpx encodes a list value as repeated query keys (context_codes[]=...).
        raw = self._get(
            "/announcements",
            {
                "context_codes[]": [f"course_{c.id}" for c in courses],
                "start_date": start.date().isoformat(),
            },
        )
        out: list[Announcement] = []
        for a in raw:
            ctx = a.get("context_code") or ""
            out.append(
                Announcement(
                    course=code_by_ctx.get(ctx, ctx),
                    title=a.get("title") or "(untitled)",
                    text=html_to_text(a.get("message")),
                    posted_at=parse_due(a.get("posted_at") or a.get("created_at")),
                )
            )
        out.sort(
            key=lambda x: x.posted_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return out

    def get_calendar_events(self, days_ahead: int = 14) -> list[CalendarEvent]:
        """Scheduled calendar events across active courses, soonest first.

        Finals/exams are often posted here as events even when they aren't a
        submittable assignment.
        """
        return self._cached(
            ("calendar", days_ahead), lambda: self._fetch_calendar(days_ahead)
        )

    def _fetch_calendar(self, days_ahead: int) -> list[CalendarEvent]:
        from datetime import timedelta

        courses = self.list_courses()
        if not courses:
            return []
        code_by_ctx = {f"course_{c.id}": c.code for c in courses}
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days_ahead)
        raw = self._get(
            "/calendar_events",
            {
                "type": "event",
                "context_codes[]": [f"course_{c.id}" for c in courses],
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
            },
        )
        out: list[CalendarEvent] = []
        for e in raw:
            ctx = e.get("context_code") or ""
            out.append(
                CalendarEvent(
                    course=code_by_ctx.get(ctx, ctx),
                    title=e.get("title") or "(untitled)",
                    start_at=parse_due(e.get("start_at")),
                    location=e.get("location_name") or "",
                    description=html_to_text(e.get("description")),
                )
            )
        out.sort(
            key=lambda x: x.start_at or datetime.max.replace(tzinfo=timezone.utc)
        )
        return out

    def get_inbox(self, limit: int = 20) -> list[InboxMessage]:
        """The student's most recent Canvas inbox conversations."""
        return self._cached(("inbox", limit), lambda: self._fetch_inbox(limit))

    def _fetch_inbox(self, limit: int) -> list[InboxMessage]:
        raw = self._get("/conversations", {"per_page": limit})[:limit]
        if not raw:
            return []

        def to_msg(cv: dict) -> InboxMessage:
            # The list endpoint only returns a ~100-char preview of last_message.
            # Fetch the full thread so details (location, "closed book", etc.) survive.
            body = cv.get("last_message")
            try:
                detail = self._get(f"/conversations/{cv['id']}")
                msgs = detail.get("messages") if isinstance(detail, dict) else None
                if msgs and msgs[0].get("body"):
                    body = msgs[0]["body"]
            except Exception:
                pass  # fall back to the preview if the detail fetch fails
            return InboxMessage(
                course=cv.get("context_name") or "",
                subject=cv.get("subject") or "(no subject)",
                snippet=html_to_text(body),
                sent_at=parse_due(cv.get("last_message_at")),
            )

        # Fetch the full bodies concurrently so it stays fast.
        with ThreadPoolExecutor(max_workers=min(8, len(raw))) as ex:
            return list(ex.map(to_msg, raw))

    def search_assignments(self, query: str, course_id: int | None = None) -> list[Item]:
        """Find assignments by name across courses (or within one course)."""
        if course_id is not None:
            course_ids = [course_id]
            course_names = {course_id: str(course_id)}
        else:
            courses = self.list_courses()
            course_ids = [c.id for c in courses]
            course_names = {c.id: c.code for c in courses}

        q = query.lower().strip()
        found: list[Item] = []
        for cid in course_ids:
            raw = self._get(f"/courses/{cid}/assignments", {"search_term": query})
            for a in raw:
                name = a.get("name") or ""
                if q and q not in name.lower():
                    continue
                found.append(
                    Item(
                        course=course_names.get(cid, str(cid)),
                        title=name,
                        due_at=parse_due(a.get("due_at")),
                        ref=f"{cid}:{a['id']}",
                        html_url=a.get("html_url") or "",
                        type="assignment",
                    )
                )
        return found
