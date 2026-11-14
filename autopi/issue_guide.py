"""IssueGuide: when a warning or ML anomaly KEEPS happening, a pop-up banner
says what it is and which graph shows it. It never switches the screen by
itself - tap the banner to open that graph, or ignore it.

Pure logic (no pygame), so it is unit-testable with a fake clock:
    event = guide.update(active_warnings, ml_result)
    event -> None, or {"key", "title", "say", "page"}

Rules (config.json "issue_guide"):
  persist_s   - an issue must stay active this long before we interrupt
                (warnings are already debounced; this stops one-off blips)
  cooldown_s  - the same issue never interrupts twice within this time
  ml_levels   - ML severities that count ("elevated" = sustained anomaly)
"""
import time


class IssueGuide:
    DEFAULTS = {"persist_s": 8.0, "cooldown_s": 300.0, "ml_levels": ["elevated"]}

    def __init__(self, playbook, cfg=None, clock=None):
        merged = dict(self.DEFAULTS)
        if cfg:
            merged.update(cfg)
        self._c = merged
        self._book = playbook
        self._clock = clock or time.monotonic
        self._first_seen = {}
        self._last_fired = {}

    def _active(self, warnings, ml):
        items = []                      # (key, title, page, first_step), priority order
        for w in warnings or []:
            key = self._book.warning_key(w)
            steps = self._book.steps_for_warning(w)
            items.append((key, w, self._book.page_for_warning(w),
                          self._first_action(steps)))
        ml = ml or {}
        if ml.get("severity") in self._c["ml_levels"]:
            culprits = ml.get("culprits", []) or []
            top = culprits[0][0] if culprits else "readings"
            steps = self._book.steps_for_anomaly(ml)
            items.append(("ML:" + top, "ML: " + self._book.plain_name(top)
                          + " unusual for this car",
                          self._book.page_for_anomaly(ml),
                          self._first_action(steps)))
        return items

    @staticmethod
    def _first_action(steps):
        # Skip explanation lines ("Lean = the ECU is adding fuel") and speak
        # the first thing the driver can actually DO.
        for step in steps:
            if " = " not in step:
                return step
        return steps[0] if steps else ""

    def update(self, warnings, ml_result):
        now = self._clock()
        items = self._active(warnings, ml_result)
        keys = set(i[0] for i in items)
        for key in list(self._first_seen):
            if key not in keys:
                del self._first_seen[key]
        for key, title, page, step in items:
            first = self._first_seen.setdefault(key, now)
            if now - first < self._c["persist_s"]:
                continue
            last = self._last_fired.get(key)
            if last is not None and now - last < self._c["cooldown_s"]:
                continue
            self._last_fired[key] = now
            say = title.rstrip(".") + "."
            if page:
                say += " Look at the " + page + " graph."
            if step:
                say += " First: " + step
            return {"key": key, "title": "LOOK AT: " + (page or "WARNINGS")
                    + (" GRAPH" if page else ""),
                    "say": say, "page": page, "issue": title, "step": step}
        return None
