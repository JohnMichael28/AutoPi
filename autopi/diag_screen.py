"""Touch-rendered diagnostic screen. Reuses the DiagnosticReader model
classes unchanged, but paints results on the pygame touchscreen instead of
the old terminal print()/input() view. Runs a reader ONCE when opened
(on-demand, O(1)), caches formatted lines, scrolls if they overflow."""
import time

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

    KIND_COLORS = {"head": TERM_DIM, "dim": TERM_DIM, "raw": TERM_GREEN,
                   "ai": WARN_YELLOW}

    def open(self, reader, priority=None):
        """priority: optional context-manager factory (VehicleData.priority)
        that pauses live polling so this read gets the OBD port at once."""
        import threading
        self._token = getattr(self, "_token", 0) + 1
        self._last_draw = time.monotonic()   # counts as shown
        self._title = reader.name()
        self._scroll = 0
        self._reader = reader
        self._priority = priority
        self._lines = [("Reading from car...", TERM_DIM)]
        self._loading = True
        # Run the (possibly slow / hang-prone) OBD read off the UI thread so
        # a flaky connection can't freeze the render loop. O(1) work, 1 thread.
        self._thread = threading.Thread(target=self._background_read,
                                        args=(self._token,), daemon=True)
        self._thread.start()

    def _format_raw(self, raw):
        result = {"raw": raw, "plain_english": ""}
        lines = self._format_result(result)
        return lines[:-1]       # drop the empty plain-English line

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

    def _background_read(self, token):
        # 1) RAW first: shown the moment the car answers (green).
        # 2) AI translation afterwards (yellow) - a slow model never holds
        #    the raw data hostage again.
        reader = self._reader
        try:
            if self._priority is not None:
                with self._priority():
                    raw = reader.read()
            else:
                raw = reader.read()
        except Exception as err:
            if token == self._token:
                self._lines = [("Read failed:", TERM_DIM), (str(err), WARN_YELLOW)]
                self._loading = False
            return
        if token != self._token:
            return
        lines = self._format_raw(raw)
        self._lines = lines + [("AI translating...", WARN_YELLOW)]
        self._loading = False

        def status(text):
            if token == self._token:
                self._lines = lines + [(text, WARN_YELLOW)]

        def wanted():
            return self.is_showing(token)
        try:
            try:
                plain = reader.explain(raw, on_status=status, still_wanted=wanted)
            except TypeError:
                plain = reader.explain(raw)       # older reader signature
        except Exception as err:
            plain = "(AI explanation unavailable: " + str(err)[:60] + ")"
        if token != self._token:
            return
        out = list(lines)
        for chunk in self._wrap(plain, WRAP_W):
            out.append((chunk, WARN_YELLOW))
        self._lines = out

    def run_read(self):
        result = self._reader.report()
        self._lines = self._format_result(result)
        self._loading = False

    def set_colored(self, title, items, keep_scroll=False):
        """items: [(text, kind)] with kind head/raw/ai/dim. The one layout
        rule for every screen: dim headers, GREEN raw data, YELLOW meaning."""
        self._token = getattr(self, "_token", 0) + 1
        self._last_draw = time.monotonic()   # counts as shown
        self._title = title
        if not keep_scroll:
            self._scroll = 0
        self._loading = False
        self._reader = None
        colored = []
        for text, kind in items:
            color = self.KIND_COLORS.get(kind, TERM_GREEN)
            if len(text) > WRAP_W:
                for chunk in self._wrap(text, WRAP_W):
                    colored.append((chunk, color))
            else:
                colored.append((text, color))
        self._lines = colored
        return self._token

    def current_token(self):
        return getattr(self, "_token", 0)

    def is_showing(self, token):
        """True while THIS content is still on screen (same token, drawn in
        the last 3 s). Background AI retries stop once you leave."""
        return (token == getattr(self, "_token", 0)
                and time.monotonic() - getattr(self, "_last_draw", 0.0) < 3.0)

    def set_message(self, title, body_lines):
        # For non-reader screens (report, fuel, engineer, clear-codes).
        # Section headers stay dim; the AI explanation block ("WHAT THIS MEANS"
        # / "PLAIN ENGLISH") is yellow; everything else green. Keeps color
        # meaning consistent across every screen: yellow = AI interpretation.
        # Every line is wrapped so long content can't run off the right edge.
        self._token = getattr(self, "_token", 0) + 1
        self._last_draw = time.monotonic()   # counts as shown
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
        # Keeps the line's leading indent on every wrapped piece, so indented
        # steps ("  1. ...") stay lined up instead of snapping to the margin.
        text = str(text)
        indent = text[:len(text) - len(text.lstrip(" "))]
        words = text.split()
        lines = []
        current = ""
        for word in words:
            if len(indent) + len(current) + len(word) + 1 <= width:
                current = (current + " " + word).strip()
            else:
                lines.append(indent + current)
                current = word
        if current:
            lines.append(indent + current)
        return lines if lines else [""]

    # Pixels at the bottom covered by a button bar the UI draws on top
    # (REPORT's SEND TO PHONE, a module's CLEAR bar). Lines never go under it,
    # so scrolling to the end always shows the last line.
    bottom_reserve = 0
    # Hint when nothing to scroll; screens with their own buttons override it.
    idle_hint = "TAP to go back"
    scroll_hint = "TAP up/down to scroll  -  TOP-RIGHT to exit"

    def _visible(self):
        return max(1, (self.height - 96 - self.bottom_reserve) // 24)

    def scroll(self, direction):
        self._scroll = max(0, self._scroll + direction)
    
    def scroll_by(self, direction, visible=None):
        # Scroll the content. Clamps so you can't scroll past the ends.
        if visible is None:
            visible = self._visible()
        max_scroll = max(0, len(self._lines) - visible)
        self._scroll = max(0, min(self._scroll + direction, max_scroll))

    def can_scroll(self):
        # True if there's more content than fits on screen.
        return len(self._lines) > self._visible()

    def draw(self, surface):
        self._last_draw = time.monotonic()
        surface.fill(TERM_BG)
        surface.blit(self.font_title.render(self._title, True, TERM_GREEN), (20, 18))
        pygame.draw.line(surface, TERM_DIM, (20, 54), (self.width - 20, 54), 2)
        y = 66
        line_h = 24
        visible = self._visible()
        for text, color in self._lines[self._scroll:self._scroll + visible]:
            surface.blit(self.font_body.render(text, True, color), (24, y))
            y += line_h
        if self.can_scroll():
            hint = self.scroll_hint
        else:
            hint = self.idle_hint
        surface.blit(self.font_hint.render(hint, True, TERM_DIM),
                     (20, self.height - 26 - self.bottom_reserve))