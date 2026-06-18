"""A fake Canvas client for the public demo.

When DEMO_MODE is on, the app uses this instead of the real CanvasClient, so the
"Try it live" link on a portfolio shows a believable sample student's classes,
grades, due dates, and syllabi — never anyone's real Canvas data. Quizzes,
flashcards, and documents still generate for real (Claude works off the fake
course material below).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.canvas import (
    AssignmentDetail,
    AssignmentScore,
    Announcement,
    CalendarEvent,
    CanvasClient,
    Course,
    Grade,
    GradeGroup,
    InboxMessage,
    Item,
    Submission,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in(days: float, hour: int = 17) -> datetime:
    d = _now() + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


# --- Sample student: four real-sounding UW intro courses (not anyone's actual schedule) ---

_COURSES = [
    Course(id=9101, name="Introduction to Computer Programming I", code="CSE 142 A Sp 26"),
    Course(id=9102, name="Calculus with Analytic Geometry III", code="MATH 126 B Sp 26"),
    Course(id=9103, name="Introduction to Psychology", code="PSYCH 101 A Sp 26"),
    Course(id=9104, name="Composition: Exposition", code="ENGL 131 C Sp 26"),
]

# Per-course content keyed by department code, used for grades, syllabi, and study material.
_DATA = {
    "CSE 142": {
        "score": 93.5, "grade": "A",
        "groups": [
            ("Programming Assignments", 50.0, [("Assignment 3: ArrayList", 19.0, 20.0),
                                               ("Assignment 4: Critters", 18.5, 20.0)]),
            ("Quizzes", 20.0, [("Quiz 3: Loops", 9.0, 10.0)]),
            ("Exams", 30.0, [("Midterm", 86.0, 100.0)]),
        ],
        "upcoming": [("Assignment 5: Strings", 2.0), ("Quiz 4: Methods", 5.0)],
        "syllabus": ("CSE 142 introduces programming in Java: variables, expressions, "
                     "loops, methods and parameters, conditionals, strings, arrays, and "
                     "ArrayLists. Grading: 50% programming assignments, 20% quizzes, "
                     "30% exams (a midterm and a final). Late work loses 10% per day, "
                     "with two free late days for the quarter."),
        "topics": ("Java basics; print/println; variables and types (int, double, "
                   "boolean, String); arithmetic and operator precedence; for and while "
                   "loops; nested loops and figures; methods, parameters, and return "
                   "values; if/else and boolean logic; Scanner and user input; String "
                   "methods (charAt, substring, indexOf); arrays and array traversal; "
                   "ArrayList add/remove/get; reference semantics."),
    },
    "MATH 126": {
        "score": 88.2, "grade": None,
        "groups": [
            ("Weekly Homework", 25.0, [("Homework 5", 18.0, 20.0), ("Homework 6", 19.0, 20.0)]),
            ("WebAssign", 15.0, [("WebAssign 5", 28.0, 30.0)]),
            ("Exams", 60.0, [("Midterm 1", 82.0, 100.0)]),
        ],
        "upcoming": [("Weekly Homework 7", 4.0), ("WebAssign 6", 1.0)],
        "syllabus": ("MATH 126 covers multivariable and vector calculus: parametric "
                     "equations, polar coordinates, vectors and the geometry of space, "
                     "Taylor series, and partial derivatives. Grading: 25% written "
                     "homework, 15% WebAssign, two midterms and a cumulative final "
                     "(60% combined)."),
        "topics": ("Parametric curves and their derivatives; polar coordinates and "
                   "area; vectors, dot and cross products; lines and planes in space; "
                   "Taylor and Maclaurin series; convergence tests; partial derivatives; "
                   "gradient and directional derivatives."),
    },
    "PSYCH 101": {
        "score": 91.0, "grade": "A-",
        "groups": [
            ("Reading Quizzes", 20.0, [("Reading Quiz 4", 9.0, 10.0), ("Reading Quiz 5", 10.0, 10.0)]),
            ("Exams", 60.0, [("Exam 1", 88.0, 100.0)]),
            ("Participation", 20.0, [("Discussion 4", 10.0, 10.0)]),
        ],
        "upcoming": [("Reading Quiz 6: Memory", 1.0), ("Exam 2 review", 6.0)],
        "syllabus": ("PSYCH 101 surveys the science of mind and behavior: research "
                     "methods, the brain and neurons, learning, memory, cognition, "
                     "development, and social psychology. Grading: 20% reading quizzes, "
                     "60% three exams, 20% participation."),
        "topics": ("Research methods and the experimental design; neurons, action "
                   "potentials, and neurotransmitters; classical conditioning (Pavlov); "
                   "operant conditioning (reinforcement and punishment); encoding, "
                   "storage, and retrieval in memory; the forgetting curve; cognition "
                   "and heuristics; Piaget's stages; social influence, conformity, and "
                   "the fundamental attribution error."),
    },
    "ENGL 131": {
        "score": 95.0, "grade": None,
        "groups": [
            ("Major Papers", 60.0, [("Major Paper 1", 47.0, 50.0)]),
            ("Short Assignments", 30.0, [("Reading Response 4", 10.0, 10.0)]),
            ("Participation", 10.0, [("Peer Review 2", 10.0, 10.0)]),
        ],
        "upcoming": [("Major Paper 2: draft", 6.0), ("Reading Response 5", 3.0)],
        "syllabus": ("ENGL 131 builds academic writing through a portfolio of essays "
                     "emphasizing argument, analysis, and revision. You will draft and "
                     "revise two major papers and complete short reading responses. "
                     "Grading is portfolio-based: 60% major papers, 30% short "
                     "assignments, 10% participation."),
        "topics": ("Thesis and claim construction; rhetorical analysis (ethos, pathos, "
                   "logos); using and citing evidence (MLA); counterargument and "
                   "rebuttal; paragraph cohesion; revision strategies; the writing "
                   "process from draft to portfolio."),
    },
}


class DemoCanvasClient(CanvasClient):
    """Duck-type of CanvasClient that serves the sample data above and never
    touches the network."""

    def __init__(self, base_url: str = "https://canvas.uw.edu/api/v1", token: str = "demo"):
        # Sets base_url / web_base / cache; the inherited URL helpers still work.
        super().__init__(base_url, token)

    # --- identity & courses ---
    def get_user_name(self) -> str:
        return ""  # generic greeting ("hey 🐾"), no fake personal name

    def list_courses(self) -> list[Course]:
        return list(_COURSES)

    def _resolve_course_id(self, course: str):
        q = re.sub(r"[^a-z0-9]", "", (course or "").lower())
        if not q:
            return None
        for c in _COURSES:
            code = re.sub(r"[^a-z0-9]", "", c.code.lower())
            name = re.sub(r"[^a-z0-9]", "", c.name.lower())
            dept = re.sub(r"[^a-z]", "", c.code.lower())[:4]  # e.g. "csea" -> "cse"
            if q in code or q in name or code.startswith(q) or (len(q) >= 3 and q[:4] in dept):
                return c.id
        return None

    def _key_for(self, course: str) -> str | None:
        cid = self._resolve_course_id(course)
        c = next((c for c in _COURSES if c.id == cid), None)
        if not c:
            return None
        m = re.match(r"[A-Z]+ \d+", c.code)
        return m.group(0) if m else None

    # --- due dates ---
    def get_upcoming(self, days: int = 7) -> list[Item]:
        items: list[Item] = []
        for c in _COURSES:
            key = re.match(r"[A-Z]+ \d+", c.code).group(0)
            for title, due_in in _DATA[key]["upcoming"]:
                if due_in <= days:
                    items.append(Item(
                        course=key, title=title, due_at=_in(due_in),
                        ref=f"{c.id}:{abs(hash(title)) % 9999}",
                        html_url=self.course_web_url(c.id), type="assignment",
                    ))
        items.sort(key=lambda i: i.due_at or _now())
        return items

    # --- grades ---
    def get_grades(self) -> list[Grade]:
        out = []
        for c in _COURSES:
            key = re.match(r"[A-Z]+ \d+", c.code).group(0)
            d = _DATA[key]
            out.append(Grade(course=key, score=d["score"], grade=d["grade"]))
        return out

    def get_course_grades(self, course: str) -> list[AssignmentScore]:
        key = self._key_for(course)
        if not key:
            return []
        scores: list[AssignmentScore] = []
        for _gname, _w, items in _DATA[key]["groups"]:
            for name, score, points in items:
                scores.append(AssignmentScore(name=name, score=score, points=points))
        return scores

    def get_grade_breakdown(self, course: str) -> list[GradeGroup]:
        key = self._key_for(course)
        if not key:
            return []
        return [GradeGroup(name=g, weight=w, items=list(items))
                for g, w, items in _DATA[key]["groups"]]

    # --- syllabus & study material ---
    def get_syllabus(self, course: str) -> str:
        key = self._key_for(course)
        return _DATA[key]["syllabus"] if key else ""

    def get_study_material(self, course: str) -> tuple[str, str]:
        key = self._key_for(course)
        if not key:
            return course, ""
        d = _DATA[key]
        text = f"## Syllabus\n{d['syllabus']}\n\n## Topics covered\n{d['topics']}"
        return key, text

    def get_assignment_detail(self, ref: str) -> AssignmentDetail:
        return AssignmentDetail(
            name="Sample assignment", course="CSE 142",
            due_at=_in(2), points=20.0,
            description="This is a demo assignment. Connect your real Canvas to see actual details.",
            html_url=self.web_base,
        )

    # --- things the demo simply doesn't surface ---
    def get_announcements(self, days_back: int = 14) -> list[Announcement]:
        return []

    def get_calendar_events(self, days_ahead: int = 14) -> list[CalendarEvent]:
        return []

    def get_inbox(self, limit: int = 20) -> list[InboxMessage]:
        return []

    def get_submission(self, course: str, assignment: str) -> Submission | None:
        return None

    def search_assignments(self, query: str, course_id: int | None = None) -> list[Item]:
        q = (query or "").lower()
        return [i for i in self.get_upcoming(days=60) if q in i.title.lower()]

    def canvas_get(self, path: str, params: dict | None = None):
        return []  # no raw Canvas API access in demo
