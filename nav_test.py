import pygame
import math

# Fallout terminal green
TERM_GREEN = (65, 255, 120)
TERM_DIM = (30, 120, 55)
TERM_BG = (8, 14, 8)

class BootSequence:
    """Fallout-style terminal boot-up: lines type out one at a time,
    then it transitions to the menu. Sets the retro mood on startup."""

    LINES = [
        "AUTOPI TERMINAL v1.0",
        "",
        "INIT: 41 55 54 4F 50 49",
        "LOADING VEHICLE INTERFACE...",
        "SCANNING OBD-II BUS...",
        "AI CO-PILOT: ONLINE",
        "ML ANOMALY ENGINE: READY",
        "",
        "> READY",
    ]

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.font = pygame.font.SysFont("consolas", 24, bold=True)
        self.line_index = 0        # which line we're currently typing
        self.char_index = 0        # which character in that line
        self.timer = 0
        self.char_speed = 0.02     # seconds per character
        self.line_pause = 0.15     # pause between lines
        self.done = False
        self.hold_timer = 0        # pause after "READY" before menu

    def update(self, dt):
        if self.done:
            return
        # After all lines are typed, hold a moment then finish
        if self.line_index >= len(self.LINES):
            self.hold_timer += dt
            if self.hold_timer > 1.2:
                self.done = True
            return

        self.timer += dt
        current_line = self.LINES[self.line_index]

        if self.char_index < len(current_line):
            if self.timer >= self.char_speed:
                self.timer = 0
                self.char_index += 1
        else:
            # line finished - pause, then next line
            if self.timer >= self.line_pause:
                self.timer = 0
                self.line_index += 1
                self.char_index = 0

    def draw(self, surface, frame):
        surface.fill(TERM_BG)
        y = 80
        for i in range(min(self.line_index + 1, len(self.LINES))):
            line = self.LINES[i]
            if i == self.line_index:
                # currently-typing line: show partial + cursor
                shown = line[:self.char_index]
                if (frame // 15) % 2 == 0:
                    shown += "_"
            else:
                shown = line
            # "> READY" is brighter (the payoff line)
            color = (120, 255, 160) if line.startswith(">") else TERM_GREEN
            t = self.font.render(shown, True, color)
            surface.blit(t, (60, y))
            y += 36

        # scanlines over it
        for sy in range(0, self.height, 4):
            pygame.draw.line(surface, (0, 20, 0), (0, sy), (self.width, sy), 1)

class Terminal:
    """Fallout-style terminal menu: glowing green monospace, scanlines,
    a blinking cursor, and a selectable list. Tap (or arrow+enter) to pick."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.font = pygame.font.SysFont("consolas", 24, bold=True)
        self.font_big = pygame.font.SysFont("consolas", 34, bold=True)
        self.selected = 0

    def draw_scanlines(self, surface):
        # Horizontal CRT scanlines (the Fallout look)
        for y in range(0, self.height, 4):
            pygame.draw.line(surface, (0, 20, 0), (0, y), (self.width, y), 1)

    def draw_menu(self, surface, title, items, frame):
        surface.fill(TERM_BG)

        # Title bar
        t = self.font_big.render(title, True, TERM_GREEN)
        surface.blit(t, (40, 30))
        pygame.draw.line(surface, TERM_DIM, (40, 80), (self.width - 40, 80), 2)

        # Menu items
        y = 100
        for i, item in enumerate(items):
            if i == self.selected:
                # Selected: bright, with a > cursor and highlight bar
                pygame.draw.rect(surface, (20, 60, 25), (40, y - 2, self.width - 80, 34))
                prefix = "> "
                color = TERM_GREEN
            else:
                prefix = "  "
                color = TERM_DIM
            line = self.font.render(prefix + item, True, color)
            surface.blit(line, (55, y))
            y += 38

        # Blinking cursor right after the last item (terminal feel)
        if (frame // 30) % 2 == 0:
            cursor = self.font.render("_", True, TERM_GREEN)
            surface.blit(cursor, (55, y + 4))

        # Footer hint - fixed at the very bottom with a divider above it
        pygame.draw.line(surface, TERM_DIM, (40, self.height - 44),
                         (self.width - 40, self.height - 44), 1)
        foot = pygame.font.SysFont("consolas", 15).render(
            "UP/DOWN select   ENTER open   ESC back", True, TERM_DIM)
        surface.blit(foot, (40, self.height - 34))

        # Scanlines over everything (subtle CRT overlay)
        self.draw_scanlines(surface)

    def move(self, direction, count):
        self.selected = (self.selected + direction) % count


# The menu structure - what a mechanic/tuner navigates
MENUS = {
    "MAIN": ["DASHBOARDS", "LIVE GRAPHS", "VIRTUAL DYNO", "DIAGNOSTICS",
             "TUNING MONITOR", "ENGINEER MODE", "REPORT", "BACK TO FACE"],
    "DASHBOARDS": ["EVERYDAY HIGHWAY", "TRACK MODE", "ADVENTURE MODE",
                   "CAMP MODE", "< BACK"],
    "DIAGNOSTICS": ["READ CODES", "FREEZE FRAME", "PENDING CODES",
                    "PERMANENT CODES", "READINESS MONITORS",
                    "MONITOR TESTS (06)", "CLEAR CODES", "< BACK"],
}


def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("AutoPi - Terminal")
    clock = pygame.time.Clock()

    boot = BootSequence(800, 480)
    term = Terminal(800, 480)
    current = "MAIN"
    frame = 0
    booting = True
    running = True

    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if booting:
                    # let the user skip the boot with any key
                    boot.done = True
                elif e.key == pygame.K_ESCAPE:
                    if current == "MAIN":
                        running = False
                    else:
                        current = "MAIN"; term.selected = 0
                elif e.key == pygame.K_UP:
                    term.move(-1, len(MENUS[current]))
                elif e.key == pygame.K_DOWN:
                    term.move(1, len(MENUS[current]))
                elif e.key == pygame.K_RETURN:
                    items = MENUS[current]
                    choice = items[term.selected]
                    if choice in ("< BACK", "BACK TO FACE"):
                        current = "MAIN"; term.selected = 0
                    elif choice == "DASHBOARDS":
                        current = "DASHBOARDS"; term.selected = 0
                    elif choice == "DIAGNOSTICS":
                        current = "DIAGNOSTICS"; term.selected = 0
                    else:
                        print("OPEN:", choice)

        if booting:
            boot.update(dt)
            boot.draw(screen, frame)
            if boot.done:
                booting = False
        else:
            title = current if current != "MAIN" else "MENU"
            term.draw_menu(screen, title, MENUS[current], frame)

        pygame.display.flip()
        frame += 1
    pygame.quit()


if __name__ == "__main__":
    main()