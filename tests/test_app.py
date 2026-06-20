"""Tests for the FastAPI app (the /sms webhook and friends)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import AppDeps, build_app
from app.db import ConversationStore, StudyPageStore, FileStore


class FakeSms:
    def __init__(self, my_number="+12065559876", channel="sms", typing_ok=True):
        self.my_number = my_number
        self.channel = channel
        self.typing_ok = typing_ok
        self.sent = []
        self.media_sent = []
        self.typing_calls = []

    def is_allowed(self, frm):
        from app.sms import numbers_match
        return numbers_match(frm, self.my_number)

    def send(self, text, to=None, media_url=None):
        self.sent.append(text)
        if media_url:
            self.media_sent.append((text, media_url))

    def send_typing(self, message_sid):
        self.typing_calls.append(message_sid)
        return self.typing_ok

    def download_media(self, url):
        return (b"FILEBYTES", "image/jpeg")


class FakeOneDrive:
    folder = "WhatsApp Files"

    def __init__(self, files=None):
        self.uploaded = []
        self._files = files or []  # list of {"name":...}

    def upload(self, name, data, content_type):
        self.uploaded.append({"name": name, "data": data, "content_type": content_type})
        self._files.append({"name": name})
        return {"name": name}

    def list_files(self):
        return list(self._files)

    def download(self, query):
        for f in self._files:
            if query.lower() in f["name"].lower():
                return (b"OUT", "application/pdf", f["name"])
        return None


class FakeBrain:
    def __init__(self, reply="hw4 is due tue 11:59pm"):
        self.reply = reply
        self.seen = []
        self.attachments_seen = []

    def respond(self, user_text, history=None, attachments=None):
        self.seen.append((user_text, history))
        self.attachments_seen.append(attachments)
        return self.reply


def make_client(tmp_path, require_signature=False):
    sms = FakeSms()
    brain = FakeBrain()
    conversation = ConversationStore(tmp_path / "c.sqlite")
    study = StudyPageStore(tmp_path / "s.sqlite")
    deps = AppDeps(
        sms=sms,
        brain=brain,
        conversation=conversation,
        study=study,
        require_signature=require_signature,
        validate=lambda url, form, sig: True,
    )
    app = build_app(deps)
    return TestClient(app), sms, brain, conversation, study


def test_health(tmp_path):
    client, *_ = make_client(tmp_path)
    assert client.get("/health").json() == {"ok": True}


def test_incoming_text_from_me_gets_a_reply(tmp_path):
    client, sms, brain, conversation, _ = make_client(tmp_path)
    resp = client.post(
        "/sms",
        data={"Body": "what's due this week?", "From": "+12065559876"},
    )
    assert resp.status_code == 200
    # Background work ran: brain answered and the reply was texted back.
    assert brain.seen[0][0] == "what's due this week?"
    assert sms.sent == ["hw4 is due tue 11:59pm"]
    # The exchange was remembered.
    contents = [t["content"] for t in conversation.recent()]
    assert "what's due this week?" in contents
    assert "hw4 is due tue 11:59pm" in contents


def _whatsapp_client(tmp_path, typing_ok=True):
    sms = FakeSms(channel="whatsapp", typing_ok=typing_ok)
    brain = FakeBrain()
    deps = AppDeps(
        sms=sms, brain=brain,
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        require_signature=False, validate=lambda u, f, s: True,
    )
    return TestClient(build_app(deps)), sms, brain


def test_whatsapp_shows_typing_indicator(tmp_path):
    client, sms, brain = _whatsapp_client(tmp_path)
    client.post(
        "/sms",
        data={"Body": "what's due?", "From": "+12065559876", "MessageSid": "SMabc123"},
    )
    # The 'typing…' animation was triggered for the inbound message, no filler text.
    assert sms.typing_calls == ["SMabc123"]
    assert sms.sent == ["hw4 is due tue 11:59pm"]


def test_typing_falls_back_to_text_when_unsupported(tmp_path):
    client, sms, brain = _whatsapp_client(tmp_path, typing_ok=False)
    client.post(
        "/sms",
        data={"Body": "hi", "From": "+12065559876", "MessageSid": "SMabc"},
    )
    # Native indicator refused → a quick "on it" text shows instead, plus the reply.
    assert "on it" in sms.sent
    assert "hw4 is due tue 11:59pm" in sms.sent


def test_no_typing_without_a_message_sid(tmp_path):
    client, sms, brain = _whatsapp_client(tmp_path)
    client.post("/sms", data={"Body": "hi", "From": "+12065559876"})
    assert sms.typing_calls == []


class FakeReminders:
    def __init__(self):
        self.cleared = 0

    def clear_all(self):
        self.cleared += 1
        return 3


def _client_with_reminders(tmp_path):
    from app.main import AppDeps, build_app
    from fastapi.testclient import TestClient

    sms = FakeSms()
    brain = FakeBrain()
    conversation = ConversationStore(tmp_path / "c.sqlite")
    study = StudyPageStore(tmp_path / "s.sqlite")
    reminders = FakeReminders()
    deps = AppDeps(
        sms=sms, brain=brain, conversation=conversation, study=study,
        require_signature=False, validate=lambda u, f, s: True, reminders=reminders,
    )
    return TestClient(build_app(deps)), sms, brain, conversation, study, reminders


def test_clear_clears_only_the_chat(tmp_path):
    client, sms, brain, conversation, study, reminders = _client_with_reminders(tmp_path)
    conversation.save("user", "old message")
    study.save("p1", "Flashcards", "<html>cards</html>")

    resp = client.post("/sms", data={"Body": "CLEAR", "From": "+12065559876"})
    assert resp.status_code == 200
    # Chat wiped; study pages and reminders left alone; brain not consulted.
    assert conversation.recent() == []
    assert study.get("p1") is not None
    assert reminders.cleared == 0
    assert brain.seen == []
    assert sms.sent and "chat" in sms.sent[0].lower()


def test_clear_reminders_clears_only_reminders(tmp_path):
    client, sms, brain, conversation, study, reminders = _client_with_reminders(tmp_path)
    conversation.save("user", "keep me")

    client.post("/sms", data={"Body": "clear reminders", "From": "+12065559876"})
    assert reminders.cleared == 1
    assert conversation.recent() != []  # chat untouched
    assert brain.seen == []
    assert "reminder" in sms.sent[0].lower()


def test_clear_all_wipes_everything(tmp_path):
    client, sms, brain, conversation, study, reminders = _client_with_reminders(tmp_path)
    conversation.save("user", "old")
    study.save("p1", "T", "<html>x</html>")

    client.post("/sms", data={"Body": "clear all", "From": "+12065559876"})
    assert conversation.recent() == []
    assert study.get("p1") is None
    assert reminders.cleared == 1
    assert brain.seen == []
    assert "everything" in sms.sent[0].lower()


def _client_with_onedrive(tmp_path, files=None):
    sms = FakeSms()
    brain = FakeBrain()
    onedrive = FakeOneDrive(files=files)
    deps = AppDeps(
        sms=sms, brain=brain,
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        files=FileStore(tmp_path / "f.sqlite"),
        onedrive=onedrive, public_base_url="https://app.example",
        require_signature=False, validate=lambda u, f, s: True,
    )
    return TestClient(build_app(deps)), sms, brain, onedrive


def test_incoming_file_is_saved_to_onedrive(tmp_path):
    client, sms, brain, onedrive = _client_with_onedrive(tmp_path)
    resp = client.post("/sms", data={
        "From": "+12065559876", "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/abc",
        "MediaContentType0": "image/jpeg",
    })
    assert resp.status_code == 200
    assert len(onedrive.uploaded) == 1
    assert onedrive.uploaded[0]["data"] == b"FILEBYTES"
    assert onedrive.uploaded[0]["name"].endswith(".jpg")
    assert brain.seen == []  # a file shouldn't go to the brain
    assert sms.sent and "saved" in sms.sent[0].lower()


def test_files_command_lists_the_folder(tmp_path):
    client, sms, *_ = _client_with_onedrive(tmp_path, files=[{"name": "essay.docx"}])
    client.post("/sms", data={"From": "+12065559876", "Body": "files"})
    assert sms.sent and "essay.docx" in sms.sent[0]


def test_send_command_delivers_the_file_as_media(tmp_path):
    client, sms, brain, _ = _client_with_onedrive(tmp_path, files=[{"name": "notes.pdf"}])
    client.post("/sms", data={"From": "+12065559876", "Body": "send notes.pdf"})
    assert sms.media_sent, "should have sent a file as media"
    text, media_url = sms.media_sent[0]
    assert "notes.pdf" in text
    assert media_url[0].startswith("https://app.example/file/")
    assert brain.seen == []


def test_serve_file_route_returns_bytes(tmp_path):
    client, sms, brain, _ = _client_with_onedrive(tmp_path, files=[{"name": "notes.pdf"}])
    client.post("/sms", data={"From": "+12065559876", "Body": "send notes.pdf"})
    _, media_url = sms.media_sent[0]
    path = media_url[0].split("https://app.example")[1]
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.content == b"OUT"


def test_get_me_my_grade_falls_through_to_brain(tmp_path):
    # "get ..." without a filename-looking arg should NOT be treated as a file request.
    client, sms, brain, _ = _client_with_onedrive(tmp_path)
    client.post("/sms", data={"From": "+12065559876", "Body": "get me my grade"})
    assert brain.seen and brain.seen[0][0] == "get me my grade"


def test_text_from_a_stranger_is_ignored(tmp_path):
    client, sms, brain, *_ = make_client(tmp_path)
    resp = client.post("/sms", data={"Body": "hi", "From": "+19999999999"})
    assert resp.status_code == 200
    assert sms.sent == []
    assert brain.seen == []


def test_prior_conversation_is_passed_as_history(tmp_path):
    client, sms, brain, conversation, _ = make_client(tmp_path)
    conversation.save("user", "what's due?")
    conversation.save("assistant", "hw4 for 163")
    client.post("/sms", data={"Body": "what's that asking?", "From": "+12065559876"})
    _, history = brain.seen[0]
    assert {"role": "user", "content": "what's due?"} in history
    assert {"role": "assistant", "content": "hw4 for 163"} in history


def test_bad_signature_is_rejected(tmp_path):
    sms = FakeSms()
    brain = FakeBrain()
    deps = AppDeps(
        sms=sms,
        brain=brain,
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        require_signature=True,
        validate=lambda url, form, sig: False,  # always reject
    )
    client = TestClient(build_app(deps))
    resp = client.post(
        "/sms",
        data={"Body": "hi", "From": "+12065559876"},
        headers={"X-Twilio-Signature": "bogus"},
    )
    assert resp.status_code == 403
    assert brain.seen == []


class FakeCanvasCourses:
    """Mirrors the messy real Canvas list: real classes + resource sites + archived."""

    def list_courses(self):
        from app.canvas import Course
        return [
            Course(id=1, name="CSE 163 A Sp 26: Intermediate Data Programming", code="CSE 163 A"),
            Course(id=2, name="MATH 124 A and B Sp 26: Calculus With Analytic Geometry I",
                   code="MATH 124 A and B"),
            Course(id=3, name="PHIL 149 Sp 26", code="PHIL 149 Sp 26"),
            Course(id=4, name="Informatics Resource Site", code="Informatics Resource"),
            Course(id=5, name="ARCHIVED: ENGL 258, Autumn '25", code="ARCHIVED: ENGL 258 A"),
        ]


def _web_chat_client(tmp_path, canvas=None):
    from app.db import ChatStore
    from app.webchat import WebClient

    chats = ChatStore(tmp_path / "chats.sqlite")
    sms = WebClient(chats)
    brain = FakeBrain()
    deps = AppDeps(
        sms=sms, brain=brain,
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        require_signature=False, validate=lambda u, f, s: True,
        chats=chats, web_chat_secret="k", canvas=canvas,
    )
    return TestClient(build_app(deps)), brain, chats


def test_opening_message_lists_real_classes_only(tmp_path):
    client, brain, webchat = _web_chat_client(tmp_path, canvas=FakeCanvasCourses())
    resp = client.get("/chat/messages?after=0", headers={"X-Chat-Key": "k"})
    greeting = resp.json()["messages"][0]["text"]
    assert "enrolled" in greeting.lower()
    # Real classes show with a clean code + title (no term-code clutter):
    assert "CSE 163: Intermediate Data Programming" in greeting
    assert "MATH 124: Calculus With Analytic Geometry I" in greeting
    assert "PHIL 149" in greeting
    # Resource sites and archived courses are filtered out:
    assert "Informatics Resource" not in greeting
    assert "ARCHIVED" not in greeting
    assert "—" not in greeting  # no em dashes anywhere


def test_opening_message_falls_back_without_canvas(tmp_path):
    client, brain, webchat = _web_chat_client(tmp_path, canvas=None)
    resp = client.get("/chat/messages?after=0", headers={"X-Chat-Key": "k"})
    greeting = resp.json()["messages"][0]["text"]
    assert "dubly" in greeting.lower()
    assert "—" not in greeting


def test_chat_send_passes_uploaded_image_to_brain(tmp_path):
    import base64

    client, brain, webchat = _web_chat_client(tmp_path)
    img_b64 = base64.b64encode(b"img-bytes").decode()
    resp = client.post(
        "/chat/send",
        headers={"X-Chat-Key": "k"},
        json={
            "text": "what is this?",
            "attachments": [
                {"name": "p.png", "content_type": "image/png", "data": img_b64}
            ],
        },
    )
    assert resp.status_code == 200
    assert brain.seen[0][0] == "what is this?"
    # The endpoint decoded the base64 and handed the brain real bytes.
    assert brain.attachments_seen[0] == [("p.png", "image/png", b"img-bytes")]


def test_chat_send_with_only_a_file_and_no_text_still_reaches_the_brain(tmp_path):
    import base64

    client, brain, webchat = _web_chat_client(tmp_path)
    pdf_b64 = base64.b64encode(b"%PDF-1.4").decode()
    resp = client.post(
        "/chat/send",
        headers={"X-Chat-Key": "k"},
        json={
            "text": "",
            "attachments": [
                {"name": "hw.pdf", "content_type": "application/pdf", "data": pdf_b64}
            ],
        },
    )
    assert resp.status_code == 200
    assert brain.attachments_seen[0] == [("hw.pdf", "application/pdf", b"%PDF-1.4")]


def test_send_with_unknown_chat_id_creates_new_chat_not_hijacks(tmp_path):
    client, brain, chats = _web_chat_client(tmp_path)
    a = chats.create_chat()
    chats.append(a, "user", "secret in A")
    r = client.post(
        "/chat/send", headers={"X-Chat-Key": "k"},
        json={"text": "hello", "chat_id": 999999},
    )
    new_cid = r.json()["chat_id"]
    assert new_cid not in (a, 999999)  # neither hijacked A nor used the bogus id
    # Chat A is untouched.
    assert [m["text"] for m in chats.since(a, 0)] == ["secret in A"]


def test_new_chat_endpoint_seeds_a_greeting(tmp_path):
    client, brain, chats = _web_chat_client(tmp_path, canvas=FakeCanvasCourses())
    cid = client.post("/chats", headers={"X-Chat-Key": "k"}).json()["id"]
    msgs = chats.since(cid, 0)
    assert msgs and msgs[0]["role"] == "assistant"
    assert "enrolled" in msgs[0]["text"].lower()


def test_search_chats_endpoint(tmp_path):
    client, brain, chats = _web_chat_client(tmp_path)
    a = chats.create_chat(); chats.append(a, "user", "MATH 124 homework due friday")
    chats.create_chat()
    r = client.get("/chats/search?q=MATH%20124", headers={"X-Chat-Key": "k"})
    ids = [c["id"] for c in r.json()["chats"]]
    assert ids == [a]
    # empty query returns the full list
    r2 = client.get("/chats/search?q=", headers={"X-Chat-Key": "k"})
    assert len(r2.json()["chats"]) >= 2


def test_rename_unknown_chat_is_404(tmp_path):
    client, brain, chats = _web_chat_client(tmp_path)
    r = client.patch("/chats/888888", headers={"X-Chat-Key": "k"}, json={"title": "x"})
    assert r.status_code == 404


def test_cancelled_generation_is_not_added_to_the_transcript(tmp_path):
    client, brain, webchat = _web_chat_client(tmp_path)
    # The user hit "stop" before the reply landed: cancel the gen id first.
    client.post("/chat/cancel", headers={"X-Chat-Key": "k"}, json={"gen_id": "g1"})
    resp = client.post(
        "/chat/send",
        headers={"X-Chat-Key": "k"},
        json={"text": "what's due?", "gen_id": "g1"},
    )
    assert resp.status_code == 200
    # The user's message shows, but the cancelled assistant reply was discarded.
    msgs = resp.json()["messages"]
    assert any(m["role"] == "user" for m in msgs)
    assert not any(m["role"] == "assistant" for m in msgs)


def test_uncancelled_generation_still_replies(tmp_path):
    client, brain, webchat = _web_chat_client(tmp_path)
    resp = client.post(
        "/chat/send",
        headers={"X-Chat-Key": "k"},
        json={"text": "what's due?", "gen_id": "g2"},
    )
    msgs = resp.json()["messages"]
    assert any(m["role"] == "assistant" for m in msgs)


class FakeStudyService:
    def __init__(self):
        self.calls = []

    def regenerate(self, page_id):
        self.calls.append(page_id)
        return page_id == "good"


def test_study_regenerate_endpoint(tmp_path):
    sms = FakeSms()
    svc = FakeStudyService()
    deps = AppDeps(
        sms=sms, brain=FakeBrain(),
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        require_signature=False, validate=lambda u, f, s: True,
        study_service=svc,
    )
    client = TestClient(build_app(deps))
    assert client.post("/study/good/regenerate").json() == {"ok": True}
    assert client.post("/study/bad/regenerate").status_code == 404
    assert svc.calls == ["good", "bad"]


def test_study_page_served_when_present(tmp_path):
    client, sms, brain, conversation, study = make_client(tmp_path)
    study.save("abc123", "Flashcards", "<html><body>cards</body></html>")
    resp = client.get("/study/abc123")
    assert resp.status_code == 200
    assert "cards" in resp.text


def test_missing_study_page_is_404(tmp_path):
    client, *_ = make_client(tmp_path)
    assert client.get("/study/nope").status_code == 404
