"""Vertical zoned bar gauge (green -> yellow -> red), the correct display for
live 'right now' values per gauge-design research (a filling bar with colored
threshold zones beats a line graph for at-a-glance monitoring). Shows a fill
whose height tracks the value and whose color shifts at the warn/danger
thresholds. Sourced thresholds come from config.json (Rule 2)."""
import pygame

GREEN = (57, 255, 120)
YELLOW = (255, 215, 40)
RED = (255, 60, 60)
DIM = (30, 120, 55)
BG = (16, 20, 24)


class BarGauge:
    def __init__(self, x, y, w, h, label, cfg):
        self._x = x
        self._y = y
        self._w = w
        self._h = h
        self._label = label
        self._min = cfg.get("min", 0)
        self._max = cfg.get("max", 100)
        self._warn = cfg.get("warn", None)
        self._danger = cfg.get("danger", None)
        self._unit = cfg.get("unit", "")
        self._font_label = pygame.font.SysFont("consolas", 16, bold=True)
        self._font_val = pygame.font.SysFont("consolas", 20, bold=True)

    def _color_for(self, value):
        # Color by zone: danger = red, warn = yellow, else green.
        if self._danger is not None and value >= self._danger:
            return RED
        if self._warn is not None and value >= self._warn:
            return YELLOW
        return GREEN

    def draw(self, surface, value):
        # Frame
        pygame.draw.rect(surface, BG, (self._x, self._y, self._w, self._h))
        pygame.draw.rect(surface, DIM, (self._x, self._y, self._w, self._h), 2)
        # Label above
        lbl = self._font_label.render(self._label, True, DIM)
        surface.blit(lbl, (self._x + self._w // 2 - lbl.get_width() // 2,
                           self._y - 22))
        # No data -> show "--", empty bar
        if not isinstance(value, (int, float)):
            v = self._font_val.render("--", True, DIM)
            surface.blit(v, (self._x + self._w // 2 - v.get_width() // 2,
                             self._y + self._h + 6))
            return
        # Fill height proportional to value within min..max
        span = self._max - self._min
        frac = 0.0 if span <= 0 else (value - self._min) / span
        frac = max(0.0, min(1.0, frac))
        fill_h = int(self._h * frac)
        color = self._color_for(value)
        pygame.draw.rect(surface, color,
                         (self._x + 3, self._y + self._h - fill_h,
                          self._w - 6, fill_h))
        # Warn/danger threshold lines across the bar
        for thresh, tcol in ((self._warn, YELLOW), (self._danger, RED)):
            if thresh is not None and self._min <= thresh <= self._max:
                ty = self._y + self._h - int(self._h * (thresh - self._min) / span)
                pygame.draw.line(surface, tcol, (self._x, ty),
                                 (self._x + self._w, ty), 1)
        # Numeric readout below
        text = str(int(value)) + self._unit
        v = self._font_val.render(text, True, color)
        surface.blit(v, (self._x + self._w // 2 - v.get_width() // 2,
                         self._y + self._h + 6))