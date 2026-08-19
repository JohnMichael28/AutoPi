import pygame
import math
import numpy as np


def draw_dashed_line(surface, color, points, width=2, dash=10, gap=6):
    """Dashed polyline - used for the torque curve (distinct from solid HP)."""
    if len(points) < 2:
        return
    for i in range(len(points) - 1):
        x1, y1 = points[i]; x2, y2 = points[i+1]
        seg_len = math.hypot(x2 - x1, y2 - y1)
        if seg_len == 0:
            continue
        dx = (x2 - x1) / seg_len; dy = (y2 - y1) / seg_len
        dist = 0
        while dist < seg_len:
            start = dist; end = min(dist + dash, seg_len)
            pygame.draw.line(surface, color,
                (x1 + dx*start, y1 + dy*start), (x1 + dx*end, y1 + dy*end), width)
            dist += dash + gap


class LiveGraph:
    """Scrolling real-time line graph (value vs time). Rolling numpy buffer -
    O(n) memory, never grows. Retro styled with glow."""

    def __init__(self, x, y, w, h, buffer_size=120, color=(57, 255, 120),
                 label="VALUE", unit="", auto_scale=True, y_min=0, y_max=100):
        self.x = x; self.y = y; self.w = w; self.h = h
        self.buffer_size = buffer_size
        self.color = color; self.label = label; self.unit = unit
        self.auto_scale = auto_scale; self.y_min = y_min; self.y_max = y_max
        self.data = np.full(buffer_size, (y_min + y_max) / 2.0, dtype=float)
        self.font_lbl = pygame.font.SysFont("consolas", 18, bold=True)
        self.font_val = pygame.font.SysFont("consolas", 32, bold=True)
        self.font_tick = pygame.font.SysFont("consolas", 12)

    def add_point(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        self.data = np.roll(self.data, -1)
        self.data[-1] = value

    def _value_to_y(self, value, lo, hi):
        if hi - lo == 0:
            return self.y + self.h // 2
        frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
        return int(self.y + self.h - frac * self.h)

    def draw(self, surface):
        if self.auto_scale:
            lo = float(np.min(self.data)); hi = float(np.max(self.data))
            pad = (hi - lo) * 0.15 + 1; lo -= pad; hi += pad
        else:
            lo, hi = self.y_min, self.y_max

        pygame.draw.rect(surface, (16, 16, 20), (self.x, self.y, self.w, self.h))
        pygame.draw.rect(surface, (50, 50, 60), (self.x, self.y, self.w, self.h), 2)

        for i in range(1, 4):
            gy = self.y + i * self.h // 4
            pygame.draw.line(surface, (35, 35, 42), (self.x, gy), (self.x+self.w, gy), 1)
        for i in range(1, 6):
            gx = self.x + i * self.w // 6
            pygame.draw.line(surface, (30, 30, 36), (gx, self.y), (gx, self.y+self.h), 1)

        n = len(self.data)
        points = [(self.x + int(i/(n-1)*self.w), self._value_to_y(self.data[i], lo, hi))
                  for i in range(n)]
        dim = (self.color[0]//3, self.color[1]//3, self.color[2]//3)
        if len(points) > 1:
            pygame.draw.lines(surface, dim, False, points, 6)
            pygame.draw.lines(surface, self.color, False, points, 2)

        val = self.font_val.render(str(round(self.data[-1], 1)) + self.unit, True, self.color)
        surface.blit(val, (self.x + self.w - val.get_width() - 12, self.y + 8))
        lbl = self.font_lbl.render(self.label, True, (150, 150, 160))
        surface.blit(lbl, (self.x + 10, self.y + 10))
        surface.blit(self.font_tick.render(str(round(hi,1)), True, (90,90,100)), (self.x+4, self.y+2))
        surface.blit(self.font_tick.render(str(round(lo,1)), True, (90,90,100)), (self.x+4, self.y+self.h-16))


class DynoGraph:
    """Dyno power curve: HP (solid) + torque (dashed) vs RPM. Torque via
    T=HP*5252/RPM. add_run() takes (rpm, hp) pairs - DynoCalculator's output."""

    def __init__(self, x, y, w, h):
        self.x = x; self.y = y; self.w = w; self.h = h
        self.runs = []
        self.font_title = pygame.font.SysFont("consolas", 22, bold=True)
        self.font_lbl = pygame.font.SysFont("consolas", 14)
        self.font_tick = pygame.font.SysFont("consolas", 12)
        self.font_peak = pygame.font.SysFont("consolas", 16, bold=True)

    def add_run(self, name, rpm_hp_pairs, color):
        if not rpm_hp_pairs:
            return
        pairs = sorted(rpm_hp_pairs, key=lambda p: p[0])
        rpms = np.array([p[0] for p in pairs], dtype=float)
        hps = self._smooth(np.array([p[1] for p in pairs], dtype=float), 5)
        torques = np.where(rpms > 0, hps * 5252.0 / rpms, 0)
        self.runs.append({"name": name, "rpms": rpms, "hps": hps,
                          "torques": torques, "color": color})

    def _smooth(self, arr, window=5):
        if len(arr) < window:
            return arr
        pad = window // 2
        padded = np.concatenate([np.full(pad, arr[0]), arr, np.full(pad, arr[-1])])
        return np.convolve(padded, np.ones(window)/window, mode="same")[pad:-pad]

    def clear(self):
        self.runs = []

    def _to_px(self, rpm, val, rlo, rhi, vlo, vhi):
        fx = (rpm - rlo)/(rhi - rlo) if rhi > rlo else 0
        fy = (val - vlo)/(vhi - vlo) if vhi > vlo else 0
        return self.x + int(fx*self.w), self.y + self.h - int(fy*self.h)

    def draw(self, surface):
        pygame.draw.rect(surface, (16, 16, 20), (self.x, self.y, self.w, self.h))
        pygame.draw.rect(surface, (50, 50, 60), (self.x, self.y, self.w, self.h), 2)
        if not self.runs:
            t = self.font_title.render("NO DYNO DATA - do a pull", True, (100,100,110))
            surface.blit(t, (self.x + self.w//2 - t.get_width()//2, self.y + self.h//2))
            return

        all_rpm = np.concatenate([r["rpms"] for r in self.runs])
        all_hp = np.concatenate([r["hps"] for r in self.runs])
        all_tq = np.concatenate([r["torques"] for r in self.runs])
        rlo, rhi = float(all_rpm.min()), float(all_rpm.max())
        vlo = 0; vhi = max(float(all_hp.max()), float(all_tq.max())) * 1.15

        for i in range(1, 8):
            gx = self.x + i*self.w//8
            pygame.draw.line(surface, (32,32,40), (gx,self.y), (gx,self.y+self.h), 1)
            rv = rlo + (rhi-rlo)*i/8
            tick = self.font_tick.render(str(int(rv)), True, (90,90,100))
            surface.blit(tick, (gx - tick.get_width()//2, self.y+self.h+6))
        for i in range(1, 5):
            gy = self.y + i*self.h//5
            pygame.draw.line(surface, (32,32,40), (self.x,gy), (self.x+self.w,gy), 1)

        if rlo <= 5252 <= rhi:
            cx = self.x + int((5252-rlo)/(rhi-rlo)*self.w)
            pygame.draw.line(surface, (90,90,50), (cx,self.y+18), (cx,self.y+self.h), 1)
            surface.blit(self.font_tick.render("5252", True, (150,150,90)), (cx-14, self.y+self.h-16))

        for run in self.runs:
            color = run["color"]
            tq_c = (color[0]//2+30, color[1]//2+30, color[2]//2+30)
            hp_pts = [self._to_px(run["rpms"][i], run["hps"][i], rlo,rhi,vlo,vhi) for i in range(len(run["rpms"]))]
            tq_pts = [self._to_px(run["rpms"][i], run["torques"][i], rlo,rhi,vlo,vhi) for i in range(len(run["rpms"]))]
            if len(tq_pts) > 1: draw_dashed_line(surface, tq_c, tq_pts, 2)
            if len(hp_pts) > 1: pygame.draw.lines(surface, color, False, hp_pts, 3)
            pk = int(np.argmax(run["hps"])); pygame.draw.circle(surface, color, hp_pts[pk], 5)

        ly = self.y + 14
        for run in self.runs:
            txt = run["name"]+": "+str(round(float(np.max(run["hps"]))))+"hp / "+str(round(float(np.max(run["torques"]))))+"tq"
            surface.blit(self.font_peak.render(txt, True, run["color"]), (self.x+12, ly)); ly += 24
        key = self.font_tick.render("solid=HP  dashed=TQ", True, (120,120,130))
        surface.blit(key, (self.x+self.w-key.get_width()-10, self.y+self.h-18))
        surface.blit(self.font_lbl.render("RPM", True, (150,150,160)), (self.x+self.w//2-15, self.y+self.h+26))
        surface.blit(self.font_lbl.render("HP / TORQUE", True, (150,150,160)), (self.x+6, self.y-20))