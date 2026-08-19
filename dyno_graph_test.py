import pygame
import math
import numpy as np


def draw_dashed_line(surface, color, points, width=2, dash=10, gap=6):
    """Draw a dashed polyline (for the torque curve - visually distinct from HP)."""
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
            start = dist
            end = min(dist + dash, seg_len)
            sx = x1 + dx * start; sy = y1 + dy * start
            ex = x1 + dx * end; ey = y1 + dy * end
            pygame.draw.line(surface, color, (sx, sy), (ex, ey), width)
            dist += dash + gap


class DynoGraph:
    """Professional dyno curve: HP (solid) + torque (dashed) vs RPM.
    Torque back-calculated via T = HP*5252/RPM. Integration-ready:
    add_run() takes (rpm, hp) pairs - exactly DynoCalculator's output."""

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
        hps = np.array([p[1] for p in pairs], dtype=float)
        hps = self._smooth(hps, window=5)
        torques = np.where(rpms > 0, hps * 5252.0 / rpms, 0)
        self.runs.append({"name": name, "rpms": rpms, "hps": hps,
                          "torques": torques, "color": color})

    def _smooth(self, arr, window=5):
        # Moving average, but PAD the edges by repeating end values so the
        # curve doesn't nosedive at the start/finish (fixes the cut-off cliff)
        if len(arr) < window:
            return arr
        pad = window // 2
        padded = np.concatenate([np.full(pad, arr[0]), arr, np.full(pad, arr[-1])])
        kernel = np.ones(window) / window
        smoothed = np.convolve(padded, kernel, mode="same")
        return smoothed[pad:-pad]   # trim back to original length, no edge artifact

    def clear(self):
        self.runs = []

    def _to_px(self, rpm, val, rpm_lo, rpm_hi, val_lo, val_hi):
        fx = (rpm - rpm_lo) / (rpm_hi - rpm_lo) if rpm_hi > rpm_lo else 0
        fy = (val - val_lo) / (val_hi - val_lo) if val_hi > val_lo else 0
        return self.x + int(fx * self.w), self.y + self.h - int(fy * self.h)

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
        rpm_lo, rpm_hi = float(all_rpm.min()), float(all_rpm.max())
        val_lo = 0
        val_hi = max(float(all_hp.max()), float(all_tq.max())) * 1.15

        # Grid + RPM ticks
        for i in range(1, 8):
            gx = self.x + i * self.w // 8
            pygame.draw.line(surface, (32,32,40), (gx,self.y), (gx,self.y+self.h), 1)
            rpm_val = rpm_lo + (rpm_hi - rpm_lo) * i / 8
            tick = self.font_tick.render(str(int(rpm_val)), True, (90,90,100))
            surface.blit(tick, (gx - tick.get_width()//2, self.y + self.h + 6))
        for i in range(1, 5):
            gy = self.y + i * self.h // 5
            pygame.draw.line(surface, (32,32,40), (self.x,gy), (self.x+self.w,gy), 1)

        # 5252 crossover line
        if rpm_lo <= 5252 <= rpm_hi:
            cx = self.x + int((5252 - rpm_lo)/(rpm_hi - rpm_lo) * self.w)
            pygame.draw.line(surface, (90,90,50), (cx, self.y+18), (cx, self.y+self.h), 1)
            lbl = self.font_tick.render("5252", True, (150,150,90))
            surface.blit(lbl, (cx - 14, self.y + self.h - 16))

        # Draw curves: HP solid (bright), torque dashed (same color, dimmer)
        for run in self.runs:
            color = run["color"]
            tq_color = (color[0]//2 + 30, color[1]//2 + 30, color[2]//2 + 30)
            hp_pts = [self._to_px(run["rpms"][i], run["hps"][i],
                      rpm_lo, rpm_hi, val_lo, val_hi) for i in range(len(run["rpms"]))]
            tq_pts = [self._to_px(run["rpms"][i], run["torques"][i],
                      rpm_lo, rpm_hi, val_lo, val_hi) for i in range(len(run["rpms"]))]
            if len(tq_pts) > 1:
                draw_dashed_line(surface, tq_color, tq_pts, 2)   # torque = dashed
            if len(hp_pts) > 1:
                pygame.draw.lines(surface, color, False, hp_pts, 3)  # HP = solid
            # Peak HP marker
            peak_i = int(np.argmax(run["hps"]))
            pygame.draw.circle(surface, color, hp_pts[peak_i], 5)

        # Legend (padded down from the top border)
        ly = self.y + 14
        for run in self.runs:
            peak_hp = float(np.max(run["hps"])); peak_tq = float(np.max(run["torques"]))
            txt = (run["name"] + ": " + str(round(peak_hp)) + "hp / " +
                   str(round(peak_tq)) + "tq")
            t = self.font_peak.render(txt, True, run["color"])
            surface.blit(t, (self.x + 12, ly))
            ly += 24

        # HP=solid / TQ=dashed legend hint (bottom-right, inside panel)
        key = self.font_tick.render("solid=HP  dashed=TQ", True, (120,120,130))
        surface.blit(key, (self.x + self.w - key.get_width() - 10, self.y + self.h - 18))

        # Axis labels (clear of the ticks)
        xl = self.font_lbl.render("RPM", True, (150,150,160))
        surface.blit(xl, (self.x + self.w//2 - 15, self.y + self.h + 26))
        yl = self.font_lbl.render("HP / TORQUE", True, (150,150,160))
        surface.blit(yl, (self.x + 6, self.y - 20))


def simulate_pull(peak_hp, peak_rpm, noise=3):
    pairs = []
    for rpm in range(1500, 6500, 50):
        frac = rpm / peak_rpm
        hp = peak_hp * (1.1 * frac - 0.35 * frac**2)
        hp = max(0, hp + np.random.uniform(-noise, noise))
        pairs.append((rpm, hp))
    return pairs


def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("AutoPi - Dyno Graph")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 14)

    graph = DynoGraph(70, 55, 660, 320)
    graph.add_run("STOCK", simulate_pull(260, 5600), (57, 255, 120))

    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT: running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE: running = False
                elif e.key == pygame.K_t:
                    graph.add_run("TUNED", simulate_pull(310, 5400), (40, 230, 200))
                elif e.key == pygame.K_r:
                    graph.clear()
                    graph.add_run("STOCK", simulate_pull(260, 5600), (57, 255, 120))

        screen.fill((10, 10, 12))
        title = pygame.font.SysFont("consolas", 20, bold=True).render(
            "VIRTUAL DYNO - estimated power curve", True, (200, 200, 210))
        screen.blit(title, (70, 16))
        graph.draw(screen)
        # bottom hint - spaced so nothing overlaps
        hint = font.render("T = add TUNED    R = reset    ESC = quit", True, (100,100,110))
        screen.blit(hint, (70, 452))
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == "__main__":
    main()