"""Tests for scheduled notifications: rule CRUD, digest firing, due-soon sweep,
one-off reminders, and ToolBox dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app import notifications
from app.canvas import Item
from app.db import ChatStore, NotificationStore


class FakePush:
    def __init__(self):
        self.sent = []

    def notify(self, title, body, url="/chat", force=False):
        self.sent.append({"title": title, "body": body, "url": url, "force": force})


class FakeCanvas:
    def __init__(self, items):
        self._items = items

    def get_upcoming(self, days=7):
        now = datetime.now(timezone.utc)
        return [it for it in self._items if it.due_at and (it.due_at - now).days <= days]


def _item(course, title, hours_from_now):
    due = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return Item(course=course, title=title, due_at=due,
                ref=f"{course}:{title}", html_url="", type="assignment")


def make_service(tmp_path, items=None):
    scheduler = BackgroundScheduler(timezone="UTC")  # not started
    store = NotificationStore(tmp_path / "notif.sqlite")
    chats = ChatStore(tmp_path / "chats.sqlite")
    push = FakePush()
    canvas = FakeCanvas(items or [])
    svc = notifications.NotificationService(
        scheduler=scheduler, store=store, canvas=canvas, chats=chats, push=push
    )
    return svc, store, chats, push


def test_add_daily_rule_lists_and_describes(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    rule = svc.add_rule(kind="daily", time="08:00")
    rules = svc.list_rules()
    assert len(rules) == 1
    assert rules[0]["id"] == rule["id"]
    assert "every day at 8am" in rules[0]["label"]


def test_weekly_and_due_descriptions(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    w = svc.add_rule(kind="weekly", time="17:30", weekday="fri")
    d = svc.add_rule(kind="due", hours_before=24)
    labels = {r["id"]: r["label"] for r in svc.list_rules()}
    assert "every Friday at 5:30pm" == labels[w["id"]]
    assert "24h from due" in labels[d["id"]]


def test_toggle_and_remove(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    rule = svc.add_rule(kind="daily", time="09:00")
    assert svc.toggle(rule["id"]) is True
    assert svc.list_rules()[0]["enabled"] is False
    assert svc.toggle(rule["id"]) is True
    assert svc.list_rules()[0]["enabled"] is True
    assert svc.remove_rule(rule["id"]) is True
    assert svc.list_rules() == []
    assert svc.remove_rule(rule["id"]) is False


def test_daily_digest_fires_with_real_assignments(tmp_path):
    items = [_item("CSE 142", "Homework 5", 20), _item("MATH 126", "WebAssign 6", 40)]
    svc, _, chats, push = make_service(tmp_path, items)
    rule = svc.add_rule(kind="daily", time="08:00")
    svc._fire(rule["id"])
    # pushed (forced) and dropped into the chat
    assert len(push.sent) == 1
    assert push.sent[0]["force"] is True
    chat_id = chats.ensure_chat()
    msgs = chats.recent_for_brain(chat_id, limit=5)
    body = " ".join(m["content"] if "content" in m else m.get("text", "") for m in msgs)
    assert "Homework 5" in body or "CSE 142" in body


def test_due_check_fires_inside_window_and_dedupes(tmp_path):
    items = [_item("CSE 142", "Quiz 4", 10), _item("PSYCH 101", "Essay", 100)]
    svc, _, _, push = make_service(tmp_path, items)
    svc.add_rule(kind="due", hours_before=24)
    svc._due_check()
    # only the item due in 10h is within the 24h window
    assert len(push.sent) == 1
    assert "Quiz 4" in push.sent[0]["body"]
    # running again does NOT re-notify (dedup)
    svc._due_check()
    assert len(push.sent) == 1


def test_disabled_rule_does_not_fire(tmp_path):
    items = [_item("CSE 142", "Quiz 4", 10)]
    svc, _, _, push = make_service(tmp_path, items)
    rule = svc.add_rule(kind="due", hours_before=24)
    svc.toggle(rule["id"])  # off
    svc._due_check()
    assert push.sent == []


def test_once_schedules_a_job(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    svc.remind_once(2, "your CSE 142 homework is due soon")
    jobs = svc.scheduler.get_jobs()
    assert len(jobs) == 1
    assert list(jobs[0].args) == ["your CSE 142 homework is due soon"]


def test_fire_once_delivers(tmp_path):
    svc, _, _, push = make_service(tmp_path)
    svc._deliver("ping about assignments")
    assert len(push.sent) == 1
    assert push.sent[0]["force"] is True


def test_dispatch_schedule_and_list_and_cancel(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    out = svc.dispatch("schedule_notification", {"kind": "due", "hours_before": 24})
    assert "ok" in out.lower()
    listed = svc.dispatch("list_notifications", {})
    assert "24h from due" in listed
    rid = svc.list_rules()[0]["id"]
    assert "cancel" in svc.dispatch("cancel_notification", {"id": rid}).lower()
    assert svc.dispatch("list_notifications", {}).lower().startswith("no notifications")


def test_dispatch_once_empty_message_uses_digest(tmp_path):
    items = [_item("CSE 142", "Quiz 4", 10)]
    svc, _, _, _ = make_service(tmp_path, items)
    # no message -> server builds the clean what's-due digest, still schedules
    out = svc.dispatch("schedule_notification", {"kind": "once", "in_minutes": 2})
    assert "2 min" in out
    jobs = svc.scheduler.get_jobs()
    assert len(jobs) == 1
    assert "Quiz 4" in jobs[0].args[0]      # the digest, not a run-on the model wrote


def test_digest_is_grouped_and_multiline(tmp_path):
    items = [
        _item("MATH 126", "WebAssign 6", 20),
        _item("PSYCH 101", "Reading Quiz 6", 20),   # same due time -> same group
        _item("CSE 142", "Assignment 5", 44),       # later -> its own group
    ]
    svc, _, _, _ = make_service(tmp_path, items)
    digest = svc._due_digest(7)
    assert digest.startswith("what's due:")
    assert "• MATH 126 - WebAssign 6" in digest
    assert digest.count("\n") >= 5                  # genuinely multi-line, not a run-on


def test_oneoff_shows_in_list_and_is_removable(tmp_path):
    items = [_item("CSE 142", "Quiz 4", 10)]
    svc, _, _, _ = make_service(tmp_path, items)
    svc.add_rule(kind="daily", time="08:00")           # a recurring rule
    svc.remind_once(1, "📋 what's due:\nin 10 hr:\n• CSE 142 - Quiz 4")
    rules = svc.list_rules()
    oneoffs = [r for r in rules if r["oneoff"]]
    assert len(oneoffs) == 1
    assert oneoffs[0]["kind"] == "once"
    assert "in 1 min" in oneoffs[0]["label"]           # shows when it'll alert
    assert "what's due" in oneoffs[0]["detail"]        # preview (first line)
    assert any(not r["oneoff"] for r in rules)          # recurring rule still listed
    # deleting the one-off by its job id removes it from the list
    assert svc.remove_rule(oneoffs[0]["id"]) is True
    assert [r for r in svc.list_rules() if r["oneoff"]] == []


def test_tool_names_match_schemas(tmp_path):
    svc, _, _, _ = make_service(tmp_path)
    assert set(svc.tool_names()) == {"schedule_notification", "list_notifications", "cancel_notification"}
    assert {s["name"] for s in svc.schemas()} == set(svc.tool_names())
