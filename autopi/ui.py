import pygame
import json
import time
import threading
import os
from autopi.face import Face, StatBar, Speech, MOODS
from autopi.graphs import LiveGraph, DynoGraph
from autopi.terminal import Terminal, BootSequence, MENUS
from autopi.face_state import FaceState
from autopi.bar_gauge import BarGauge
from autopi.snapshot_thread import SnapshotThread
from autopi.data_logger import DataLogger
from autopi.warning_advisor import WarningAdvisor
from autopi.warning_log import WarningLog
from autopi.anomaly_feed import AnomalyFeed

# Per-mode stat bars (researched from AEM/pro dash systems).
# Format: (label, data_key, unit, warn_threshold, warn_direction)
#   warn fires when value crosses threshold in the "high" or "low" direction.
#   None threshold = no warning on that gauge.
MODE_STATS = {
    "highway": [
        ("MAF", "boost", "g/s", None, None),
        ("COOLANT", "coolant_temp", "C", 105, "high"),
        ("RANGE", "fuel", "mi", 40, "low"),
        ("SPEED", "speed", "", None, None),
    ],
    "track": [
        ("MAF", "boost", "g/s", None, None),
        ("RPM", "rpm", "", 6500, "high"),
        ("AIR/FUEL", "afr", "", 15.5, "high"),
        ("COOLANT", "coolant_temp", "C", 105, "high"),
    ],
    "adventure": [
        ("COOLANT", "coolant_temp", "C", 105, "high"),
        ("LOAD", "engine_load", "%", 95, "high"),
        ("MAF", "boost", "g/s", None, None),
        ("LT TRIM", "ltft_b1", "%", 15, "high"),
    ],
    "camp": [
        ("VOLTAGE", "voltage", "V", 12.2, "low"),
        ("COOLANT", "coolant_temp", "C", 105, "high"),
        ("ST TRIM", "stft_b1", "%", 15, "high"),
        ("IDLE TIME", "run_time", "m", None, None),
    ],
}

class UI:
    """The UI controller (View conductor). Assembles the Face, graphs,
    terminal, and boot into one app with screen states. Reads from a
    data provider (real Vehicle-backed or SimData) - dependency injection."""

    def __init__(self, data_provider, width=800, height=480,
                 vehicle=None, ai_client=None, report_view=None,
                 ai_router=None):
        self.data = data_provider
        self._vehicle = vehicle
        self._ai_client = ai_client
        self._report_view = report_view
        self._data_logger = DataLogger()      # starts the ML data collection
        self._anomaly = AnomalyFeed()   # ML advisory (soft signal, not alarm)
        self._anomaly_result = {"severity": "ok"}
        self._warn_items = []      # tappable (label, payload) for WARNINGS tab
        self._explain_busy = False # guard against double-firing the AI
        self._snapshotter = SnapshotThread(data_provider, interval=0.75,
                                           logger=self._data_logger)
        self._ai_router = ai_router
        with open("config.json") as f:
            cfg = json.load(f)
        touch_cfg = cfg.get("touch", {})
        self._quit_corner = touch_cfg.get("quit_corner", "top_left")
        self._quit_hold = touch_cfg.get("quit_hold_seconds", 2.0)
        self._quit_zone = touch_cfg.get("quit_zone_px", 80)
        self._quit_started = None    # time.monotonic() when hold began, else None
        self.width = width; self.height = height

        self.face = Face(width, height)
        self.statbar = StatBar(width)
        self.speech = Speech(width)
        self.terminal = Terminal(width, height)
        self.boot = BootSequence(width, height)
        # Honest boot status: is the car actually connected? Is AI reachable?
        obd_ok = False
        if vehicle is not None:
            try:
                obd_ok = vehicle.is_connected()
            except Exception:
                obd_ok = False
        ai_ok = False
        if ai_client is not None:
            try:
                # Quick reachability check - does Ollama actually respond?
                ai_ok = ai_client.is_reachable()
            except Exception:
                ai_ok = False
        self.boot.set_status(obd_ok, ai_ok)
        self.decider = FaceState()
        self.advisor = WarningAdvisor()      # sourced rule-based warnings
        self._active_warnings = []           # current one-line warnings
        self._warn_streak = 0                # (kept, harmless)
        self._warn_log = WarningLog()        # timestamped warning history
        self._warn_streak = 0                # debounce auto-surfacing
        
        ml_ready = os.path.exists("models/anomaly_model.joblib")
        self.boot.set_status(obd_ok, ai_ok, ml_ready)
        
        # Live gauges: 4 zoned bars (RPM, Boost, AFR, Load) + coolant trend line.
        # Sourced design - bars for instant values, line for coolant creep.
        with open("config.json") as gf:
            gcfg = json.load(gf).get("gauges", {})
        bar_w = 150
        gap = (self.width - 40 - 4 * bar_w) // 3
        bar_y = 60
        bar_h = 280
        self.bars = []
        specs = [("RPM", "rpm", "rpm"), ("MAF", "boost", "maf"),
                 ("AIR/FUEL", "afr", "afr"), ("LOAD", "engine_load", "load")]
        for i, (label, snap_key, cfg_key) in enumerate(specs):
            bx = 20 + i * (bar_w + gap)
            self.bars.append((snap_key, BarGauge(bx, bar_y, bar_w, bar_h, label,
                                                 gcfg.get(cfg_key, {}))))
        # Coolant trend line across the bottom.
        self.coolant_graph = LiveGraph(20, 380, self.width - 40, 80,
                                       color=(255, 140, 60), label="COOLANT",
                                       unit="C")
        self._coolant_cfg = gcfg.get("coolant", {})
        self.dyno = DynoGraph(70, 55, 660, 320)
        
        self._diag_refresh_frame = 0
        self._active_diag = None      # which screen is live-refreshing
        
        # Screen state machine
        self.state = "boot"     # boot -> face -> menu -> livegraphs / dyno
        self.menu = "MAIN"
        self.frame = 0
        
        self._waiting_title = ""
        self._waiting_msg = ""
        self._spoken_code = None    # last code the guardian has spoken aloud
        
        self._alert_streak = 0      # consecutive knock alerts before auto-popping
        
        # Diagnostics: reuse the DiagnosticReader models, render on touch.
        from autopi.diag_screen import DiagScreen
        from autopi.diagnostics import (FreezeFrameReader, PendingCodesReader,
                                        PermanentCodesReader, ReadinessReader,
                                        MonitorTestReader)
        self.diag_screen = DiagScreen(width, height)
        if vehicle is not None:
            self._readers = {
                "FREEZE FRAME": FreezeFrameReader(vehicle, ai_client),
                "PENDING CODES": PendingCodesReader(vehicle, ai_client),
                "PERMANENT CODES": PermanentCodesReader(vehicle, ai_client),
                "READINESS MONITORS": ReadinessReader(vehicle, ai_client),
                "MONITOR TESTS (06)": MonitorTestReader(vehicle, ai_client),
            }
        else:
            self._readers = {}
        # Tuning: live knock/timing analyzer (pure logic, rendered on touch).
        from autopi.tuning import TuningAnalyzer
        self._tuner = TuningAnalyzer()
        self._last_timing = None    # for timing-drop knock detection
        self._last_timing_watch = None    # for the background knock watcher
        
        # Voice input (push-to-talk). Offline Vosk transcription; answers route
        # through the AI. Built lazily so a mic/model problem can't stop boot.
        self._voice = None
        try:
            with open("config.json") as vf:
                vcfg = json.load(vf).get("voice", {})
            from autopi.voice import VoiceInput
            self._voice = VoiceInput(
                vcfg.get("model_path", "vosk-model-small-en-us-0.15"),
                device_name=vcfg.get("device_name"),
                sample_rate=vcfg.get("sample_rate", 44100),
                record_seconds=vcfg.get("record_seconds", 5))
            print("Voice ready")
        except Exception as err:
            print("Voice unavailable:", err)

    def handle_key(self, key):
        if self.state == "boot":
            self.boot.done = True
            return
        # Speech dismiss/skip take priority when talking
        if self.speech.active:
            if key == pygame.K_SPACE: self.speech.dismiss()
            elif key == pygame.K_RETURN: self.speech.skip_page()
            return

        if self.state == "face":
            if key == pygame.K_m:      # M = open menu (stand-in for menu button/tap)
                self.state = "menu"; self.menu = "MAIN"; self.terminal.selected = 0
            elif key == pygame.K_SPACE:  # SPACE = "AutoPi, talk" (wake word stand-in)
                self._demo_talk()
            elif key == pygame.K_1: self.data.mode = "highway"
            elif key == pygame.K_2: self.data.mode = "track"
            elif key == pygame.K_3: self.data.mode = "adventure"
            elif key == pygame.K_4: self.data.mode = "camp"    
        elif self.state == "menu":
            items = MENUS[self.menu]
            if key == pygame.K_UP: self.terminal.move(-1, len(items))
            elif key == pygame.K_DOWN: self.terminal.move(1, len(items))
            elif key == pygame.K_ESCAPE:
                if self.menu == "MAIN": self.state = "face"
                else: self.menu = "MAIN"; self.terminal.selected = 0
            elif key == pygame.K_RETURN:
                self._menu_select(items[self.terminal.selected])
        elif self.state in ("livegraphs", "dyno", "waiting", "diag", "engineer", "tuning", "confirm_clear", "warnings"):
            if key == pygame.K_ESCAPE:
                self.state = "menu"
                
    def _in_quit_zone(self, pos):
        # True if pos (x, y) is inside the configured quit corner zone.
        x, y = pos
        z = self._quit_zone
        if self._quit_corner == "top_left":
            return x <= z and y <= z
        if self._quit_corner == "top_right":
            return x >= self.width - z and y <= z
        if self._quit_corner == "bottom_left":
            return x <= z and y >= self.height - z
        if self._quit_corner == "bottom_right":
            return x >= self.width - z and y >= self.height - z
        return False
    
    @staticmethod
    def _as_number(value):
        # Graphs need a float; unsupported PIDs return "--". Coerce to 0.0
        # so the graph stays alive with a flat line instead of crashing. O(1).
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def handle_touch_down(self, pos):
        # Quit corner always wins - start the hold timer and do nothing else.
        if self._in_quit_zone(pos):
            self._quit_started = time.monotonic()
            return
        self._quit_started = None
        x, y = pos
        
        if self.state == "confirm_clear":
            # CANCEL is the big safe button (left); CLEAR is the small red one
            # (right). Only a tap squarely inside CLEAR wipes codes; anything
            # else cancels, so a stray tap can never clear.
            cx, cy, cw, ch = self.width - 250, self.height - 90, 210, 60
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                self._clear_codes()
            else:
                self.state = "menu"
            return

        if self.state == "warnings":
            # Top-right corner exits back to menu.
            if x >= self.width - 100 and y <= 100:
                self._active_diag = None
                self.state = "menu"
                return
            # Tap a numbered item to explain it; else scroll if long.
            item = self._warn_item_at(y)
            if item is not None:
                self._explain_item(item)
            elif self.diag_screen.can_scroll():
                if y < self.height // 2:
                    self.diag_screen.scroll_by(-3)
                else:
                    self.diag_screen.scroll_by(3)
            else:
                self._active_diag = None
                self.state = "menu"
            return
        
        if self.state == "boot":
            self.boot.done = True
        elif self.state == "menu":
            items = MENUS[self.menu]
            # Tap the top edge = scroll up a step; bottom edge = scroll down.
            if y <= 92:
                self.terminal.move(-1, len(items))
            elif y >= self.height - 66:
                self.terminal.move(1, len(items))
            else:
                index = self.terminal.row_at(y, len(items))
                if index is not None:
                    self.terminal.selected = index
                    self._menu_select(items[index])
        elif self.state == "dyno":
            if y >= 420 and x <= 180:
                self.data.arm_dyno()          # START button (bottom-left)
            elif y >= 420 and x >= 620:
                self.data.disarm_dyno()       # STOP button (bottom-right)
            else:
                self.state = "menu"           # tap elsewhere = back
        
        elif self.state in ("diag", "report"):
            if x >= self.width - 100 and y <= 100:
                self._active_diag = None
                self.state = "menu"
            elif self.state == "report" and y >= self.height - 60:
                saved = self._save_report(self._last_report_body)
                self.diag_screen.set_message("VEHICLE REPORT",
                    self._last_report_body + ["", saved])
            elif self.diag_screen.can_scroll():
                if y < self.height // 2:
                    self.diag_screen.scroll_by(-3)
                else:
                    self.diag_screen.scroll_by(3)
            else:
                self._active_diag = None
                self.state = "menu"
        elif self.state in ("livegraphs", "waiting", "engineer", "tuning"):
            self.state = "menu"
        elif self.state in ("voice", "voiceanswer"):
            self.state = "face"               # tap cancels the AI, back to face
        elif self.state == "face":
            if self.speech.active:
                self.speech.dismiss()
            elif self._voice is not None and y >= self.height - 70 and x <= 200:
                self._voice.start_listening()
                self.state = "voice"
            else:
                self.state = "menu"
                self.menu = "MAIN"
                self.terminal.selected = 0

    def handle_touch_up(self, pos):
        self._quit_started = None    # lifting finger cancels the quit hold

    def check_quit_hold(self):
        # Returns True when the corner has been held long enough to quit. O(1).
        if self._quit_started is None:
            return False
        return (time.monotonic() - self._quit_started) >= self._quit_hold

    def _menu_select(self, choice):
        if choice in ("< BACK", "BACK TO FACE"):
            if choice == "BACK TO FACE": self.state = "face"
            else: self.menu = "MAIN"; self.terminal.selected = 0
        elif choice == "DASHBOARDS":
            self.menu = "DASHBOARDS"; self.terminal.selected = 0
        elif choice == "DIAGNOSTICS":
            self.menu = "DIAGNOSTICS"; self.terminal.selected = 0
        elif choice == "LIVE GRAPHS":
            self.state = "livegraphs"
        elif choice == "WARNINGS":
            self._show_warnings()
        elif choice == "VIRTUAL DYNO":
            run = self.data.get_dyno_run()
            self.dyno.clear()
            if run:
                self.dyno.add_run("RUN 1", run, (57, 255, 120))
            self.state = "dyno"
        elif choice == "READ CODES":
            self._open_read_codes()
        elif choice in self._readers:
            self._active_diag = ("reader", choice)   # live-refresh this reader
            self.diag_screen.open(self._readers[choice])
            self.state = "diag"
        elif choice == "CLEAR CODES":
            self.state = "confirm_clear"
        elif choice == "EVERYDAY HIGHWAY":
            self.data.mode = "highway"; self.state = "face"
        elif choice == "TRACK MODE":
            self.data.mode = "track"; self.state = "face"
        elif choice == "ADVENTURE MODE":
            self.data.mode = "adventure"; self.state = "face"
        elif choice == "CAMP MODE":
            self.data.mode = "camp"; self.state = "face"
        elif choice == "ENGINEER MODE":
            self.state = "engineer"
        elif choice == "TUNING MONITOR":
            self.state = "tuning"
        elif choice == "REPORT":
            self._open_report()
        elif choice == "FUEL":
            self._open_fuel()
        else:
            self.state = "waiting"
            self._waiting_title = choice
            self._waiting_msg = "WAITING FOR VEHICLE DATA..."
            
    def _open_read_codes(self):
        # Mode 03 - stored/confirmed trouble codes via the vehicle directly.
        if self._vehicle is None:
            self.diag_screen.set_message("READ CODES", ["No vehicle connected."])
            self.state = "diag"; return
        read_failed = False
        try:
            codes = self._vehicle.get_dtcs() or []
        except Exception:
            codes = []
            read_failed = True
        if read_failed:
            body = ["--- READ FAILED ---", "",
                    "Could not read codes from the car.",
                    "The adapter was busy or dropped the query.",
                    "",
                    "This is NOT 'no codes' - if the CEL is on,",
                    "the code is there. Try again with the",
                    "engine running."]
        elif not codes:
            body = ["No stored trouble codes.", "", "Engine is clean."]
        else:
            body = ["Stored trouble codes:", ""]
            for entry in codes:
                code = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
                desc = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else ""
                body.append("  " + str(code) + "  " + str(desc))
        self.diag_screen.set_message("READ CODES", body)
        self.state = "diag"

    def _draw_confirm_clear(self, surface):
        surface.fill((8, 14, 8))
        title = pygame.font.SysFont("consolas", 24, bold=True).render(
            "CLEAR TROUBLE CODES?", True, (255, 215, 40))
        surface.blit(title, (40, 40))
        pygame.draw.line(surface, (30, 120, 55), (40, 82),
                         (self.width - 40, 82), 2)
        body = pygame.font.SysFont("consolas", 19)
        for i, ln in enumerate([
                "This erases stored codes AND freeze-frame",
                "data from the car's computer.",
                "",
                "Only do this AFTER you've noted the codes",
                "and fixed the problem. Permanent codes",
                "won't clear until the monitor re-runs."]):
            surface.blit(body.render(ln, True, (57, 255, 120)), (40, 100 + i * 28))
        btn = pygame.font.SysFont("consolas", 22, bold=True)
        pygame.draw.rect(surface, (57, 255, 120), (40, self.height - 90, 260, 60))
        surface.blit(btn.render("CANCEL", True, (8, 14, 8)), (120, self.height - 74))
        pygame.draw.rect(surface, (200, 50, 50),
                         (self.width - 250, self.height - 90, 210, 60))
        surface.blit(btn.render("CLEAR", True, (255, 255, 255)),
                     (self.width - 180, self.height - 74))
        hint = pygame.font.SysFont("consolas", 14).render(
            "Tap CANCEL or anywhere else to keep your codes", True, (30, 120, 55))
        surface.blit(hint, (40, self.height - 22))
        
    def _clear_codes(self):
        # Mode 04 - clears stored DTCs + freeze frame. NOT permanent codes.
        if self._vehicle is None:
            self.diag_screen.set_message("CLEAR CODES", ["No vehicle connected."])
            self.state = "diag"; return
        ok = self._vehicle.clear_dtcs()
        if ok:
            body = ["Clear command sent.", "",
                    "Stored codes and freeze frame cleared.",
                    "Note: permanent codes remain until",
                    "the monitor re-runs and passes."]
        else:
            body = ["Clear failed - no connection."]
        self.diag_screen.set_message("CLEAR CODES", body)
        self.state = "diag"
    def _open_fuel(self):
        # FUEL SYSTEM tab: all trims + O2 + color verdict + AI explanation.
        # Threaded so the fresh reads never stall the UI.
        self._active_diag = ("fuel", None)
        self.diag_screen.set_message("FUEL SYSTEM", ["Reading fuel system..."])
        self.state = "diag"
        threading.Thread(target=self._build_fuel_screen, daemon=True).start()

    def _build_fuel_screen(self):
        health = self.data.fuel_health()
        t = health["trims"]
        # If nothing reads, we're not connected to a running car. Say so
        # honestly - do NOT let the AI invent a diagnosis from empty data.
        all_missing = all(not isinstance(t.get(k), (int, float))
                          for k in ("stft_b1", "ltft_b1", "stft_b2", "ltft_b2"))
        o2_missing = not isinstance(health["o2"], (int, float))
        if all_missing and o2_missing:
            if self._active_diag == ("fuel", None):
                self.diag_screen.set_message("FUEL SYSTEM", [
                    "--- NOT CONNECTED ---",
                    "No live fuel data.",
                    "",
                    "Plug into the car's OBD port and",
                    "start the engine, then try again."])
            return
        def fmt(label, val):
            return "  " + label.ljust(10) + ": " + (str(val) + "%" if isinstance(val, (int, float)) else "--")
        body = ["--- FUEL TRIMS ---"]
        body.append(fmt("STFT B1", t.get("stft_b1")))
        body.append(fmt("LTFT B1", t.get("ltft_b1")))
        body.append(fmt("STFT B2", t.get("stft_b2")))
        body.append(fmt("LTFT B2", t.get("ltft_b2")))
        o2 = health["o2"]
        body.append("  " + "O2 VOLT".ljust(10) + ": "
                    + (str(round(o2, 2)) + "V" if isinstance(o2, (int, float)) else "--"))
        body.append("")
        body.append("--- STATUS ---")
        vtext = {"ok": "HEALTHY - fuel mix is normal.",
                 "warn": "WATCH - trims drifting (10-15%).",
                 "bad": "PROBLEM - trims beyond 15%."}[health["verdict"]]
        body.append("  " + vtext)
        body.append("")
        # AI DIAGNOSIS of THIS car's actual numbers - not generic theory.
        # This is the device's purpose: interpret the real readings and say
        # what they mean for THIS engine right now.
        if self._ai_client is not None:
            try:
                ai = self._ai_client.ask(
                    "You are a car mechanic reading live fuel data. Give a "
                    "SHORT diagnosis (3 sentences max, plain words, no jargon) "
                    "of what THESE specific numbers mean for the engine's health "
                    "right now. If numbers are small, say it's running well and "
                    "why. If a bank is high, say what that points to. "
                    "Numbers: STFT bank1 " + str(t.get("stft_b1")) + "%, LTFT "
                    "bank1 " + str(t.get("ltft_b1")) + "%, O2 " + str(o2)
                    + "V. Verdict: " + health["verdict"]
                    + ". Talk to the owner directly about THEIR car.")
                if not ai.startswith("AI unavailable"):
                    body.append("--- DIAGNOSIS ---")
                    for chunk in ai.split("\n"):
                        body.extend(self._wrap_line(chunk, 60))
            except Exception:
                pass
        # Only write to the screen if FUEL is STILL the active screen. A late-
        # finishing background thread must not overwrite a screen the user has
        # since navigated away from (fixes the FUEL/REPORT flip-flop race).
        if self._active_diag == ("fuel", None):
            self.diag_screen.set_message("FUEL SYSTEM", body)
            
    def _show_warnings(self):
        # WARNINGS tab: rule-based warnings AND ML anomalies together, each a
        # TAPPABLE numbered item you can tap to get an on-demand AI explanation.
        # Plus this drive's timestamped history. The AI only runs when you tap -
        # never automatically - so it never churns on momentary blips.
        body = []
        self._warn_items = []      # (number, kind, text/payload) for tap-to-explain
        n = 0

        # 1. Active rule-based warnings (tappable).
        if self._active_warnings:
            body.append("--- ACTIVE NOW ---")
            body.append("")
            for w in self._active_warnings:
                n += 1
                self._warn_items.append((n, "warning", w))
                for chunk in self._wrap_line("[" + str(n) + "] " + w, 42):
                    body.append(chunk)
                body.append("")

        # 2. ML anomaly (tappable) - only when the detector is flagging.
        ml = self._anomaly_result
        if ml.get("severity", "ok") != "ok":
            body.append("--- ML ANOMALY ---")
            body.append("")
            n += 1
            culprits = ml.get("culprits", [])
            top = culprits[0][0].upper() if culprits else "readings"
            sev = ml.get("severity", "watch")
            regime = ml.get("regime", "")
            label = ("SUSTAINED anomaly - " + top) if sev == "elevated"                 else ("unusual - " + top)
            self._warn_items.append((n, "anomaly", ml))
            for chunk in self._wrap_line("[" + str(n) + "] ML: " + label, 42):
                body.append(chunk)
            if regime:
                body.append("     (while " + regime + ")")
            body.append("")

        # 3. This drive's history - NOW TAPPABLE too, so you can explain any
        # past warning, not just the active ones. Historical explanations are a
        # bit more general (the live sensor context is gone), but still useful.
        history = self._warn_log.session_entries()
        if history:
            body.append("--- THIS DRIVE (newest first) ---")
            body.append("")
            for stamp, w in history:
                n += 1
                self._warn_items.append((n, "history", w))
                clock = stamp.split(" ")[1] if " " in stamp else stamp
                body.append(clock)
                for chunk in self._wrap_line("[" + str(n) + "] " + w, 42):
                    body.append(chunk)
                body.append("")

        if not body:
            body = ["No warnings this drive.", "", "All systems nominal."]
        elif self._warn_items:
            body.append("")
            body.append("--- TAP A [#] TO EXPLAIN ---")

        self._active_diag = ("warnings", None)
        self._warn_body = body     # remember for tap hit-testing
        self.diag_screen.set_message("WARNINGS", body)
        self.state = "warnings"

    def _warn_item_at(self, y):
        # Which tappable [#] item is at screen-y? diag_screen renders lines from
        # y=66, 24px each, offset by its scroll. Find the line, match its [#].
        line_h = 24
        top = 66
        if y < top:
            return None
        line_idx = (y - top) // line_h + self.diag_screen._scroll
        if line_idx < 0 or line_idx >= len(self._warn_body):
            return None
        text = self._warn_body[line_idx]
        # A tappable line starts with "[N] ". Extract N.
        import re as _re
        m = _re.match(r"\[(\d+)\]", text.strip())
        if not m:
            return None
        num = int(m.group(1))
        for item in self._warn_items:
            if item[0] == num:
                return item
        return None

    def _explain_item(self, item):
        # Fire an ON-DEMAND AI explanation of the tapped warning/anomaly.
        # Threaded so the UI never blocks. Routes through ai_client (home Ollama
        # now; the local SLM will slot into this same path later).
        if self._explain_busy:
            return
        self._explain_busy = True
        num, kind, payload = item
        if kind == "warning":
            title = "EXPLAIN: warning"
            prompt = ("A car diagnostic device raised this warning: '"
                      + str(payload) + "'. Explain in 2-3 plain sentences what "
                      "it means and what the driver should do. Be concrete, no "
                      "jargon.")
        elif kind == "history":
            title = "EXPLAIN: past warning"
            prompt = ("Earlier in this drive the car showed this warning: '"
                      + str(payload) + "'. It may have cleared since. Explain in "
                      "2-3 plain sentences what it means and what the driver "
                      "should do or watch for. Be concrete, no jargon.")
        else:  # anomaly
            culprits = payload.get("culprits", [])
            cul_txt = ", ".join("%s (%.1f sigma from normal)" % (c, z)
                                for c, z in culprits[:4]) or "overall readings"
            regime = payload.get("regime", "normal")
            title = "EXPLAIN: ML anomaly"
            prompt = ("A car's machine-learning monitor flagged an anomaly while "
                      "the engine was in '" + regime + "' operation. The sensors "
                      "furthest from this car's learned normal are: " + cul_txt
                      + ". Explain in 2-3 plain sentences what this pattern might "
                      "mean and whether the driver should be concerned. Be honest "
                      "if it could be benign.")
        self.diag_screen.set_message(title, ["Asking AI...", "",
                                             "(needs WiFi / local AI)"])
        self.state = "diag"
        self._active_diag = None

        def worker():
            if self._ai_client is None:
                lines = ["AI not available.", "",
                         "Connect to WiFi or enable the local model."]
            else:
                try:
                    ans = self._ai_client.ask(prompt)
                except Exception as err:
                    ans = "AI unavailable: " + str(err)
                if ans.startswith("AI unavailable"):
                    ans = ans + ("  (Offline - the local model will "
                                 "answer this once installed.)")
                lines = []
                for chunk in ans.split("\n"):
                    lines.extend(self._wrap_line(chunk, 58))
            self.diag_screen.set_message(title, lines)
            self._explain_busy = False

        threading.Thread(target=worker, daemon=True).start()
        
    def _open_report(self):
        self._active_diag = ("report", None)
        self._build_report()
        self.state = "report"

    def _build_report(self):
        import time
        snap = self._snapshotter.latest()
        body = ["Generated: " + time.strftime("%Y-%m-%d %H:%M:%S"), ""]
        body.append("--- LIVE READINGS ---")
        # (snapshot_key, display_label) - the "boost" key now carries MAF, so
        # it's displayed as MAF to stay honest (this car can't read true boost).
        for key, label in (("rpm", "RPM"), ("speed", "SPEED"),
                           ("coolant_temp", "COOLANT_TEMP"), ("boost", "MAF"),
                           ("voltage", "VOLTAGE"), ("fuel", "FUEL"),
                           ("timing", "TIMING"), ("throttle", "THROTTLE")):
            body.append("  " + label.ljust(14) + ": " + str(snap.get(key, "--")))
        body.append("")
        body.append("--- TROUBLE CODES ---")
        codes = []
        if self._vehicle is not None:
            try:
                codes = self._vehicle.get_dtcs() or []
            except Exception:
                codes = []
        if codes:
            for entry in codes:
                code = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
                desc = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else ""
                body.append("  " + str(code) + "  " + str(desc))
        else:
            body.append("  No trouble codes.")
        body.append("")
        body.append("--- TAP BOTTOM-LEFT TO SAVE ---")
        self._last_report_body = body
        self.diag_screen.set_message("VEHICLE REPORT", body)

    def _save_report(self, body_lines):
        # Safe text dump only. No AI, no ReportView - those hang on Ollama
        # off-WiFi. Text dump can't hang. Returns a status line.
        try:
            import os, time
            os.makedirs("reports", exist_ok=True)
            name = "reports/report_" + time.strftime("%Y%m%d_%H%M%S") + ".txt"
            f = open(name, "w")
            for line in body_lines:
                f.write(line + "\n")
            f.close()
            return "Saved: " + name
        except Exception as err:
            return "Save failed: " + str(err)
        
        
    
    def _answer_question(self, question):
        if not question:
            return ["(Didn't catch that - try again.)"]
        if self._ai_client is None:
            return ["AI not available."]
        snap = self._snapshotter.latest()
        rpm = snap.get("rpm", "--")
        coolant = snap.get("coolant_temp", "--")
        # If nothing's reading, the car isn't connected - don't let the AI
        # invent analysis from empty data. Tell the truth instead.
        if rpm == "--" and coolant == "--":
            return ["No live car data right now.",
                    "Make sure the OBD adapter is connected",
                    "and the engine is running, then ask again."]
        context = ("Current car data: RPM " + str(rpm)
                   + ", coolant " + str(coolant) + "C, voltage "
                   + str(snap.get("voltage", "--")) + "V, speed "
                   + str(snap.get("speed", "--")) + ". ")
        codes = []
        if self._vehicle is not None:
            try:
                codes = self._vehicle.get_dtcs() or []
            except Exception:
                codes = []
        if codes:
            context = context + "Trouble codes present: "
            for entry in codes:
                code = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
                context = context + str(code) + " "
        else:
            context = context + "No trouble codes. "
        try:
            answer = self._ai_client.ask(
                "You are a car diagnostic assistant. The driver's spoken "
                "question was transcribed by imperfect speech recognition and "
                "may have misheard words ('gods'='codes', 'red'='read'). "
                "Interpret what they meant. " + context
                + "Answer their question using this real data, briefly. "
                "Question: " + question)
        except Exception as err:
            answer = "AI unavailable: " + str(err)
        if answer.startswith("AI unavailable"):
            answer = answer + "  (Offline - connect to WiFi for full answers.)"
        lines = []
        for chunk in answer.split("\n"):
            lines.extend(self._wrap_line(chunk, 60))
        return lines

    def _draw_voice_listening(self, surface):
        surface.fill((8, 14, 8))
        status = self._voice.status() if self._voice is not None else "idle"
        label = "LISTENING..." if status == "listening" else "THINKING..."
        f = pygame.font.SysFont("consolas", 32, bold=True)
        t = f.render(label, True, (65, 255, 120))
        surface.blit(t, (self.width//2 - t.get_width()//2, 200))
        hint = pygame.font.SysFont("consolas", 16).render(
            "Speak your question", True, (120, 120, 130))
        surface.blit(hint, (self.width//2 - hint.get_width()//2, 260))    
            
    def _demo_talk(self):
        self.speech.say("P0301: CYLINDER 1 MISFIRE",
            "Your engine has an irregular firing in cylinder one. This usually "
            "means the spark plug or ignition coil needs attention. Drive gently "
            "until fixed to protect the catalytic converter.")

    def update(self, dt):
        self.frame += 1
        
        # Live-refresh the active diagnostic screen while it's open.
        if self.state in ("diag", "report") and self._active_diag is not None:
            self._diag_refresh_frame += 1
            if self._diag_refresh_frame >= 120:
                self._diag_refresh_frame = 0
                kind, which = self._active_diag
                if kind == "fuel":
                    threading.Thread(target=self._build_fuel_screen, daemon=True).start()
                elif kind == "report":
                    self._build_report()
                elif kind == "warnings":
                    self._show_warnings()
                # readers NOT auto-refreshed - they read once (codes don't
                # change while viewing, and re-reading collides with the poller)
        
        # Run the sourced WarningAdvisor on the latest snapshot every cycle on
        # the face/menu. This updates the one-line warning shown on the face,
        # the face's issue-light color, and the timestamped warning log. It
        # does NOT hijack the screen - the full list lives in the WARNINGS tab,
        # which the driver opens when they choose to.
        if self.state in ("face", "menu"):
            snap = self._snapshotter.latest()
            if self._as_number(snap.get("rpm", "--")) > 0:   # only with real data
                self._active_warnings = self.advisor.check(snap)
                self._warn_log.record(self._active_warnings)   # timestamped log
                self._anomaly_result = self._anomaly.push(snap)   # ML advisory

        # Voice: when transcription finishes, route to AI and show the answer.
        if self.state == "voice" and self._voice is not None:
            text = self._voice.poll_result()
            if text is not None:
                answer = self._answer_question(text)
                self.diag_screen.set_message("YOU ASKED: " + text, answer)
                self.state = "voiceanswer"

        if self.state == "boot":
            self.boot.update(dt)
            if self.boot.done: self.state = "face"

        # Auto-speak a NEW trouble code explanation (guardian speaks up on its
        # own when the car reports a code). Only fires once per code, only when
        # not already talking, and only on the face screen.
        new_code = getattr(self.data, "last_code", None)
        new_text = getattr(self.data, "last_explanation", None)
        if (new_code is not None and new_code != self._spoken_code
                and new_text and not self.speech.active
                and self.state == "face"):
            self._spoken_code = new_code
            title = new_code
            if getattr(self.data, "last_tier", None):
                title = new_code + "  [" + self.data.last_tier + "]"
            self.speech.say(title, new_text)

        self.speech.update(dt)
        # Refresh the dyno with the latest captured pull while viewing it.
        if self.state == "dyno":
            run = self.data.get_dyno_run()
            if run:
                self.dyno.clear()
                self.dyno.add_run("RUN 1", run, (57, 255, 120))
                
        
                
                
    def draw(self, surface):
        snap = self._snapshotter.latest()
        mode = getattr(self.data, "mode", snap.get("mode", "highway"))
        # The WarningAdvisor is the single source of truth for the issue light:
        # if it has active warnings, its severity sets the face color (red/
        # yellow), and the WARNINGS screen lists exactly those same warnings -
        # face and list can never disagree. If no warnings, fall back to the
        # normal mode/state color from FaceState.
        advisor_mood = self.advisor.face_mood(self._active_warnings)
        if advisor_mood is not None:
            mood = advisor_mood
        else:
            mood = self.decider.decide(
                coolant_temp=snap["coolant_temp"], has_codes=snap["has_codes"],
                voltage=snap["voltage"], mode=mode, needs_gas=snap["needs_gas"],
                fuel_flag=self.data.fuel_mood_flag() if hasattr(self.data, "fuel_mood_flag") else None)
        self.face.set_mood(mood)

        if self.state == "boot":
            self.boot.draw(surface, self.frame)
            return

        if self.state == "menu":
            title = self.menu if self.menu != "MAIN" else "MENU"
            self.terminal.draw_menu(surface, title, MENUS[self.menu], self.frame)
            return

        if self.state == "livegraphs":
            surface.fill((10, 10, 12))
            # 4 zoned bars - instant values
            for key, bar in self.bars:
                bar.draw(surface, self._as_number(snap.get(key, "--")))
            # Coolant trend line - catches slow temperature creep
            if self.frame % 6 == 0:
                self.coolant_graph.add_point(self._as_number(snap.get("coolant_temp", "--")))
            self.coolant_graph.draw(surface)
            self._esc_hint(surface)
            return

        if self.state == "dyno":
            surface.fill((10, 10, 12))
            self.dyno.draw(surface)
            self._draw_estimated_banner(surface)
            self._draw_dyno_buttons(surface)
            self._esc_hint(surface)
            return
        
        if self.state == "waiting":
            self._draw_waiting(surface, self._waiting_title, self._waiting_msg)
            return
        
        if self.state == "confirm_clear":
            self._draw_confirm_clear(surface)
            return
        
        if self.state == "warnings":
            self.diag_screen.draw(surface)   # same renderer; taps handled above
            return
        
        if self.state == "diag":
            self.diag_screen.draw(surface)
            return
        
        if self.state == "engineer":
            self._draw_engineer(surface, snap)
            return
        
        if self.state == "tuning":
            self._draw_tuning(surface, snap)
            return
        
        if self.state == "report":
            self.diag_screen.draw(surface)   # reuse the diag screen renderer
            return
        
        if self.state == "voice":
            self._draw_voice_listening(surface)
            return
        if self.state == "voiceanswer":
            self.diag_screen.draw(surface)
            return

        # --- FACE state (home) ---
        surface.fill((10, 10, 12))
        accent = MOODS[self.face.mood]["rgb"]
        text_color = MOODS[self.face.mood]["text"]

        if self.speech.active:
            self.face.draw(surface, self.frame, cell=18, y_offset=20, talking=True)
            self.speech.draw(surface, 260, accent, text_color)
        else:
            mode = getattr(self.data, "mode", snap.get("mode", "highway"))
            mode_bar = MODE_STATS.get(mode, MODE_STATS["highway"])
            stats = []
            for label, key, unit, warn_thresh, warn_dir in mode_bar:
                value = snap.get(key, "--")
                # Check if this value is in a warning state
                is_warn = False
                if warn_thresh is not None and isinstance(value, (int, float)):
                    if warn_dir == "high" and value >= warn_thresh:
                        is_warn = True
                    elif warn_dir == "low" and value <= warn_thresh:
                        is_warn = True
                stats.append((label, value, unit, None, is_warn))
            self.statbar.draw(surface, stats, accent, self.frame)
            self.face.draw(surface, self.frame, cell=36, y_offset=100)
            lbl = pygame.font.SysFont("consolas", 26, bold=True).render(
                MOODS[self.face.mood]["label"], True, text_color)
            surface.blit(lbl, (self.width//2 - lbl.get_width()//2, 442))
            # ML advisory line - shows only when the rule-based advisor is quiet
            # (rules are the real alarm; this is the soft "unusual" signal).
            ml = self._anomaly_result
            if not self._active_warnings and ml.get("severity", "ok") != "ok":
                sev = ml["severity"]
                culprits = ml.get("culprits", [])
                top = culprits[0][0].upper() if culprits else "readings"
                if sev == "elevated":
                    ml_color = (255, 140, 40)
                    ml_text = "ML: sustained anomaly - " + top
                else:
                    ml_color = (200, 200, 90)
                    ml_text = "ML: readings unusual"
                ml_font = pygame.font.SysFont("consolas", 16, bold=True)
                ml_surf = ml_font.render(ml_text, True, ml_color)
                surface.blit(ml_surf, (self.width//2 - ml_surf.get_width()//2, 95))
            # ONE-LINE warning on the face: show the top (most important)
            # warning as a single line. Full list lives in the WARNINGS tab.
            if self._active_warnings:
                warn_font = pygame.font.SysFont("consolas", 18, bold=True)
                top = self._active_warnings[0]
                if len(top) > 52:
                    top = top[:49] + "..."
                wline = warn_font.render(top, True, (65, 255, 120))
                surface.blit(wline, (self.width//2 - wline.get_width()//2, 410))
                if len(self._active_warnings) > 1:
                    more = pygame.font.SysFont("consolas", 14).render(
                        "+" + str(len(self._active_warnings) - 1) + " more - see WARNINGS",
                        True, (150, 150, 90))
                    surface.blit(more, (self.width//2 - more.get_width()//2, 464))
                else:
                    hint = pygame.font.SysFont("consolas", 16).render(
                        "TAP to open menu", True, (90, 90, 100))
                    surface.blit(hint, (10, 464))
            else:
                hint = pygame.font.SysFont("consolas", 16).render(
                    "TAP to open menu", True, (90, 90, 100))
                surface.blit(hint, (10, 462))

    def _draw_waiting(self, surface, title, message):
        # Styled "waiting for data" placeholder screen.
        surface.fill((10, 10, 12))
        ft = pygame.font.SysFont("consolas", 32, bold=True)
        t = ft.render(title, True, (65, 255, 120))
        surface.blit(t, (self.width//2 - t.get_width()//2, 90))
        pygame.draw.line(surface, (30, 120, 55),
                         (100, 140), (self.width-100, 140), 2)
        fm = pygame.font.SysFont("consolas", 24, bold=True)
        if (self.frame // 30) % 2 == 0:
            m = fm.render(message, True, (255, 215, 40))
            surface.blit(m, (self.width//2 - m.get_width()//2, 200))
        fs = pygame.font.SysFont("consolas", 16)
        note = fs.render("Feature ready - awaiting live vehicle connection",
                         True, (120, 120, 130))
        surface.blit(note, (self.width//2 - note.get_width()//2, 260))
        self._esc_hint(surface)
    def _esc_hint(self, surface):
        h = pygame.font.SysFont("consolas", 16).render("TAP to go back", True, (100,100,110))
        surface.blit(h, (20, 462))
    def _draw_engineer(self, surface, snap):
        # Raw live-data dump for the technical user - every snapshot value,
        # updated live. Reads the cached snapshot (non-blocking), O(n) in the
        # number of fields.
        surface.fill((8, 14, 8))
        title = pygame.font.SysFont("consolas", 22, bold=True).render(
            "ENGINEER MODE - LIVE DATA", True, (65, 255, 120))
        surface.blit(title, (20, 16))
        pygame.draw.line(surface, (30, 120, 55), (20, 52), (self.width - 20, 52), 2)
        font = pygame.font.SysFont("consolas", 20)
        # (snapshot_key, display_label) - "boost" key now carries MAF airflow.
        fields = [("rpm", "RPM"), ("speed", "SPEED"),
                  ("coolant_temp", "COOLANT_TEMP"), ("boost", "MAF"),
                  ("engine_load", "ENGINE_LOAD"), ("voltage", "VOLTAGE"),
                  ("fuel", "FUEL"), ("oil_temp", "OIL_TEMP"),
                  ("afr", "AFR"), ("run_time", "RUN_TIME")]
        y = 66
        for key, label in fields:
            value = snap.get(key, "--")
            line = label.ljust(16) + ": " + str(value)
            surface.blit(font.render(line, True, (57, 255, 120)), (24, y))
            y += 30
        hint = pygame.font.SysFont("consolas", 15).render(
            "TAP to go back", True, (30, 120, 55))
        surface.blit(hint, (20, self.height - 26))
        
    def _draw_tuning(self, surface, snap):
        # Live knock/tuning monitor. Runs TuningAnalyzer against live timing,
        # rpm, throttle. Contextual - only flags knock under load. O(1) per
        # frame (fixed number of threshold checks).
        surface.fill((8, 14, 8))
        title = pygame.font.SysFont("consolas", 22, bold=True).render(
            "TUNING MONITOR - KNOCK/TIMING", True, (65, 255, 120))
        surface.blit(title, (20, 16))
        pygame.draw.line(surface, (30, 120, 55), (20, 52), (self.width - 20, 52), 2)

        font = pygame.font.SysFont("consolas", 20)
        timing = self._as_number(snap.get("timing", "--"))
        rpm = self._as_number(snap.get("rpm", "--"))
        throttle = self._as_number(snap.get("throttle", "--"))

        # Live readouts - value turns RED when it crosses a danger threshold.
        loaded = self._tuner.under_load(rpm, throttle)
        y = 66
        readouts = (
            ("TIMING ADV", timing, self._timing_is_bad(timing, loaded)),
            ("RPM", rpm, rpm is not None and rpm >= 6500),
            ("THROTTLE %", throttle, False),
        )
        for label, val, bad in readouts:
            color = (255, 60, 60) if bad else (57, 255, 120)
            text = label.ljust(14) + ": " + (str(val) if val is not None else "--")
            surface.blit(font.render(text, True, color), (24, y))
            y += 30
        state = "UNDER LOAD" if loaded else "cruise/idle"
        surface.blit(font.render("STATE".ljust(14) + ": " + state, True,
                                 (120, 200, 255)), (24, y))
        y += 40

        subaru = self._tuner.read_subaru_knock()
        alerts = self._tuner.evaluate(timing, self._last_timing, rpm, throttle, subaru)
        self._last_timing = timing    # track for next-frame drop detection

        if alerts:
            for alert in alerts:
                for chunk in self._wrap_line(alert, 46):
                    surface.blit(font.render(chunk, True, (255, 60, 60)), (24, y))
                    y += 28
        else:
            surface.blit(font.render("No knock detected - all clear.", True,
                                     (65, 255, 120)), (24, y))
            if not self._tuner.SUBARU_PIDS_VERIFIED:
                y += 30
                small = pygame.font.SysFont("consolas", 15)
                surface.blit(small.render(
                    "(DAM/FBKC/FLKC unavailable - Subaru PIDs unverified)",
                    True, (120, 120, 130)), (24, y))

        hint = pygame.font.SysFont("consolas", 15).render(
            "TAP to go back", True, (30, 120, 55))
        surface.blit(hint, (20, self.height - 26))
        
    def _draw_dyno_buttons(self, surface):
        # START (green) / STOP (red) buttons for manual pull control.
        armed = self.data.dyno_armed() if hasattr(self.data, "dyno_armed") else False
        font = pygame.font.SysFont("consolas", 22, bold=True)
        # START button - bottom left
        start_color = (40, 90, 40) if armed else (57, 255, 120)
        pygame.draw.rect(surface, start_color, (20, 420, 160, 50))
        s = font.render("START", True, (10, 10, 10))
        surface.blit(s, (60, 432))
        # STOP button - bottom right
        stop_color = (255, 60, 60) if armed else (90, 40, 40)
        pygame.draw.rect(surface, stop_color, (620, 420, 160, 50))
        t = font.render("STOP", True, (10, 10, 10))
        surface.blit(t, (665, 432))
        # Status text
        status = "ARMED - do your pull" if armed else "Tap START, then pull"
        st = pygame.font.SysFont("consolas", 16).render(status, True, (200, 200, 210))
        surface.blit(st, (self.width // 2 - st.get_width() // 2, 434))
        
    def _timing_is_bad(self, timing, loaded):
        # Timing yanked negative under load is a knock symptom. O(1).
        if timing is None or self._last_timing is None or not loaded:
            return False
        return (self._last_timing - timing) >= self._tuner.TIMING_DROP_WARN
    
    @staticmethod
    def _wrap_line(text, width):
        # Word-wrap one alert string. O(n) in words.
        words = str(text).split()
        lines, current = [], ""
        for word in words:
            if len(current) + len(word) + 1 <= width:
                current = (current + " " + word).strip()
            else:
                lines.append(current); current = word
        if current:
            lines.append(current)
        return lines

    def _draw_estimated_banner(self, surface):
        # Honest label: this is an OBD acceleration-based ESTIMATE, not a
        # calibrated dyno reading. Critical that users don't mistake it for
        # real crank/wheel HP. Drawn on top of the dyno graph.
        font = pygame.font.SysFont("consolas", 18, bold=True)
        text = font.render("* ESTIMATED (OBD acceleration method) *",
                           True, (255, 215, 40))
        # Semi-transparent strip behind it for legibility over the graph.
        strip = pygame.Surface((text.get_width() + 20, text.get_height() + 8))
        strip.set_alpha(180)
        strip.fill((20, 20, 20))
        surface.blit(strip, (self.width // 2 - text.get_width() // 2 - 10, 6))
        surface.blit(text, (self.width // 2 - text.get_width() // 2, 10))

def run_ui(data_provider, vehicle=None, ai_client=None, report_view=None,
           ai_router=None):
    """Entry point: run the full UI with a given data provider."""
    import json
    from autopi.touch import TouchReader
    pygame.init()
    pygame.mouse.set_visible(False)      # hide the cursor - touchscreen only
    screen = pygame.display.set_mode((800, 480))
    pygame.display.set_caption("AutoPi")
    clock = pygame.time.Clock()
    ui = UI(data_provider, vehicle=vehicle, ai_client=ai_client,
            report_view=report_view, ai_router=ai_router)

    with open("config.json") as f:
        touch_cfg = json.load(f).get("touch", {})
    touch = None
    try:
        touch = TouchReader(touch_cfg.get("device", "/dev/input/event3"), 800, 480,
                            calib=touch_cfg.get("calibration", {}))
    except Exception as err:
        print("Touch unavailable:", err)   # keyboard still works

    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_q and ui.state == "boot":
                    running = False
                ui.handle_key(e.key)
        if touch is not None:
            for kind, pos in touch.poll():
                if kind == "down":
                    # Start the quit-hold timer only (coords can be stale on
                    # 'down' with this panel; the quit corner is a large zone
                    # so that's fine). Do NOT select menu items on down.
                    if ui._in_quit_zone(pos):
                        ui._quit_started = time.monotonic()
                elif kind == "up":
                    ui._quit_started = None
                    ui.handle_touch_down(pos)   # act on 'up' - coords settled
        if ui.check_quit_hold():
            running = False
        ui.update(dt)
        ui.draw(screen)
        pygame.display.flip()

 
    if hasattr(ui.data, "_dyno"):
        ui.data._dyno.stop()
    ui._snapshotter.stop()
    
    if touch is not None:
        touch.close()
    pygame.quit()