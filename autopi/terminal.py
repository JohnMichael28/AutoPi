import pygame

# Fallout terminal palette
TERM_GREEN = (65, 255, 120)
TERM_DIM = (30, 120, 55)
TERM_BG = (8, 14, 8)

# The menu structure (what a mechanic/tuner navigates)
MENUS = {
    "MAIN": ["DASHBOARDS", "LIVE GRAPHS", "VIRTUAL DYNO", "DIAGNOSTICS",
             "FUEL", "WARNINGS", "TUNING MONITOR", "ENGINEER MODE", "REPORT",
             "BACK TO FACE"],
    "DASHBOARDS": ["EVERYDAY HIGHWAY", "TRACK MODE", "ADVENTURE MODE",
                   "CAMP MODE", "< BACK"],
    "DIAGNOSTICS": ["READ CODES", "FREEZE FRAME", "PENDING CODES",
                    "PERMANENT CODES", "READINESS MONITORS",
                    "MONITOR TESTS (06)", "CLEAR CODES", "< BACK"],
}


class BootSequence:
    """Fallout-style boot-up. Shows REAL system status, not fake claims."""

    def __init__(self, width, height):
        self.width = width; self.height = height
        self.font = pygame.font.SysFont("consolas", 24, bold=True)
        self.line_index = 0; self.char_index = 0
        self.timer = 0; self.char_speed = 0.02; self.line_pause = 0.15
        self.done = False; self.hold_timer = 0
        # Default lines; real status filled in by set_status() before boot.
        self.LINES = [
            "AUTOPI TERMINAL v1.0",
            "",
            "INIT: 41 55 54 4F 50 49",
            "LOADING VEHICLE INTERFACE...",
            "OBD-II: CHECKING...",
            "OFFLINE DIAGNOSTICS: READY",
            "",
            "> READY",
        ]

    def set_status(self, obd_connected, ai_reachable, ml_ready=False):
        obd_line = "OBD-II: CONNECTED" if obd_connected else "OBD-II: WAITING FOR CAR"
        ai_line = "AI CO-PILOT: ONLINE" if ai_reachable else "AI CO-PILOT: OFFLINE (local only)"
        ml_line = "ML ANOMALY MODEL: ACTIVE" if ml_ready else "ML ANOMALY MODEL: LEARNING"
        self.LINES = [
            "AUTOPI TERMINAL v1.0",
            "",
            "INIT: 41 55 54 4F 50 49",
            "LOADING VEHICLE INTERFACE...",
            obd_line,
            ai_line,
            ml_line,
            "OFFLINE DIAGNOSTICS: READY",
            "",
            "> READY",
        ]

    def update(self, dt):
        if self.done:
            return
        if self.line_index >= len(self.LINES):
            self.hold_timer += dt
            if self.hold_timer > 1.2:
                self.done = True
            return
        self.timer += dt
        line = self.LINES[self.line_index]
        if self.char_index < len(line):
            if self.timer >= self.char_speed:
                self.timer = 0; self.char_index += 1
        else:
            if self.timer >= self.line_pause:
                self.timer = 0; self.line_index += 1; self.char_index = 0

    def draw(self, surface, frame):
        surface.fill(TERM_BG)
        y = 80
        for i in range(min(self.line_index + 1, len(self.LINES))):
            line = self.LINES[i]
            if i == self.line_index:
                shown = line[:self.char_index]
                if (frame // 15) % 2 == 0:
                    shown += "_"
            else:
                shown = line
            color = (120, 255, 160) if line.startswith(">") else TERM_GREEN
            surface.blit(self.font.render(shown, True, color), (60, y))
            y += 36
        for sy in range(0, self.height, 4):
            pygame.draw.line(surface, (0, 20, 0), (0, sy), (self.width, sy), 1)


class Terminal:
    """Fallout-style navigable menu: glowing green, scanlines, selection cursor."""

    def __init__(self, width, height):
        self.width = width; self.height = height
        self.font = pygame.font.SysFont("consolas", 24, bold=True)
        self.font_big = pygame.font.SysFont("consolas", 34, bold=True)
        self.selected = 0

    def draw_scanlines(self, surface):
        for y in range(0, self.height, 4):
            pygame.draw.line(surface, (0, 20, 0), (0, y), (self.width, y), 1)

    def draw_menu(self, surface, title, items, frame):
        surface.fill(TERM_BG)
        surface.blit(self.font_big.render(title, True, TERM_GREEN), (40, 30))
        pygame.draw.line(surface, TERM_DIM, (40, 80), (self.width - 40, 80), 2)

        # Scrollable window: only draw rows that fit between the header (y=100)
        # and the footer line (height-44). Scroll so the selected row is always
        # visible - prevents the menu overlapping the bottom instructions when
        # there are more items than fit on screen.
        top = 100
        row_h = 38
        bottom_limit = self.height - 52          # leave room for the footer
        visible = (bottom_limit - top) // row_h  # how many rows fit
        # Compute scroll offset so 'selected' stays in view.
        offset = 0
        if self.selected >= visible:
            offset = self.selected - visible + 1
        offset = max(0, min(offset, max(0, len(items) - visible)))

        y = top
        for i in range(offset, min(offset + visible, len(items))):
            item = items[i]
            if i == self.selected:
                pygame.draw.rect(surface, (20, 60, 25), (40, y - 2, self.width - 80, 34))
                prefix = "> "; color = TERM_GREEN
            else:
                prefix = "  "; color = TERM_DIM
            surface.blit(self.font.render(prefix + item, True, color), (55, y))
            y += row_h

        # Scroll indicators (up/down arrows) when there's more above/below.
        small = pygame.font.SysFont("consolas", 16, bold=True)
        if offset > 0:
            surface.blit(small.render("^ more", True, TERM_DIM), (self.width - 110, 84))
        if offset + visible < len(items):
            surface.blit(small.render("v more", True, TERM_DIM),
                         (self.width - 110, self.height - 68))

        # Remember the current window so row_at() maps taps correctly.
        self._view_offset = offset
        self._view_visible = visible
        self._row_h = row_h
        self._row_top = top

        pygame.draw.line(surface, TERM_DIM, (40, self.height - 44),
                         (self.width - 40, self.height - 44), 1)
        foot = pygame.font.SysFont("consolas", 15).render(
            "TAP an item   -   swipe/tap edges to scroll   -   hold corner to exit",
            True, TERM_DIM)
        surface.blit(foot, (40, self.height - 34))
        self.draw_scanlines(surface)

    def move(self, direction, count):
        self.selected = (self.selected + direction) % count

    def row_at(self, y, count):
        """Return the menu index at pixel-y within the CURRENT scroll window,
        or None if outside the rows. Accounts for the scroll offset so taps map
        to the right item even when scrolled. O(1)."""
        top = getattr(self, "_row_top", 100)
        row_h = getattr(self, "_row_h", 38)
        offset = getattr(self, "_view_offset", 0)
        visible = getattr(self, "_view_visible", count)
        if y < top:
            return None
        row_in_view = (y - top) // row_h
        if row_in_view < 0 or row_in_view >= visible:
            return None
        index = offset + row_in_view
        if 0 <= index < count:
            return index
        return None

    def move(self, direction, count):
        self.selected = (self.selected + direction) % count
    def row_at(self, y, count):
        """Return the menu index at pixel-y, or None if outside the rows.
        Rows start at y=100, each 38px tall (matches draw_menu). O(1)."""
        if y < 100:
            return None
        index = (y - 100) // 38
        if 0 <= index < count:
            return index
        return None