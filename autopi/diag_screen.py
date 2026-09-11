"""Touch-rendered diagnostic screen. Reuses the DiagnosticReader model
classes unchanged, but paints results on the pygame touchscreen instead of
the old terminal print()/input() view. Runs a reader ONCE when opened
(on-demand, O(1)), caches formatted lines, scrolls if they overflow."""
import pygame

TERM_GREEN = (65, 255, 120)
TERM_DIM = (30, 120, 55)
TERM_BG = (8, 14, 8)
WARN_YELLOW = (255, 215, 40)

# Max characters per line before wrapping. Sized for the 18px consolas body
# font at x=24 on an 800px screen (~80 char hard limit); 58 leaves a margin so
# nothing runs off the right edge - including long RAW DATA tuples like DTC
# code+description, which previously were not wrapped at all.
WRAP_W = 58


class DiagScreen:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.font_title = pygame.font.SysFont("consolas", 22, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)
        self.font_hint = pygame.font.SysFont("consolas", 15)
        self._title = ""
        self._lines = []
        self._scroll = 0
        self._loading = False
        self._reader = None

    def open(self, reader):
        import threading
        self._title = reader.name()
        self._scroll = 0
        self._reader = reader
        self._lines = [("Reading from car...", TERM_DIM)]
        self._loading = True
        # Run the (possibly slow / hang-prone) OBD read off the UI thread so
        # a flaky connection can't freeze the render loop. O(1) work, 1 thread.
        self._thread = threading.Thread(target=self._background_read, daemon=True)
        self._thread.start()

    def _format_result(self, result):
        # Shared formatter for both the threaded and sync read paths. Wraps
        # EVERY line (raw data included) so nothing overflows the screen width.
        lines = [("--- RAW DATA ---", TERM_DIM)]
        raw = result["raw"]
        if raw is None:
            lines.append(("Nothing reported.", TERM_GREEN))
        elif isinstance(raw, dict):
            for key in raw:
                for chunk in self._wrap("  " + str(key) + ": " + str(raw[key]), WRAP_W):
                    lines.append((chunk, TERM_GREEN))
        elif isinstance(raw, list):
            for item in raw:
                for chunk in self._wrap("  " + str(item), WRAP_W):
                    lines.append((chunk, TERM_GREEN))
        else:
            for chunk in self._wrap("  " + str(raw), WRAP_W):
                lines.append((chunk, TERM_GREEN))
        lines.append(("", TERM_GREEN))
        lines.append(("--- PLAIN ENGLISH ---", TERM_DIM))
        for chunk in self._wrap(result["plain_english"], WRAP_W):
            lines.append((chunk, WARN_YELLOW))
        return lines

    def _background_read(self):
        try:
            result = self._reader.report()
        except Exception as err:
            self._lines = [("Read failed:", TERM_DIM), (str(err), WARN_YELLOW)]
            self._loading = False
            return
        self._lines = self._format_result(result)
        self._loading = False

    def run_read(self):
        result = self._reader.report()
        self._lines = self._format_result(result)
        self._loading = False

    def set_message(self, title, body_lines):
        # For non-reader screens (report, fuel, engineer, clear-codes).
        # Section headers stay dim; the AI explanation block ("WHAT THIS MEANS"
        # / "PLAIN ENGLISH") is yellow; everything else green. Keeps color
        # meaning consistent across every screen: yellow = AI interpretation.
        # Every line is wrapped so long content can't run off the right edge.
        self._title = title
        self._scroll = 0
        self._loading = False
        self._reader = None
        colored = []
        in_ai = False
        for line in body_lines:
            stripped = line.strip()
            if stripped.startswith("---") and stripped.endswith("---"):
                # Section header. AI-explanation sections flip us to yellow.
                if ("WHAT THIS MEANS" in stripped or "PLAIN ENGLISH" in stripped
                        or "DIAGNOSIS" in stripped):
                    in_ai = True
                else:
                    in_ai = False
                colored.append((line, TERM_DIM))
            else:
                color = WARN_YELLOW if in_ai else TERM_GREEN
                # Wrap long lines; keep short ones as-is (preserves blank lines).
                if len(line) > WRAP_W:
                    for chunk in self._wrap(line, WRAP_W):
                        colored.append((chunk, color))
                else:
                    colored.append((line, color))
        self._lines = colored

    @staticmethod
    def _wrap(text, width):
        words = str(text).split()
        lines = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= width:
                current = (current + " " + word).strip()
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines if lines else [""]

    def scroll(self, direction):
        self._scroll = max(0, self._scroll + direction)
    
    def scroll_by(self, direction, visible=None):
        # Scroll the content. Clamps so you can't scroll past the ends.
        if visible is None:
            visible = (self.height - 96) // 24
        max_scroll = max(0, len(self._lines) - visible)
        self._scroll = max(0, min(self._scroll + direction, max_scroll))

    def can_scroll(self):
        # True if there's more content than fits on screen.
        visible = (self.height - 96) // 24
        return len(self._lines) > visible

    def draw(self, surface):
        surface.fill(TERM_BG)
        surface.blit(self.font_title.render(self._title, True, TERM_GREEN), (20, 18))
        pygame.draw.line(surface, TERM_DIM, (20, 54), (self.width - 20, 54), 2)
        y = 66
        line_h = 24
        visible = (self.height - 96) // line_h
        for text, color in self._lines[self._scroll:self._scroll + visible]:
            surface.blit(self.font_body.render(text, True, color), (24, y))
            y += line_h
        if self.can_scroll():
            hint = "TAP up/down to scroll  -  TOP-RIGHT to exit"
        else:
            hint = "TAP to go back"
        surface.blit(self.font_hint.render(hint, True, TERM_DIM), (20, self.height - 26))