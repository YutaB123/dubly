"""Scheduled notifications the student configures: a daily or weekly "what's
due" digest, a "tell me when an assignment is N hours from due" alert, and
one-off "notify me in N minutes" reminders.

Recurring rules persist in a NotificationStore and are (re)scheduled on the
shared APScheduler at startup. Delivery goes through web push (forced, so it
buzzes even when the app is open) and also drops a copy into the chat so tapping
the notification opens the message. The jobs reference module-level functions by
name so APScheduler's persistent store can re-load them across restarts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.timefmt import PACIFIC, human_due

# The live service, so persisted jobs can find it after a restart.
_ACTIVE: "NotificationService | None" = None

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAY_FULL = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}


def fire_rule(rule_id: str) -> None:
    """Job target for a daily/weekly digest rule."""
    if _ACTIVE is not None:
        _ACTIVE._fire(rule_id)


def fire_due_check() -> None:
    """Job target for the recurring due-soon sweep."""
    if _ACTIVE is not None:
        _ACTIVE._due_check()


def fire_once(message: str) -> None:
    """Job target for a one-off 'remind me in N minutes'."""
    if _ACTIVE is not None:
        _ACTIVE._deliver(message)


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    try:
        h, m = (hhmm or "08:00").split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except Exception:
        return 8, 0


def _clock(hhmm: str) -> str:
    h, m = _parse_hhmm(hhmm)
    ampm = "am" if h < 12 else "pm"
    hh = h % 12 or 12
    return f"{hh}:{m:02d}{ampm}" if m else f"{hh}{ampm}"


def describe(rule: dict) -> str:
    """A short human phrase for a rule, e.g. 'every day at 8am'."""
    kind = rule["kind"]
    if kind == "daily":
        return f"every day at {_clock(rule['hhmm'])}"
    if kind == "weekly":
        day = _WEEKDAY_FULL.get(rule["weekday"], rule["weekday"])
        return f"every {day} at {_clock(rule['hhmm'])}"
    if kind == "due":
        hrs = rule["hours_before"]
        return f"when an assignment is {hrs}h from due"
    return kind


SCHEDULE_TOOLS = [
    {
        "name": "schedule_notification",
        "description": (
            "Set up a notification for the student when they ask to be notified or "
            "reminded on a schedule. Pick the kind:\n"
            "- 'daily': every day at a time. Give 'time' as 'HH:MM' (24h, Pacific). "
            "Sends a what's-due digest automatically unless you pass a 'message'.\n"
            "- 'weekly': every week. Give 'weekday' (mon,tue,wed,thu,fri,sat,sun) and 'time'.\n"
            "- 'due': notify a set number of hours before EACH assignment is due. Give "
            "'hours_before' (e.g. 24 for 'when assignments are 24h away').\n"
            "- 'once': a single reminder a relative time from now. Give 'in_minutes' "
            "(e.g. 2 for 'in 2 minutes'). If it's a 'what's due' reminder, LEAVE 'message' "
            "EMPTY so Dubly sends a clean formatted list — do NOT type the assignments out "
            "yourself. Only set 'message' for genuinely custom text (e.g. 'email your professor').\n"
            "These show up in the student's notifications menu (except 'once')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["daily", "weekly", "due", "once"]},
                "time": {"type": "string", "description": "HH:MM 24h Pacific (daily/weekly)."},
                "weekday": {"type": "string", "description": "mon..sun (weekly)."},
                "hours_before": {"type": "integer", "description": "hours before due (due)."},
                "in_minutes": {"type": "integer", "description": "minutes from now (once)."},
                "message": {"type": "string", "description": "exact text to send (required for once; optional digest override)."},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "list_notifications",
        "description": "List the student's currently scheduled recurring notifications.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_notification",
        "description": "Cancel/remove a scheduled notification by its id (from list_notifications).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
]


class NotificationService:
    def __init__(self, scheduler, store, canvas, chats, push):
        self.scheduler = scheduler
        self.store = store
        self.canvas = canvas
        self.chats = chats
        self.push = push
        global _ACTIVE
        _ACTIVE = self

    # --- startup: (re)schedule everything ------------------------------------

    def start(self) -> None:
        for rule in self.store.list():
            if rule["enabled"]:
                self._schedule_job(rule)
        # one recurring sweep handles every 'due' rule
        self.scheduler.add_job(
            fire_due_check, trigger="interval", minutes=30,
            id="notif_due_check", replace_existing=True, misfire_grace_time=1800,
        )

    # --- delivery ------------------------------------------------------------

    def _deliver(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        chat_id = self.chats.ensure_chat()
        self.chats.append(chat_id, "assistant", text)
        if self.push is not None:
            preview = text if len(text) <= 120 else text[:117] + "…"
            # force=True so it buzzes even if the app happens to be open.
            self.push.notify("Dubly", preview, url=f"/chat?c={chat_id}", force=True)

    def _due_digest(self, days: int) -> str:
        try:
            items = self.canvas.get_upcoming(days=days)
        except Exception:
            items = []
        if not items:
            return "nothing due " + ("today" if days <= 2 else "this week")
        # Group by when it's due so it reads as a short scannable list:
        #   what's due:
        #   Sun 10am:
        #   • MATH 126 - WebAssign 6
        #   • PSYCH 101 - Reading Quiz 6
        groups: dict[str, list[str]] = {}
        order: list[str] = []
        for it in items[:14]:
            when = human_due(it.due_at)
            if when not in groups:
                groups[when] = []
                order.append(when)
            groups[when].append(f"• {it.course} - {it.title}")
        lines = ["what's due:"]
        for when in order:
            lines.append("")
            lines.append(f"{when}:")
            lines.extend(groups[when])
        return "\n".join(lines)

    # --- scheduling a single rule's job --------------------------------------

    def _schedule_job(self, rule: dict) -> None:
        jid = f"notif_{rule['id']}"
        kind = rule["kind"]
        if kind in ("daily", "weekly"):
            h, m = _parse_hhmm(rule["hhmm"])
            kw = dict(
                trigger="cron", hour=h, minute=m, args=[rule["id"]], id=jid,
                replace_existing=True, timezone=PACIFIC, misfire_grace_time=3600,
            )
            if kind == "weekly":
                kw["day_of_week"] = rule["weekday"]
            self.scheduler.add_job(fire_rule, **kw)
        # 'due' rules are handled by the shared due-check sweep (no per-rule job)

    def _unschedule_job(self, rule_id: str) -> None:
        try:
            self.scheduler.remove_job(f"notif_{rule_id}")
        except Exception:
            pass

    # --- job bodies ----------------------------------------------------------

    def _fire(self, rule_id: str) -> None:
        rule = self.store.get(rule_id)
        if not rule or not rule["enabled"]:
            return
        msg = rule["message"] or self._due_digest(2 if rule["kind"] == "daily" else 7)
        self._deliver(msg)

    def _due_check(self) -> None:
        rules = [r for r in self.store.list() if r["enabled"] and r["kind"] == "due"]
        if not rules:
            return
        try:
            items = self.canvas.get_upcoming(days=14)
        except Exception:
            return
        now = datetime.now(timezone.utc)
        for r in rules:
            hours = r["hours_before"] or 24
            for it in items:
                if it.due_at is None:
                    continue
                secs = (it.due_at - now).total_seconds()
                if 0 < secs <= hours * 3600:
                    key = f"{r['id']}:{it.ref}"
                    if not self.store.was_sent(key):
                        self.store.mark_sent(key)
                        self._deliver(f"{it.course} - {it.title} is due {human_due(it.due_at)}")

    # --- create / list / remove (shared by the tool AND the HTTP menu) -------

    def add_rule(
        self, kind: str, time: str = "", weekday: str = "", hours_before: int = 24,
        message: str = "",
    ) -> dict:
        kind = (kind or "").strip().lower()
        rule = {
            "id": uuid.uuid4().hex[:8],
            "kind": kind,
            "hhmm": (time or "08:00").strip(),
            "weekday": (weekday or "mon").strip().lower()[:3] or "mon",
            "hours_before": int(hours_before or 24),
            "message": (message or "").strip(),
            "enabled": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.save(rule)
        self._schedule_job(rule)
        return rule

    def remind_once(self, in_minutes: int, message: str) -> None:
        run_at = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(in_minutes or 1)))
        self.scheduler.add_job(
            fire_once, trigger="date", run_date=run_at, args=[message],
            id=f"once_{uuid.uuid4().hex[:8]}", replace_existing=True,
            misfire_grace_time=3600,
        )

    def list_rules(self) -> list[dict]:
        out = []
        for r in self.store.list():
            out.append({
                "id": r["id"], "kind": r["kind"], "enabled": r["enabled"],
                "oneoff": False, "label": describe(r), "detail": "",
                "time": r["hhmm"], "weekday": r["weekday"],
                "hours_before": r["hours_before"],
            })
        # pending one-off reminders (date jobs — both schedule-once and
        # set_reminder). They auto-remove once they fire, so the list always
        # reflects what's still coming. Recurring 'notif_' cron/interval jobs
        # are already covered by the store above.
        for job in self.scheduler.get_jobs():
            if job.id == "notif_due_check" or job.id.startswith("notif_"):
                continue
            when = getattr(job.trigger, "run_date", None) or getattr(job, "next_run_time", None)
            if when is None:
                continue  # not a one-shot date job
            msg = (job.args[0] if job.args else "") or "reminder"
            preview = msg.splitlines()[0].strip()
            if len(preview) > 38:
                preview = preview[:37] + "…"
            # round UP for imminent ones so a 1-min reminder isn't "in 0 min".
            secs = (when - datetime.now(timezone.utc)).total_seconds()
            if secs < 0:
                label = "any moment"
            elif secs < 3600:
                label = f"in {max(1, round(secs / 60))} min"
            else:
                label = human_due(when)
            out.append({
                "id": job.id, "kind": "once", "enabled": True, "oneoff": True,
                "label": label, "detail": preview,
                "time": "", "weekday": "", "hours_before": 0,
            })
        return out

    def toggle(self, rule_id: str) -> bool:
        rule = self.store.get(rule_id)
        if not rule:
            return False
        new_state = not rule["enabled"]
        self.store.set_enabled(rule_id, new_state)
        if new_state:
            self._schedule_job({**rule, "enabled": True})
        else:
            self._unschedule_job(rule_id)
        return True

    def remove_rule(self, rule_id: str) -> bool:
        # A stored recurring rule...
        if self.store.get(rule_id) is not None:
            self._unschedule_job(rule_id)
            self.store.remove(rule_id)
            return True
        # ...or a pending one-off job (schedule-once / set_reminder).
        try:
            if self.scheduler.get_job(rule_id) is not None:
                self.scheduler.remove_job(rule_id)
                return True
        except Exception:
            pass
        return False

    # --- ToolBox integration -------------------------------------------------

    def tool_names(self) -> list[str]:
        return [t["name"] for t in SCHEDULE_TOOLS]

    def schemas(self) -> list[dict]:
        return list(SCHEDULE_TOOLS)

    def dispatch(self, name: str, tool_input: dict) -> str:
        if name == "schedule_notification":
            kind = (tool_input.get("kind") or "").strip().lower()
            if kind == "once":
                # No message -> send the clean, server-formatted "what's due"
                # digest. Only use a custom message when one is given.
                msg = (tool_input.get("message") or "").strip() or self._due_digest(7)
                self.remind_once(tool_input.get("in_minutes", 1), msg)
                mins = int(tool_input.get("in_minutes", 1) or 1)
                return f"ok, i'll send that in {mins} min."
            if kind not in ("daily", "weekly", "due"):
                return "(unknown notification kind)"
            rule = self.add_rule(
                kind=kind,
                time=tool_input.get("time", ""),
                weekday=tool_input.get("weekday", ""),
                hours_before=tool_input.get("hours_before", 24),
                message=tool_input.get("message", ""),
            )
            return f"ok, set: {describe(rule)} (id {rule['id']})."
        if name == "list_notifications":
            rules = self.list_rules()
            if not rules:
                return "No notifications scheduled."
            return "\n".join(
                f"[{r['id']}] {r['label']}" + ("" if r["enabled"] else " (off)")
                for r in rules
            )
        if name == "cancel_notification":
            ok = self.remove_rule(tool_input.get("id", ""))
            return "cancelled." if ok else "no notification with that id."
        return f"(unknown notification tool: {name})"
