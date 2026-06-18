"""Proactive alerts: a background poller that pushes new grades + due-soon items.

Runs in a daemon thread (the server is always-on), checks Canvas on an interval,
and notifies via web push. State is remembered so the same thing isn't re-pushed.
The first time a grade is seen it's recorded silently (a baseline), so going live
doesn't blast every existing grade at the student.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.canvas import is_real_class, short_course_code


class AlertService:
    def __init__(self, canvas: Any, push: Any, store: Any, interval: int = 1800):
        self.canvas = canvas
        self.push = push
        self.store = store
        self.interval = interval

    def check_once(self) -> None:
        # --- new / changed grades ---
        try:
            grades = self.canvas.get_grades()
        except Exception:
            grades = []
        for g in grades:
            if not is_real_class(g.course) or g.score is None:
                continue
            prev = self.store.grade_for(g.course)
            if prev is None:
                self.store.set_grade(g.course, g.score)  # baseline, stay quiet
            elif abs(g.score - prev) > 0.009:
                self.push.notify("📊 New grade", f"{short_course_code(g.course)}: {g.score:g}%")
                self.store.set_grade(g.course, g.score)

        # --- assignments due soon (next couple of days) ---
        try:
            items = self.canvas.get_upcoming(days=2)
        except Exception:
            items = []
        for it in items:
            ref = getattr(it, "ref", "")
            if not ref or self.store.was_due_alerted(ref):
                continue
            self.push.notify("⏰ Due soon", f"{it.title} ({it.course})")
            self.store.mark_due_alerted(ref)

    def _loop(self) -> None:
        while True:
            try:
                self.check_once()
            except Exception:
                pass
            time.sleep(self.interval)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()
