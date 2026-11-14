"""Keeps the SD card clean by itself, so AutoPi never needs a person to
delete old files and never fills the card.

What it does (run once per boot, takes well under a second):
- MONTHLY ROLL: the live logs (driving log, warning log) are moved into
  data/archive/ as <name>_YYYY-MM.csv when a new month starts. The app then
  starts a fresh file. Moving a file is one rename - no copying, no extra
  SD writes.
- AGE LIMIT: archived logs older than keep_days are deleted. The trained ML
  model keeps what it learned; it only needs recent driving to retrain.
- SIZE LIMIT: if the archive is bigger than max_archive_mb, the oldest
  archived files go first.
- REPORTS: only the newest keep_reports mechanic reports are kept
  (reports/latest.html is always kept).
- TEXT LOGS: boot.log / security.jsonl are rotated to .1 when they get big,
  so there is always the current file plus one older copy.
- LOW SPACE: if the card is nearly full, the oldest archives are deleted
  first; if it is still nearly full, low_space is reported so the app stops
  logging instead of filling the card.

It NEVER touches: config.json, models/ (the trained ML model), the module
list cache, reports/latest.html, or any code.

All paths and limits come from the "storage" block in config.json.
"""
import os
import shutil
import time


DEFAULTS = {
    "archive_dir": "data/archive",
    "roll": ["data/driving_log_v2.csv", "data/warning_log.csv"],
    "legacy": ["data/driving_log.csv"],
    "keep_days": 90,
    "max_archive_mb": 2048,
    "reports_dir": "reports",
    "keep_reports": 20,
    "text_logs": ["logs/boot.log", "logs/security.jsonl"],
    "max_text_log_kb": 512,
    "min_free_mb": 500,
    "protect": ["config.json", "models", "data/modules.json",
                "reports/latest.html"],
}

MB = 1024 * 1024


class StorageKeeper:
    def __init__(self, cfg=None, root=".", clock=time.time,
                 disk_usage=shutil.disk_usage):
        self._c = dict(DEFAULTS)
        self._c.update(cfg or {})
        self._root = root
        self._clock = clock
        self._disk_usage = disk_usage
        self.last = None            # summary of the last run()

    # ---- paths ----------------------------------------------------------
    def _p(self, rel):
        return os.path.join(self._root, rel)

    def _protected(self, path):
        full = os.path.abspath(path)
        for rel in self._c["protect"]:
            keep = os.path.abspath(self._p(rel))
            if full == keep or full.startswith(keep + os.sep):
                return True
        return False

    def _delete(self, path, summary):
        # The one place files are removed - and it refuses protected ones.
        if self._protected(path):
            summary["notes"].append("refused to delete " + path)
            return 0
        try:
            size = os.path.getsize(path)
            os.remove(path)
            summary["deleted"].append(os.path.relpath(path, self._root))
            return size
        except OSError:
            return 0

    def free_mb(self):
        try:
            return int(self._disk_usage(self._p(".")).free / MB)
        except Exception:
            return -1

    # ---- the steps ------------------------------------------------------
    def _month(self, ts):
        return time.strftime("%Y-%m", time.localtime(ts))

    def _archive_name(self, path, month):
        stem, ext = os.path.splitext(os.path.basename(path))
        name = stem + "_" + month + ext
        dest = os.path.join(self._p(self._c["archive_dir"]), name)
        n = 2
        while os.path.exists(dest):          # never overwrite an archive
            dest = os.path.join(self._p(self._c["archive_dir"]),
                                stem + "_" + month + "_" + str(n) + ext)
            n += 1
        return dest

    def _move_to_archive(self, path, month, summary):
        os.makedirs(self._p(self._c["archive_dir"]), exist_ok=True)
        dest = self._archive_name(path, month)
        try:
            os.replace(path, dest)           # rename keeps the file's date
            summary["rolled"].append(os.path.relpath(dest, self._root))
        except OSError as err:
            summary["notes"].append("could not archive " + path + ": " + str(err))

    def roll_monthly(self, summary):
        now_month = self._month(self._clock())
        for rel in self._c["roll"]:
            path = self._p(rel)
            if not os.path.exists(path):
                continue
            file_month = self._month(os.path.getmtime(path))
            if file_month != now_month:
                self._move_to_archive(path, file_month, summary)
        # Old-format logs that nothing writes to any more: archive them so
        # the same age limit cleans them up later.
        for rel in self._c["legacy"]:
            path = self._p(rel)
            if os.path.exists(path):
                self._move_to_archive(path, self._month(os.path.getmtime(path)),
                                      summary)

    def _archive_files(self):
        folder = self._p(self._c["archive_dir"])
        if not os.path.isdir(folder):
            return []
        files = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                files.append((os.path.getmtime(path), path))
        files.sort()                         # oldest first
        return files

    def prune_by_age(self, summary):
        cutoff = self._clock() - self._c["keep_days"] * 86400
        freed = 0
        for mtime, path in self._archive_files():
            if mtime < cutoff:
                freed += self._delete(path, summary)
        return freed

    def prune_by_size(self, summary):
        files = self._archive_files()
        total = sum(os.path.getsize(p) for _, p in files)
        limit = self._c["max_archive_mb"] * MB
        freed = 0
        for _, path in files:
            if total <= limit:
                break
            size = self._delete(path, summary)
            total -= size
            freed += size
        return freed

    def prune_reports(self, summary):
        folder = self._p(self._c["reports_dir"])
        if not os.path.isdir(folder):
            return 0
        # A report is a .html + .txt pair sharing one name; keep the newest N.
        stems = {}
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or self._protected(path):
                continue
            stem = os.path.splitext(name)[0]
            stems.setdefault(stem, []).append(path)
        ordered = sorted(stems.items(),
                         key=lambda kv: max(os.path.getmtime(p) for p in kv[1]),
                         reverse=True)
        freed = 0
        for _, paths in ordered[self._c["keep_reports"]:]:
            for path in paths:
                freed += self._delete(path, summary)
        return freed

    def rotate_text_logs(self, summary):
        limit = self._c["max_text_log_kb"] * 1024
        for rel in self._c["text_logs"]:
            path = self._p(rel)
            try:
                if os.path.exists(path) and os.path.getsize(path) > limit:
                    os.replace(path, path + ".1")    # replaces the older .1
                    summary["rotated"].append(rel)
            except OSError:
                pass

    def free_space(self, summary):
        # Card nearly full: delete the oldest archives until there is room.
        floor = self._c["min_free_mb"]
        freed = 0
        for _, path in self._archive_files():
            free = self.free_mb()
            if free < 0 or free >= floor:
                break
            freed += self._delete(path, summary)
        return freed

    # ---- the whole job --------------------------------------------------
    def run(self):
        summary = {"rolled": [], "deleted": [], "rotated": [], "notes": [],
                   "freed_mb": 0.0, "free_mb": -1, "low_space": False}
        freed = 0
        try:
            self.roll_monthly(summary)
            freed += self.prune_by_age(summary)
            freed += self.prune_by_size(summary)
            freed += self.prune_reports(summary)
            self.rotate_text_logs(summary)
            freed += self.free_space(summary)
        except Exception as err:             # cleanup must never stop boot
            summary["notes"].append("cleanup error: " + str(err)[:60])
        summary["freed_mb"] = round(freed / float(MB), 1)
        summary["free_mb"] = self.free_mb()
        summary["low_space"] = 0 <= summary["free_mb"] < self._c["min_free_mb"]
        self.last = summary
        return summary

    def describe(self, summary=None):
        """One short line for the boot screen."""
        s = summary or self.last or {}
        if s.get("low_space"):
            return "SD card nearly full: " + str(s.get("free_mb")) + " MB free - logging paused"
        parts = [str(s.get("free_mb", "?")) + " MB free"]
        if s.get("rolled"):
            parts.append(str(len(s["rolled"])) + " archived")
        if s.get("deleted"):
            parts.append(str(len(s["deleted"])) + " old files removed")
        return ", ".join(parts)
