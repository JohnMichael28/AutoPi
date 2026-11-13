"""BootLoader: the AutoPi start-up screen that ACTUALLY loads everything.

Every line is a real step that runs once at start-up (on a background
thread, so the screen keeps animating): the OBD link, the fast live-data
lane, each AI "bot" and its guardrail self-test, the ML model, and finally
a warm-up question to the on-Pi AI so its model and prompt are already in
memory - the first real question after boot answers much faster.

    steps = [("OBD-II LINK", fn), ...]   fn() -> (state, detail)
    state: "ok" | "warn" | "fail"

The screen is built to be read at a glance: WHAT is loading now, a
progress bar, one square per step, and only the problems spelled out.
Every step's full result goes to logs/boot.log.

Tap to skip: the screen closes but unfinished steps keep loading in the
background. Same update(dt) / draw(surface, frame) / .done interface as the
old BootSequence, so ui.py swaps it in without other changes.
"""
import threading
import time

import pygame


GREEN = (65, 255, 120)
BRIGHT = (150, 255, 190)
DIM = (30, 110, 50)
YELLOW = (255, 215, 40)
RED = (255, 70, 70)
BG = (6, 12, 7)

TAGS = {"ok": ("[ OK ]", GREEN), "warn": ("[WARN]", YELLOW),
        "fail": ("[FAIL]", RED)}

MINI_FACE = ["                ", "                ", "   XX      XX   ",
             "   XX      XX   ", "   XX      XX   ", "                ",
             "                ", "  X        X    ", "   X      X     ",
             "    XXXXXX      ", "                ", "                "]


class BootLoader:
    def __init__(self, width, height, steps, title="AUTOPI GUARDIAN TERMINAL",
                 version="v2.0", hold_s=1.5):
        self.width = width
        self.height = height
        self.title = title
        self.version = version
        self.hold_s = hold_s
        self.done = False
        self._rows = [{"label": label, "fn": fn, "state": "wait", "detail": "",
                       "secs": 0.0} for label, fn in steps]
        self._current = 0
        self._finished = False
        self._hold = 0.0
        self._start = time.monotonic()
        self.font_title = pygame.font.SysFont("consolas", 28, bold=True)
        self.font_head = pygame.font.SysFont("consolas", 26, bold=True)
        self.font = pygame.font.SysFont("consolas", 17, bold=True)
        self.font_small = pygame.font.SysFont("consolas", 14)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---- loading --------------------------------------------------------
    def _run(self):
        for i, row in enumerate(self._rows):
            self._current = i
            row["state"] = "run"
            t0 = time.monotonic()
            try:
                state, detail = row["fn"]()
            except Exception as err:
                state, detail = "fail", str(err)[:60]
            row["secs"] = time.monotonic() - t0
            row["state"] = state if state in TAGS else "warn"
            row["detail"] = str(detail)
        self._current = len(self._rows)
        self._write_log()
        self._finished = True

    def _write_log(self):
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            with open(os.path.join("logs", "boot.log"), "a") as f:
                f.write("=== boot " + time.strftime("%Y-%m-%d %H:%M:%S") + " ===\n")
                for r in self._rows:
                    f.write(r["state"].upper().ljust(5) + " " + r["label"] + " - "
                            + r["detail"] + " (" + str(round(r["secs"], 1)) + " s)\n")
        except Exception:
            pass

    def finished(self):
        return self._finished

    def results(self):
        return [(r["label"], r["state"], r["detail"]) for r in self._rows]

    def update(self, dt):
        if self.done:
            return
        if self._finished:
            self._hold += dt
            problems = [r for r in self._rows if r["state"] in ("warn", "fail")]
            # Give you time to read what went wrong; a clean boot just goes.
            if self._hold >= (self.hold_s + 3.0 if problems else self.hold_s):
                self.done = True

    # ---- drawing --------------------------------------------------------
    # One glance: what is loading right now, a progress bar, one square per
    # step, and ONLY the things that went wrong (in plain words). The full
    # step-by-step log is written to logs/boot.log.
    def draw(self, surface, frame):
        surface.fill(BG)
        title = self.font_title.render(self.title, True, GREEN)
        surface.blit(title, (30, 22))
        ver = self.font_small.render(self.version + "  //  OBD-II + AI GUARDIAN", True, DIM)
        surface.blit(ver, (32, 58))
        self._draw_face(surface, frame)
        pygame.draw.line(surface, DIM, (30, 80), (self.width - 30, 80), 2)

        total = len(self._rows)
        problems = [r for r in self._rows if r["state"] in ("warn", "fail")]
        if self._finished:
            if problems:
                head, color = "READY - " + str(len(problems)) + " NEED ATTENTION", YELLOW
            else:
                head, color = "ALL SYSTEMS READY", BRIGHT
            sub = "loaded in " + str(int(time.monotonic() - self._start)) + " s"
        else:
            row = self._rows[min(self._current, total - 1)]
            dots = "." * ((frame // 12) % 4)
            head, color = "LOADING " + row["label"] + dots, GREEN
            sub = "step " + str(min(self._current + 1, total)) + " of " + str(total)
        big = self.font_head.render(head, True, color)
        surface.blit(big, (self.width // 2 - big.get_width() // 2, 112))
        s2 = self.font_small.render(sub, True, DIM)
        surface.blit(s2, (self.width // 2 - s2.get_width() // 2, 150))

        self._draw_progress(surface, 178)
        self._draw_squares(surface, frame, 214)

        y = 262
        if problems:
            surface.blit(self.font.render("NEEDS ATTENTION:", True, YELLOW), (40, y))
            y += 28
            for r in problems[:4]:
                tag = "FAIL " if r["state"] == "fail" else "WARN "
                line = tag + r["label"] + " - " + r["detail"]
                surface.blit(self.font_small.render(line[:84], True,
                             RED if r["state"] == "fail" else YELLOW), (40, y))
                y += 24
        foot = ("tap to continue" if self._finished
                else "tap to skip - loading continues in the background")
        f = self.font_small.render(foot, True, DIM)
        surface.blit(f, (self.width // 2 - f.get_width() // 2, self.height - 30))
        for sy in range(0, self.height, 3):
            pygame.draw.line(surface, (0, 16, 0), (0, sy), (self.width, sy), 1)

    def _draw_face(self, surface, frame):
        cell = 4
        ox = self.width - 30 - 16 * cell
        oy = 14
        blink = (frame // 90) % 8 == 0
        for r, line in enumerate(MINI_FACE):
            if blink and 2 <= r <= 4:
                continue
            for c, ch in enumerate(line):
                if ch == "X":
                    pygame.draw.rect(surface, GREEN, (ox + c * cell, oy + r * cell, cell - 1, cell - 1))

    def _draw_progress(self, surface, y):
        total = max(1, len(self._rows))
        done = len([r for r in self._rows if r["state"] in TAGS])
        frac = done / float(total)
        x, w, h = 60, self.width - 120, 20
        pygame.draw.rect(surface, DIM, (x, y, w, h), 2)
        pygame.draw.rect(surface, GREEN, (x + 3, y + 3, int((w - 6) * frac), h - 6))
        pct = self.font_small.render(str(int(frac * 100)) + "%", True, BRIGHT)
        surface.blit(pct, (x + w + 8, y + 2))

    def _draw_squares(self, surface, frame, y):
        # One square per step: green ok, yellow warn, red fail, blink = now.
        n = len(self._rows)
        size, gap = 18, 8
        x = self.width // 2 - (n * (size + gap) - gap) // 2
        for i, r in enumerate(self._rows):
            st = r["state"]
            if st in TAGS:
                color = TAGS[st][1]
            elif st == "run":
                color = BRIGHT if (frame // 8) % 2 == 0 else DIM
            else:
                color = (20, 45, 26)
            pygame.draw.rect(surface, color, (x + i * (size + gap), y, size, size))
