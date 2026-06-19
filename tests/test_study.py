"""Tests for the study-page maker (flashcards + practice exams)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app import study
from app.db import StudyPageStore


# --- Fakes -------------------------------------------------------------------

class FakeCanvas:
    def __init__(self):
        self.requested = None

    def get_study_material(self, course):
        self.requested = course
        return "STAT 311", (
            "## Syllabus\nTopics: hypothesis testing, confidence intervals, regression.\n\n"
            "## Topic outline (course modules)\n- Inference\n  - p-values\n- Regression"
        )


def text_block(text):
    return SimpleNamespace(type="text", text=text)


class FakeAnthropic:
    """Returns a scripted JSON payload as the model's text output."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", content=[text_block(json.dumps(self._payload))]
        )


def make_service(tmp_path, payload):
    pages = StudyPageStore(tmp_path / "s.sqlite")
    client = FakeAnthropic(payload)
    svc = study.StudyService(
        canvas=FakeCanvas(),
        client=client,
        model="claude-opus-4-8",
        pages=pages,
        public_base_url="https://app.example.com",
    )
    return svc, pages, client


# --- Rendering (pure) --------------------------------------------------------

def test_render_flashcards_includes_questions_and_answers():
    html = study.render_flashcards(
        "STAT 311 — flashcards",
        [{"q": "What is a p-value?", "a": "The probability of data this extreme under H0."}],
    )
    assert "STAT 311 — flashcards" in html
    assert "What is a p-value?" in html
    assert "The probability of data this extreme under H0." in html
    assert "<html" in html.lower()
    # It's an interactive flip-deck with spaced-repetition rating, not a flat list.
    assert "flip" in html and "Got it" in html


def test_render_exam_includes_questions_and_an_answer_key():
    html = study.render_exam(
        "STAT 311 — practice exam",
        [{"q": "State the Central Limit Theorem.", "a": "Sample means tend to normal."}],
    )
    assert "State the Central Limit Theorem." in html
    assert "Sample means tend to normal." in html  # answer present (revealable)


def test_render_quiz_mc_shows_options_and_embeds_the_answer_key():
    html = study.render_quiz_mc(
        "STAT 311 - quiz",
        [{"q": "Mean of 2, 4, 6?", "choices": ["3", "4", "5", "6"],
          "answer_index": 1, "explanation": "(2+4+6)/3 = 4"}],
    )
    assert "Mean of 2, 4, 6?" in html
    for choice in ["3", "4", "5", "6"]:
        assert choice in html
    assert "answer_index" in html          # key embedded so the page can grade
    assert "(2+4+6)/3 = 4" in html         # explanation shown after answering
    assert "score" in html.lower()         # it tallies a score
    assert "<html" in html.lower()


def test_render_quiz_written_has_a_box_to_type_into():
    html = study.render_quiz_written(
        "STAT 311 - quiz",
        [{"q": "Define statistical power.", "answer": "P(reject H0 | H1 true)"}],
    )
    assert "Define statistical power." in html
    assert "P(reject H0 | H1 true)" in html         # model answer (revealable)
    assert "<textarea" in html.lower()              # you can type your answer


# --- Dispatch flow -----------------------------------------------------------

def test_make_flashcards_uses_course_material_and_returns_link(tmp_path):
    payload = {"cards": [{"q": "What is a p-value?", "a": "A probability."}]}
    svc, pages, client = make_service(tmp_path, payload)

    out = svc.dispatch("make_flashcards", {"course": "STAT 311"})

    # A link to our app was returned and the page was stored.
    assert "https://app.example.com/study/" in out
    page_id = out.split("/study/")[1].split()[0].strip()
    stored = pages.get(page_id)
    assert stored is not None
    assert "What is a p-value?" in stored
    # The model was asked with a forced JSON shape...
    assert "output_config" in client.calls[0]
    # ...and fed the whole-course material (not one thin assignment).
    sent = client.calls[0]["messages"][0]["content"]
    assert "hypothesis testing" in sent and "Regression" in sent
    assert svc.canvas.requested == "STAT 311"


def test_make_practice_exam_uses_course(tmp_path):
    payload = {"questions": [{"q": "Define statistical power.", "a": "P(reject H0 | H1 true)."}]}
    svc, pages, _ = make_service(tmp_path, payload)

    out = svc.dispatch("make_practice_exam", {"course": "STAT 311"})
    assert "/study/" in out
    page_id = out.split("/study/")[1].split()[0].strip()
    assert "Define statistical power." in pages.get(page_id)


def test_make_practice_exam_multiple_choice_builds_a_graded_quiz(tmp_path):
    payload = {"questions": [
        {"q": "Mean of 2,4,6?", "choices": ["3", "4", "5", "6"],
         "answer_index": 1, "explanation": "average"}
    ]}
    svc, pages, client = make_service(tmp_path, payload)

    out = svc.dispatch("make_practice_exam", {"course": "STAT 311", "format": "multiple_choice"})
    page_id = out.split("/study/")[1].split()[0].strip()
    html = pages.get(page_id)
    assert "Mean of 2,4,6?" in html
    assert "answer_index" in html
    # The forced JSON shape required multiple-choice fields.
    schema = json.dumps(client.calls[0]["output_config"]["format"]["schema"])
    assert "choices" in schema and "answer_index" in schema


def test_make_practice_exam_written_lets_you_type_answers(tmp_path):
    payload = {"questions": [{"q": "Define power.", "answer": "P(reject H0 | H1 true)"}]}
    svc, pages, client = make_service(tmp_path, payload)

    out = svc.dispatch("make_practice_exam", {"course": "STAT 311", "format": "written"})
    page_id = out.split("/study/")[1].split()[0].strip()
    html = pages.get(page_id)
    assert "Define power." in html
    assert "<textarea" in html.lower()


def test_topic_focuses_title_and_prompt(tmp_path):
    svc, pages, client = make_service(tmp_path, {"cards": [{"q": "q", "a": "a"}]})

    out = svc.dispatch("make_flashcards", {"course": "STAT 311", "topic": "regression"})
    page_id = out.split("/study/")[1].split()[0].strip()
    assert "regression" in pages.get(page_id)  # title carries the topic
    assert "regression" in client.calls[0]["messages"][0]["content"]


def test_legacy_ref_falls_back_to_course_id(tmp_path):
    # Older callers may still pass a 'courseId:assignmentId' ref.
    svc, _, _ = make_service(tmp_path, {"cards": []})
    svc.dispatch("make_flashcards", {"ref": "12345:55"})
    assert svc.canvas.requested == "12345"


def test_quiz_page_has_a_regenerate_control_with_its_own_id(tmp_path):
    payload = {"questions": [
        {"q": "Q1", "choices": ["a", "b", "c", "d"], "answer_index": 0, "explanation": "x"}
    ]}
    svc, pages, _ = make_service(tmp_path, payload)
    out = svc.dispatch("make_practice_exam", {"course": "STAT 311", "format": "multiple_choice"})
    page_id = out.split("/study/")[1].split()[0].strip()
    html = pages.get(page_id)
    assert "regenerate" in html.lower()   # the regenerate endpoint is wired in
    assert page_id in html                 # the page knows its own id to call it


def test_regenerate_rebuilds_the_same_page_from_stored_recipe(tmp_path):
    payload = {"questions": [
        {"q": "Q1", "choices": ["a", "b", "c", "d"], "answer_index": 0, "explanation": "x"}
    ]}
    svc, pages, client = make_service(tmp_path, payload)
    out = svc.dispatch("make_practice_exam", {"course": "STAT 311", "format": "multiple_choice"})
    page_id = out.split("/study/")[1].split()[0].strip()
    assert len(client.calls) == 1

    ok = svc.regenerate(page_id)
    assert ok is True
    assert len(client.calls) == 2          # generated a fresh set on the same id
    assert "Q1" in pages.get(page_id)
    assert svc.canvas.requested == "STAT 311"  # reused the stored course


def test_regenerate_unknown_page_returns_false(tmp_path):
    svc, _, _ = make_service(tmp_path, {"questions": []})
    assert svc.regenerate("does-not-exist") is False


def test_tool_names_and_schemas(tmp_path):
    svc, _, _ = make_service(tmp_path, {"cards": []})
    assert set(svc.tool_names()) == {"make_flashcards", "make_practice_exam"}
    assert {s["name"] for s in svc.schemas()} == set(svc.tool_names())
    # Either a course OR a saved lecture works, so neither is hard-required;
    # both inputs are still offered.
    for s in svc.schemas():
        assert "required" not in s["input_schema"]
        props = s["input_schema"]["properties"]
        assert "course" in props and "lecture_id" in props
