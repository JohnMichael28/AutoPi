"""GraphPages: scan-tool style live graph screens (one system per page).

Every page shows up to 3 stacked strip charts over the last N seconds with
the current value, the min/max seen in that window and a dashed line at the
warning limit. History is kept for EVERY trace all the time, so switching
pages (or being sent to a page by the IssueGuide) shows the last minute
immediately instead of an empty chart.

Samples are time-stamped, so RPM arriving ~10x/s from the fast lane and
fuel trims arriving every few seconds plot on the same honest time axis.
Pages and ranges come from config.json "graph_pages" (DEFAULT_PAGES below
are used if that block is missing).
"""
import time
from collections import deque

import pygame


DEFAULT_PAGES = [
    {"name": "ENGINE", "traces": [
        {"key": "rpm", "label": "RPM", "unit": "", "min": 0, "max": 7000, "warn_high": 6000},
        {"key": "throttle", "label": "THROTTLE", "unit": "%", "min": 0, "max": 100},
        {"key": "engine_load", "label": "LOAD", "unit": "%", "min": 0, "max": 100, "warn_high": 95}]},
    {"name": "FUEL", "traces": [
        {"key": "stft_b1", "label": "SHORT TRIM", "unit": "%", "min": -25, "max": 25, "warn_high": 10, "warn_low": -10},
        {"key": "ltft_b1", "label": "LONG TRIM", "unit": "%", "min": -25, "max": 25, "warn_high": 10, "warn_low": -10},
        {"key": "afr", "label": "CMD AIR/FUEL", "unit": "", "min": 10, "max": 18}]},
    {"name": "TEMPS", "traces": [
        {"key": "coolant_temp", "label": "COOLANT", "unit": "C", "min": 0, "max": 120, "warn_high": 105},
        {"key": "intake_temp", "label": "INTAKE AIR", "unit": "C", "min": -20, "max": 80, "warn_high": 60}]},
    {"name": "AIR + SPARK", "traces": [
        {"key": "boost", "label": "MAF AIRFLOW", "unit": "g/s", "min": 0, "max": 200},
        {"key": "timing", "label": "TIMING", "unit": "deg", "min": -15, "max": 45}]},
    {"name": "O2 / CAT", "traces": [
        {"key": "o2_b1s2", "label": "REAR O2", "unit": "V", "min": 0, "max": 1.1, "warn_high": 0.95, "warn_low": 0.1},
        {"key": "stft_b1", "label": "SHORT TRIM", "unit": "%", "min": -25, "max": 25, "warn_high": 10, "warn_low": -10}]},
    {"name": "DRIVE", "traces": [
        {"key": "speed", "label": "SPEED", "unit": "mph", "min": 0, "max": 100},
        {"key": "rpm", "label": "RPM", "unit": "", "min": 0, "max": 7000, "warn_high": 6000}]},
]

GREEN = (65, 255, 120)
DIM = (30, 120, 55)
GRID = (25, 45, 30)
WARN = (255, 215, 40)
BG = (8, 14, 8)


class StripChart:
    def __init__(self, window_s=60.0):
        self.window_s = window_s
        self.points = deque()          # (t, value)

    def add(self, t, value):
        if not isinstance(value, (int, float)):
            return
        if self.points and self.points[-1][1] == value and t - self.points[-1][0] < 0.5:
            return                     # unchanged repeat - skip, saves draw time
        self.points.append((t, float(value)))
        self._trim(t)

    def _trim(self, now):
        while self.points and now - self.points[0][0] > self.window_s:
            self.points.popleft()

    def latest(self):
        return self.points[-1][1] if self.points else None

    def min_max(self):
        if not self.points:
            return None, None
        vals = [p[1] for p in self.points]
        return min(vals), max(vals)


class GraphPages:
    def __init__(self, width, height, pages=None, window_s=60.0, clock=None):
        self.width = width
        self.height = height
        self.pages = pages or DEFAULT_PAGES
        self.window_s = window_s
        self._clock = clock or time.monotonic
        self.charts = {}
        for page in self.pages:
            for tr in page["traces"]:
                if tr["key"] not in self.charts:
                    self.charts[tr["key"]] = StripChart(window_s)
        self.font_title = pygame.font.SysFont("consolas", 22, bold=True)
        self.font_label = pygame.font.SysFont("consolas", 16, bold=True)
        self.font_value = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_small = pygame.font.SysFont("consolas", 13)

    def names(self):
        return [p["name"] for p in self.pages]

    def index_of(self, name):
        for i, p in enumerate(self.pages):
            if p["name"] == name:
                return i
        return None

    def sample(self, live):
        """Feed one dict of live values (call every frame; cheap)."""
        now = self._clock()
        for key, chart in self.charts.items():
            chart.add(now, live.get(key))
            chart._trim(now)

    # ------------------------------------------------------------------
    def draw(self, surface, page_index, total_label):
        page = self.pages[page_index % len(self.pages)]
        surface.fill(BG)
        title = self.font_title.render(page["name"], True, GREEN)
        surface.blit(title, (20, 12))
        nav = self.font_small.render(total_label + "   < TAP LEFT | TAP RIGHT >   "
                                     + str(int(self.window_s)) + "s window",
                                     True, DIM)
        surface.blit(nav, (self.width - nav.get_width() - 20, 18))
        pygame.draw.line(surface, DIM, (20, 44), (self.width - 20, 44), 1)

        traces = page["traces"][:3]
        top = 52
        bottom = self.height - 30
        slot = (bottom - top) // max(1, len(traces))
        for i, tr in enumerate(traces):
            self._draw_trace(surface, tr, 20, top + i * slot, self.width - 40, slot - 8)
        hint = self.font_small.render("TAP MIDDLE to go back", True, (100, 100, 110))
        surface.blit(hint, (20, self.height - 22))

    def _draw_trace(self, surface, tr, x, y, w, h):
        chart = self.charts[tr["key"]]
        lo = float(tr.get("min", 0))
        hi = float(tr.get("max", 100))
        info_w = 190
        gx = x + info_w
        gw = w - info_w
        pygame.draw.rect(surface, GRID, (gx, y, gw, h), 1)
        for frac in (0.25, 0.5, 0.75):
            yy = int(y + h * frac)
            pygame.draw.line(surface, GRID, (gx, yy), (gx + gw, yy), 1)

        def to_y(v):
            frac = (v - lo) / (hi - lo) if hi != lo else 0.5
            frac = max(0.0, min(1.0, frac))
            return int(y + h - frac * h)

        for limit_key in ("warn_high", "warn_low"):
            if limit_key in tr:
                ly = to_y(float(tr[limit_key]))
                for sx in range(gx, gx + gw, 12):
                    pygame.draw.line(surface, WARN, (sx, ly), (min(sx + 6, gx + gw), ly), 1)

        current = chart.latest()
        in_warn = current is not None and (
            ("warn_high" in tr and current >= tr["warn_high"])
            or ("warn_low" in tr and current <= tr["warn_low"]))
        color = WARN if in_warn else GREEN

        now = self._clock()
        pts = []
        for t, v in chart.points:
            px = int(gx + gw - (now - t) / self.window_s * gw)
            if px >= gx:
                pts.append((px, to_y(v)))
        if pts:
            pts.append((gx + gw, pts[-1][1]))     # hold last value to "now"
        if len(pts) >= 2:
            pygame.draw.lines(surface, color, False, pts, 2)

        label = self.font_label.render(tr["label"], True, DIM)
        surface.blit(label, (x, y))
        if current is None:
            txt = "--"
        elif abs(hi - lo) <= 2:
            txt = str(round(current, 2))
        elif abs(hi - lo) <= 60:
            txt = str(round(current, 1))
        else:
            txt = str(int(round(current)))
        val = self.font_value.render(txt + " " + tr.get("unit", ""), True, color)
        surface.blit(val, (x, y + 20))
        mn, mx = chart.min_max()
        if mn is not None:
            mm = self.font_small.render("min " + str(round(mn, 2)) + "  max "
                                        + str(round(mx, 2)), True, DIM)
            surface.blit(mm, (x, y + 56))
        scale = self.font_small.render(str(hi), True, DIM)
        surface.blit(scale, (gx + 4, y + 2))
        scale = self.font_small.render(str(lo), True, DIM)
        surface.blit(scale, (gx + 4, y + h - 14))
