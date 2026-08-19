import pygame
import math
import random
import textwrap
from face_state import FaceState

MOODS = {
    "green":     {"rgb": (57, 255, 120),  "text": (185, 100, 255), "label": "ALL GOOD"},
    "red":       {"rgb": (255, 60, 60),   "text": (60, 200, 255),  "label": "WARNING LIGHT"},
    "blue":      {"rgb": (255, 40, 200),  "text": (255, 140, 60),  "label": "INFO / NEEDS GAS"},
    "yellow":    {"rgb": (255, 215, 40),  "text": (185, 100, 255), "label": "ISSUE - ATTENTION"},
    "orange":    {"rgb": (230, 110, 40),  "text": (40, 230, 200),  "label": "ADVENTURE MODE"},
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

# Mouth rows (7-10) that flap while "talking"
TALK_MOUTH_OPEN =  ["     XXXXXX     ","    XX    XX    ","    XX    XX    ","     XXXXXX     "]
TALK_MOUTH_CLOSED= ["                ","    XXXXXXXX    ","    XXXXXXXX    ","                "]


class Face:
    def __init__(self, width=800, height=480):
        self.width = width; self.height = height; self.mood = "green"

    def set_mood(self, mood):
        if mood in MOODS: self.mood = mood

    def draw(self, surface, frame, cell=36, y_offset=35, talking=False):
        color = MOODS[self.mood]["rgb"]
        brightness = 1.0
        if self.mood == "critical":
            brightness = 0.5 + 0.5 * (math.sin(frame * 0.2) + 1) / 2
        lit = (int(color[0]*brightness), int(color[1]*brightness), int(color[2]*brightness))
        grid = [row for row in FACES[self.mood]]  # copy

        # If talking, override the mouth rows (7-10) with flapping mouth
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
                    if blinking and row < 5: continue
                    pygame.draw.rect(surface, lit, (ox+col*cell, oy+row*cell, cell-4, cell-4))
        if blinking:
            ly = oy + 3*cell
            pygame.draw.rect(surface, lit, (ox+2*cell, ly, 4*cell, cell-4))
            pygame.draw.rect(surface, lit, (ox+9*cell, ly, 4*cell, cell-4))


class Speech:
    """Handles the guardian 'talking' - headline + paged 4-line body,
    auto-advancing at a readable pace."""

    LINES_PER_PAGE = 4
    SECONDS_PER_PAGE = 8      # your chosen reading pace

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
        # Wrap the body text to fit the screen width, split into 4-line pages
        self.headline = headline
        wrapped = textwrap.wrap(body, width=52)   # ~52 chars per line at this font
        self.pages = [wrapped[i:i+self.LINES_PER_PAGE]
                      for i in range(0, len(wrapped), self.LINES_PER_PAGE)]
        if not self.pages:
            self.pages = [[""]]
        self.page_index = 0
        self.timer = 0
        self.active = True

    def update(self, dt):
        if not self.active:
            return
        self.timer += dt
        if self.timer >= self.SECONDS_PER_PAGE:
            self.timer = 0
            self.page_index += 1
            if self.page_index >= len(self.pages):
                self.active = False   # done talking

    def draw(self, surface, y_start, head_color, body_color):
        if not self.active:
            return
        # Headline (bright, bold - the "what" you can find instantly)
        head = self.font_head.render(self.headline, True, head_color)
        surface.blit(head, (self.width//2 - head.get_width()//2, y_start))
        # Body lines for current page
        page = self.pages[self.page_index]
        y = y_start + 44
        for line in page:
            t = self.font_body.render(line, True, body_color)
            surface.blit(t, (self.width//2 - t.get_width()//2, y))
            y += 30
        # Page indicator if multiple pages
        if len(self.pages) > 1:
            ind = self.font_body.render(
                str(self.page_index+1) + "/" + str(len(self.pages)), True, (100,100,110))
            surface.blit(ind, (self.width - 70, y_start))


def main():
    pygame.init()
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("AutoPi")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 22, bold=True)
    tiny = pygame.font.SysFont("consolas", 14)

    face = Face(); decider = FaceState(); speech = Speech(800)
    sim = {"coolant_temp": 90, "has_codes": False, "voltage": 14.0, "mode": "highway",
           "critical": False, "needs_gas": False, "speed": 65, "boost": 8.2,
           "afr": 14.7, "fuel": 72}

    # A sample long response to test paging (headline + body)
    SAMPLE_HEAD = "P0301: CYLINDER 1 MISFIRE"
    SAMPLE_BODY = ("Your engine has an irregular firing in cylinder one. This "
                   "usually means the spark plug, ignition coil, or a fuel "
                   "injector in that cylinder needs attention. It can cause "
                   "rough idling and lost power. I'd recommend checking the "
                   "spark plugs first since that's the most common cause, then "
                   "the ignition coil. Drive gently until it's fixed to avoid "
                   "damaging the catalytic converter.")

    running = True; frame = 0
    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT: running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if speech.active:
                        speech.active = False   # ESC dismisses talking first
                    else:
                        running = False         # ESC again quits
                elif e.key == pygame.K_SPACE:
                    if speech.active:
                        speech.active = False   # tap to dismiss while talking
                    else:
                        speech.say(SAMPLE_HEAD, SAMPLE_BODY)  # or start talking
                elif e.key == pygame.K_RETURN:
                    # ENTER advances to the next page manually (skip the wait)
                    if speech.active:
                        speech.timer = speech.SECONDS_PER_PAGE
                elif e.key == pygame.K_1: sim["mode"] = "highway"
                elif e.key == pygame.K_2: sim["mode"] = "track"
                elif e.key == pygame.K_3: sim["mode"] = "adventure"
                elif e.key == pygame.K_4: sim["mode"] = "camp"
                elif e.key == pygame.K_c: sim["has_codes"] = not sim["has_codes"]
                elif e.key == pygame.K_h: sim["coolant_temp"] = 118 if sim["coolant_temp"] < 100 else 90
                elif e.key == pygame.K_g: sim["needs_gas"] = not sim["needs_gas"]

        if frame % 30 == 0:
            sim["speed"] = max(0, sim["speed"] + random.randint(-2, 2))
            sim["boost"] = round(max(-10, min(15, sim["boost"] + random.uniform(-1,1))), 1)

        mood = decider.decide(coolant_temp=sim["coolant_temp"], has_codes=sim["has_codes"],
            voltage=sim["voltage"], mode=sim["mode"], critical=sim["critical"],
            needs_gas=sim["needs_gas"])
        face.set_mood(mood)
        speech.update(dt)

        screen.fill((10, 10, 12))
        accent = MOODS[face.mood]["rgb"]
        text_color = MOODS[face.mood]["text"]

        if speech.active:
            # TALKING: face small at top, text below
            face.draw(screen, frame, cell=18, y_offset=20, talking=True)
            speech.draw(screen, 260, accent, text_color)
        else:
            # NORMAL: full face + stat bar
            # stat bar
            stats = [("BOOST", sim["boost"], "psi"), ("AIR/FUEL", sim["afr"], ""),
                     ("FUEL", sim["fuel"], "%"), ("SPEED", sim["speed"], "")]
            slot = 800 // 4
            fv = pygame.font.SysFont("consolas", 30, bold=True)
            fl = pygame.font.SysFont("consolas", 14)
            for i,(l,v,u) in enumerate(stats):
                cx = i*slot + slot//2
                lbl = fl.render(l, True, (110,110,120))
                screen.blit(lbl,(cx-lbl.get_width()//2,12))
                val = fv.render(str(v)+u, True, accent)
                screen.blit(val,(cx-val.get_width()//2,30))
            pygame.draw.line(screen,(40,40,48),(0,72),(800,72),2)
            face.draw(screen, frame, cell=36, y_offset=100)
            lbl = font.render(MOODS[face.mood]["label"], True, text_color)
            screen.blit(lbl,(400-lbl.get_width()//2, 442))

        hint = tiny.render("SPACE=AutoPi ask  1-4 mode  C code  H heat  G gas  ESC",
                           True, (60,60,70))
        screen.blit(hint,(10,462))
        pygame.display.flip(); frame += 1
    pygame.quit()


if __name__ == "__main__":
    main()