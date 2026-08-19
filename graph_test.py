import pygame
import math
import numpy as np


class LiveGraph:
    """A scrolling real-time line graph, drawn natively in pygame.
    Uses a fixed rolling numpy buffer (efficient - O(n) memory, never grows).
    Styled retro: black bg, glowing line, subtle grid."""

    def __init__(self, x, y, w, h, buffer_size=120,
                 color=(57, 255, 120), label="VALUE", unit="",
                 y_min=0, y_max=100, auto_scale=True):
        self.x = x; self.y = y; self.w = w; self.h = h
        self.buffer_size = buffer_size
        self.color = color
        self.label = label
        self.unit = unit
        self.y_min = y_min
        self.y_max = y_max
        self.auto_scale = auto_scale
        # Rolling buffer - starts full of the midpoint so the line starts flat
        self.data = np.full(buffer_size, (y_min + y_max) / 2.0, dtype=float)
        self.font_lbl = pygame.font.SysFont("consolas", 18, bold=True)
        self.font_val = pygame.font.SysFont("consolas", 32, bold=True)
        self.font_tick = pygame.font.SysFont("consolas", 12)

    def add_point(self, value):
        # Shift the buffer left and drop in the new value (the np.roll technique)
        self.data = np.roll(self.data, -1)
        self.data[-1] = value

    def _value_to_y(self, value, lo, hi):
        # Map a data value to a pixel y-coordinate (inverted: high value = top)
        if hi - lo == 0:
            return self.y + self.h // 2
        frac = (value - lo) / (hi - lo)
        frac = max(0.0, min(1.0, frac))
        return int(self.y + self.h - frac * self.h)

    def draw(self, surface):
        # Determine the y-range (auto-scale to the data, or fixed)
        if self.auto_scale:
            lo = float(np.min(self.data))
            hi = float(np.max(self.data))
            pad = (hi - lo) * 0.15 + 1
            lo -= pad; hi += pad
        else:
            lo, hi = self.y_min, self.y_max

        # --- Panel background (slightly lighter than pure black) ---
        pygame.draw.rect(surface, (16, 16, 20), (self.x, self.y, self.w, self.h))
        # --- Border ---
        pygame.draw.rect(surface, (50, 50, 60), (self.x, self.y, self.w, self.h), 2)

        # --- Grid lines (subtle, retro) ---
        for i in range(1, 4):
            gy = self.y + i * self.h // 4
            pygame.draw.line(surface, (35, 35, 42), (self.x, gy), (self.x+self.w, gy), 1)
        for i in range(1, 6):
            gx = self.x + i * self.w // 6
            pygame.draw.line(surface, (30, 30, 36), (gx, self.y), (gx, self.y+self.h), 1)

        # --- The data line ---
        points = []
        n = len(self.data)
        for i in range(n):
            px = self.x + int(i / (n - 1) * self.w)
            py = self._value_to_y(self.data[i], lo, hi)
            points.append((px, py))

        # Glow effect: draw a thick dim line under a bright thin line
        dim = (self.color[0]//3, self.color[1]//3, self.color[2]//3)
        if len(points) > 1:
            pygame.draw.lines(surface, dim, False, points, 6)     # glow
            pygame.draw.lines(surface, self.color, False, points, 2)  # bright core

        # --- Current value (big, top-right of panel) ---
        current = self.data[-1]
        val_str = str(round(current, 1)) + self.unit
        val = self.font_val.render(val_str, True, self.color)
        surface.blit(val, (self.x + self.w - val.get_width() - 12, self.y + 8))

        # --- Label (top-left) ---
        lbl = self.font_lbl.render(self.label, True, (150, 150, 160))
        surface.blit(lbl, (self.x + 10, self.y + 10))

        # --- Y-axis min/max ticks ---
        hi_t = self.font_tick.render(str(round(hi, 1)), True, (90, 90, 100))
        lo_t = self.font_tick.render(str(round(lo, 1)), True, (90, 90, 100))
        surface.blit(hi_t, (self.x + 4, self.y + 2))
        surface.blit(lo_t, (self.x + 4, self.y + self.h - 16))


def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("AutoPi - Live Graphs")
    clock = pygame.time.Clock()

    # Three stacked graphs: boost, RPM, coolant
    boost_g = LiveGraph(20, 20, 760, 140, color=(57, 255, 120),
                        label="BOOST", unit="psi")
    rpm_g = LiveGraph(20, 175, 760, 140, color=(40, 230, 200),
                      label="RPM", unit="")
    coolant_g = LiveGraph(20, 330, 760, 130, color=(255, 140, 60),
                          label="COOLANT", unit="C")

    # Simulated values
    boost = 8.0; rpm = 2400; coolant = 90
    t = 0
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT: running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False

        # Simulate realistic-ish changing values (add one point per frame tick)
        t += 1
        boost = 8 + 5 * math.sin(t * 0.05) + np.random.uniform(-0.8, 0.8)
        rpm = 2500 + 1200 * math.sin(t * 0.03) + np.random.uniform(-100, 100)
        coolant = 90 + 8 * math.sin(t * 0.01) + np.random.uniform(-0.5, 0.5)

        # Add points a few times a second (not every frame - realistic sensor rate)
        if t % 6 == 0:
            boost_g.add_point(boost)
            rpm_g.add_point(rpm)
            coolant_g.add_point(coolant)

        screen.fill((10, 10, 12))
        boost_g.draw(screen)
        rpm_g.draw(screen)
        coolant_g.draw(screen)

        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == "__main__":
    main()