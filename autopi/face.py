import pygame
import math
import textwrap

# ---- Color moods (the locked system) ----
MOODS = {
    "green":     {"rgb": (57, 255, 120),  "text": (185, 100, 255), "label": "ALL GOOD"},
    "red":       {"rgb": (255, 60, 60),   "text": (60, 200, 255),  "label": "WARNING LIGHT"},
    "blue":      {"rgb": (255, 40, 200),  "text": (255, 140, 60),  "label": "INFO / NEEDS GAS"},
    "yellow":    {"rgb": (255, 215, 40),  "text": (185, 100, 255), "label": "ISSUE - ATTENTION"},
    "orange":    {"rgb": (230, 145, 35),  "text": (40, 230, 200),  "label": "ADVENTURE MODE"},
    "purple":    {"rgb": (185, 100, 255), "text": (57, 255, 120),  "label": "CAMP / IDLING"},
    "turquoise": {"rgb": (40, 230, 200),  "text": (185, 100, 255), "label": "TRACK MODE"},
    "critical":  {"rgb": (255, 30, 30),   "text": (255, 255, 255), "label": "!! CRITICAL !!"},
}

FACES = {
    "green": ["                ","                ","   XX      XX   ","   XX      XX   ","   XX      XX   ","                ","                ","  X        X    ","   X      X     ","    XXXXXX      ","                ","                ",],
    "red": ["                ","  XXXX    XXXX  ","  XXXX    XXXX  ","  XXXX    XXXX  ","                ","                ","     XXXXXX     ","    XX    XX    ","    XX    XX    ","    XX    XX    ","     XXXXXX     ","                ",],
    "blue": ["                ","                ","   XX      XX   ","   XX      XX   ","   XX      XX   ","                ","                ","                ","    XXXXXXXX    ","    XXXXXXXX    ","                ","                ",],
    "yellow": ["                ","  X        X    ","   XX      XX   ","    XX    XX    ","    XX    XX    ","                ","                ","                ","    XX  XX  X   ","   X  XX  XX    ","                ","                ",],
    "orange": ["   XX      XX   ","  XXXX    XXXX  ","  XXXX    XXXX  ","  XXXX    XXXX  ","   XX      XX   ","                ","  X        X    ","  X        X    ","  XX      XX    ","   XXXXXXXX     ","    XXXXXX      ","                ",],
    "purple": ["                ","                ","                ","  XXXXX  XXXXX  ","  XXXXX  XXXXX  ","                ","                ","                ","                ","     XXXXXX     ","      XXXX      ","                ",],
    "turquoise": ["                ","  XXX        XX ","   XXXX    XXX  ","   XXXX    XXX  ","                ","                ","                ","   XXXXXXXXXX   ","   X X X X X X  ","   XXXXXXXXXX   ","                ","                ",],
    "critical": ["  X        X    ","  XX      XX    ","  XXXX  XXXX    ","   XXX  XXX     ","                ","     XXXXXX     ","    XX    XX    ","    XX    XX    ","    XX    XX    ","    XX    XX    ","     XXXXXX     ","                ",],
}

TALK_MOUTH_OPEN =  ["     XXXXXX     ","    XX    XX    ","    XX    XX    ","     XXXXXX     "]
TALK_MOUTH_CLOSED= ["                ","    XXXXXXXX    ","    XXXXXXXX    ","                "]


class Face:
    """The pixel guardian. Draws a retro face whose color + expression
    reflect the car's mood. Can shrink and flap its mouth while talking."""

    def __init__(self, width=800, height=480):
        self.width = width
        self.height = height
        self.mood = "green"

    def set_mood(self, mood):
        if mood in MOODS:
            self.mood = mood

    def draw(self, surface, frame, cell=36, y_offset=100, talking=False):
        color = MOODS[self.mood]["rgb"]
        brightness = 1.0
        if self.mood == "critical":
            brightness = 0.5 + 0.5 * (math.sin(frame * 0.2) + 1) / 2
        lit = (int(color[0]*brightness), int(color[1]*brightness), int(color[2]*brightness))

        grid = [row for row in FACES[self.mood]]
        if talking:
            mouth = TALK_MOUTH_OPEN if (frame // 6) % 2 == 0 else TALK_MOUTH_CLOSED
            for i, mrow in enumerate(mouth):
                grid[7 + i] = mrow

        bounce = int(math.sin(frame * 0.15) * 8) if (self.mood in ("orange","turquoise") and not talking) else 0
        ox = self.width // 2 - (16 * cell) // 2
        oy = y_offset + bounce
        blinking = (frame % 210) < 7 and not talking

        for row in range(12):
            for col in range(16):
                if col < len(grid[row]) and grid[row][col] == "X":
                    if blinking and row < 5:
                        continue
                    pygame.draw.rect(surface, lit, (ox+col*cell, oy+row*cell, cell-4, cell-4))
        if blinking:
            ly = oy + 3*cell
            pygame.draw.rect(surface, lit, (ox+2*cell, ly, 4*cell, cell-4))
            pygame.draw.rect(surface, lit, (ox+9*cell, ly, 4*cell, cell-4))


class StatBar:
    """Top bar of glanceable live stats. Each stat can have its own color
    (None = mood accent). Stats in a WARNING state flash red (the pro
    'alarm' concept from AEM-style dashes)."""

    WARN_COLOR = (255, 40, 40)

    def __init__(self, width):
        self.width = width
        self.font_val = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_lbl = pygame.font.SysFont("consolas", 14)

    def draw(self, surface, stats, accent, frame=0):
        # stats: list of (label, value, unit, color, is_warning)
        n = len(stats)
        slot = self.width // n
        for i, stat in enumerate(stats):
            label, value, unit = stat[0], stat[1], stat[2]
            color = stat[3] if len(stat) > 3 else None
            is_warn = stat[4] if len(stat) > 4 else False
            cx = i * slot + slot // 2

            # Warning = flashing red; else the given color or mood accent
            if is_warn:
                # flash: bright red on even frames, dim red on odd (attention)
                flash = (frame // 15) % 2 == 0
                use = self.WARN_COLOR if flash else (140, 20, 20)
                lbl_color = self.WARN_COLOR
            else:
                use = color if color is not None else accent
                lbl_color = (110, 110, 120)

            lbl = self.font_lbl.render(label, True, lbl_color)
            surface.blit(lbl, (cx - lbl.get_width()//2, 12))
            val = self.font_val.render(str(value) + unit, True, use)
            surface.blit(val, (cx - val.get_width()//2, 30))
        pygame.draw.line(surface, (40, 40, 48), (0, 72), (self.width, 72), 2)


class Speech:
    """The guardian talking: headline + paged 4-line body, auto-advancing."""

    LINES_PER_PAGE = 4
    SECONDS_PER_PAGE = 8

    def __init__(self, width):
        self.width = width
        self.font_head = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 24)
        self.active = False
        self.headline = ""
        self.pages = []
        self.page_index = 0
        self.timer = 0

    def say(self, headline, body):
        self.headline = headline
        wrapped = textwrap.wrap(body, width=52)
        self.pages = [wrapped[i:i+self.LINES_PER_PAGE]
                      for i in range(0, len(wrapped), self.LINES_PER_PAGE)]
        if not self.pages:
            self.pages = [[""]]
        self.page_index = 0
        self.timer = 0
        self.active = True

    def dismiss(self):
        self.active = False

    def skip_page(self):
        self.timer = self.SECONDS_PER_PAGE

    def update(self, dt):
        if not self.active:
            return
        self.timer += dt
        if self.timer >= self.SECONDS_PER_PAGE:
            self.timer = 0
            self.page_index += 1
            if self.page_index >= len(self.pages):
                self.active = False

    def draw(self, surface, y_start, head_color, body_color):
        if not self.active:
            return
        head = self.font_head.render(self.headline, True, head_color)
        surface.blit(head, (self.width//2 - head.get_width()//2, y_start))
        page = self.pages[self.page_index]
        y = y_start + 44
        for line in page:
            t = self.font_body.render(line, True, body_color)
            surface.blit(t, (self.width//2 - t.get_width()//2, y))
            y += 30
        if len(self.pages) > 1:
            ind = self.font_body.render(
                str(self.page_index+1) + "/" + str(len(self.pages)), True, (100,100,110))
            surface.blit(ind, (self.width - 70, y_start))