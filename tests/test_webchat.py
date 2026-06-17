"""Tests for the web chat app (the private 'texting' interface)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import AppDeps, build_app
from app.db import ConversationStore, StudyPageStore, WebChatStore, ChatStore, PushStore
from app.webchat import WebClient


class FakeBrain:
    def __init__(self, reply="hw4 is due tue 11:59pm"):
        self.reply = reply
        self.seen = []

    def respond(self, user_text, history=None, attachments=None):
        self.seen.append((user_text, history))
        return self.reply


class FakePush:
    """Records notify() calls; carries a real PushStore for subscribe tests."""

    def __init__(self, store, enabled=True):
        self.store = store
        self.enabled = enabled
        self.notes = []

    def notify(self, title, body, url="/chat", force=False):
        self.notes.append((title, body))

    def send_sync(self, title, body, url="/chat", force=False):
        self.notes.append((title, body))
        return [{"endpoint": s["endpoint"][-14:], "status": 201, "error": None}
                for s in self.store.all()]


def make_web_client(tmp_path, secret="letmein", with_push=False, vapid="PUBKEY"):
    chats = ChatStore(tmp_path / "chats.sqlite")
    push = FakePush(PushStore(tmp_path / "push.sqlite")) if with_push else None
    sms = WebClient(chats, push=push)
    brain = FakeBrain()
    deps = AppDeps(
        sms=sms,
        brain=brain,
        conversation=ConversationStore(tmp_path / "c.sqlite"),
        study=StudyPageStore(tmp_path / "s.sqlite"),
        require_signature=False,
        validate=lambda u, f, s: True,
        chats=chats,
        web_chat_secret=secret,
        push=push,
        vapid_public_key=vapid if with_push else "",
    )
    return TestClient(build_app(deps)), chats, brain, push


# ---- WebClient -------------------------------------------------------------

def test_webclient_send_strips_em_dashes(tmp_path):
    chats = ChatStore(tmp_path / "w.sqlite")
    cid = chats.ensure_chat()
    WebClient(chats).send("CSE 163 — Homework 4 — due tue 11:59pm")
    text = chats.since(cid, 0)[-1]["text"]
    assert "—" not in text
    assert text == "CSE 163 - Homework 4 - due tue 11:59pm"


# ---- WebChatStore ---------------------------------------------------------

def test_webchatstore_append_since_and_clear(tmp_path):
    s = WebChatStore(tmp_path / "w.sqlite")
    i1 = s.append("user", "hi")
    i2 = s.append("assistant", "hey", "https://x/file/1")
    assert i2 > i1
    assert s.max_id() == i2
    msgs = s.since(0)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["media_url"] == "https://x/file/1"
    # since(after) only returns newer rows
    assert s.since(i1) == [msgs[1]]
    s.clear()
    assert s.since(0) == [] and s.max_id() == 0


def test_webclient_send_writes_to_active_chat(tmp_path):
    chats = ChatStore(tmp_path / "w.sqlite")
    cid = chats.ensure_chat()
    WebClient(chats).send("here's your essay:", media_url=["https://x/file/9"])
    msgs = chats.since(cid, 0)
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["text"] == "here's your essay:"
    assert msgs[0]["media_url"] == "https://x/file/9"


# ---- Web routes -----------------------------------------------------------

def test_root_redirects_to_chat(tmp_path):
    client, *_ = make_web_client(tmp_path)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/chat"


def test_chat_page_and_pwa_assets_served(tmp_path):
    client, *_ = make_web_client(tmp_path)
    assert client.get("/chat").status_code == 200
    assert "Study Assistant" in client.get("/chat").text
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200


def test_chat_send_requires_passcode(tmp_path):
    client, store, brain, _ = make_web_client(tmp_path)
    r = client.post("/chat/send", json={"text": "what's due?"})  # no key
    assert r.status_code == 401
    assert brain.seen == []
    r2 = client.post("/chat/send", json={"text": "x"}, headers={"X-Chat-Key": "wrong"})
    assert r2.status_code == 401


def test_chat_send_returns_user_and_brain_reply(tmp_path):
    client, store, brain, _ = make_web_client(tmp_path)
    r = client.post(
        "/chat/send", json={"text": "what's due this week?"},
        headers={"X-Chat-Key": "letmein"},
    )
    assert r.status_code == 200
    msgs = r.json()["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert msgs[0]["text"] == "what's due this week?"
    assert msgs[1]["text"] == "hw4 is due tue 11:59pm"
    assert brain.seen[0][0] == "what's due this week?"


def test_chat_messages_polling_returns_new_only(tmp_path):
    client, chats, brain, _ = make_web_client(tmp_path)
    client.post("/chat/send", json={"text": "hi"}, headers={"X-Chat-Key": "letmein"})
    cid = chats.ensure_chat()
    last = chats.max_id(cid)
    # a reminder fires later -> WebClient.send appends to the active/most-recent chat
    WebClient(chats).send("don't forget: hw4 due tonight")
    r = client.get(f"/chat/messages?after={last}&chat_id={cid}", headers={"X-Chat-Key": "letmein"})
    new = r.json()["messages"]
    assert len(new) == 1
    assert "hw4 due tonight" in new[0]["text"]


def test_chat_clear_wipes_transcript(tmp_path):
    client, store, brain, _ = make_web_client(tmp_path)
    client.post("/chat/send", json={"text": "hi"}, headers={"X-Chat-Key": "letmein"})
    calls_before = len(brain.seen)
    r = client.post("/chat/send", json={"text": "clear"}, headers={"X-Chat-Key": "letmein"})
    body = r.json()
    # clear wipes everything, then the fresh chat greets you again
    assert body.get("cleared") is True
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert "hey" in body["messages"][0]["text"].lower()
    assert len(brain.seen) == calls_before  # clear shouldn't reach the brain


def test_new_chat_greets_first(tmp_path):
    client, store, brain, _ = make_web_client(tmp_path)
    r = client.get("/chat/messages?after=0", headers={"X-Chat-Key": "letmein"})
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "hey" in msgs[0]["text"].lower()


# ---- Push notifications ---------------------------------------------------

def test_pushstore_save_all_remove_clear(tmp_path):
    s = PushStore(tmp_path / "p.sqlite")
    sub = {"endpoint": "https://push/abc", "keys": {"p256dh": "PK", "auth": "AU"}}
    s.save(sub)
    got = s.all()
    assert got == [sub]
    # re-saving the same endpoint upserts (no duplicate)
    s.save(sub)
    assert len(s.all()) == 1
    s.remove("https://push/abc")
    assert s.all() == []


def test_webclient_send_triggers_push(tmp_path):
    chats = ChatStore(tmp_path / "w.sqlite")
    chats.ensure_chat()
    push = FakePush(PushStore(tmp_path / "p.sqlite"))
    WebClient(chats, push=push).send("you have a final tomorrow")
    assert push.notes and push.notes[0][0] == "Study Assistant"
    assert "final tomorrow" in push.notes[0][1]


def test_chat_send_pushes_the_reply(tmp_path):
    client, store, brain, push = make_web_client(tmp_path, with_push=True)
    client.post("/chat/send", json={"text": "what's due?"}, headers={"X-Chat-Key": "letmein"})
    assert push.notes  # the assistant reply fired a push


def test_chat_subscribe_requires_auth_and_stores(tmp_path):
    client, store, brain, push = make_web_client(tmp_path, with_push=True)
    sub = {"endpoint": "https://push/xyz", "keys": {"p256dh": "P", "auth": "A"}}
    assert client.post("/chat/subscribe", json=sub).status_code == 401
    r = client.post("/chat/subscribe", json=sub, headers={"X-Chat-Key": "letmein"})
    assert r.status_code == 200
    assert push.store.all() == [sub]
    assert push.notes  # subscribing fires a confirmation notification


def test_chat_config_returns_vapid_key_when_push_enabled(tmp_path):
    client, store, brain, push = make_web_client(tmp_path, with_push=True, vapid="THEKEY")
    assert client.get("/chat/config").status_code == 401  # needs passcode
    r = client.get("/chat/config", headers={"X-Chat-Key": "letmein"})
    assert r.json()["vapidPublicKey"] == "THEKEY"


def test_push_debug_and_testpush(tmp_path):
    client, store, brain, push = make_web_client(tmp_path, with_push=True)
    sub = {"endpoint": "https://push/longendpoint123", "keys": {"p256dh": "P", "auth": "A"}}
    client.post("/chat/subscribe", json=sub, headers={"X-Chat-Key": "letmein"})
    dbg = client.get("/chat/pushdebug", headers={"X-Chat-Key": "letmein"}).json()
    assert dbg["subscriptions"] == 1 and dbg["enabled"] is True
    res = client.post("/chat/testpush", headers={"X-Chat-Key": "letmein"}).json()
    assert res["results"] and res["results"][0]["status"] == 201
    # both endpoints require the passcode
    assert client.get("/chat/pushdebug").status_code == 401
    assert client.post("/chat/testpush").status_code == 401
