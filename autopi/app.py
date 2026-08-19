import os
import time
import threading

from autopi.config import Config
from autopi.vehicle import Vehicle
from autopi.db_logger import Logger
from autopi.ai_client import AIClient
from autopi.ai_router import AIRouter
from autopi.anomaly import AnomalyDetector
from autopi.warnings_checker import WarningChecker
from autopi.factory import DashboardFactory
from autopi.dashboards import EngineerView, TuningView, clean
from autopi.report import ReportView
from autopi.states import StateMachine
from autopi.diagnostics import (FreezeFrameReader, PendingCodesReader,
                                PermanentCodesReader, ReadinessReader,
                                MonitorTestReader)
from autopi.diagnostic_view import DiagnosticView
from autopi.vehicle import Event


class App:
    """The Controller (MVC). Owns all the Model objects and Views, runs the
    menu, and manages the background watcher thread. Composition: the App
    owns its collaborators (they live and die with it)."""

    def __init__(self, port=None):
        # --- Compose all the Models (the App owns these) ---
        self._config = Config()
        # Port comes from config.json (DRY, no hardcoded values).
        # An explicit port arg still wins (useful for testing on the emulator).
        if port is None:
            port = self._config.obd_port
        self._vehicle = Vehicle(port=port)
        self._logger = Logger()
        self._ai = AIClient(self._config)
        # Tier the AI: router tries Ollama, falls back to instant offline.
        # This is what stops a slow/absent laptop from freezing the loop.
        self._router = AIRouter(self._ai)
        self._detector = AnomalyDetector(window=6)
        self._warnings = WarningChecker(self._config)
        self._states = StateMachine()

        # --- The factory for standard dashboards ---
        self._factory = DashboardFactory(
            self._vehicle, self._logger, self._warnings, self._ai,
            self._detector, self._config.log_every)

        # --- Threading control (proper flag, not a bare global) ---
        self._menu_open = False
        self._lock = threading.Lock()   # guards the shared flag (race safety)
        self._port = port
        self._seen_codes = set()        # codes already explained (no repeat AI hits)
        
    # ---------- BACKGROUND WATCHER (daemon thread) ----------
    def _background_watcher(self):
        batch = []
        counter = 0
        while True:
            with self._lock:
                paused = self._menu_open
            if paused:
                time.sleep(1)
                continue

            reading = self._vehicle.read_current()
            self._states.update_from_speed(reading.speed)

            os.system("clear")
            print("=========== AUTOPI - BACKGROUND ===========")
            print("Mode:           ", self._states.label())
            print("Speed:          ", clean(reading.speed))
            print("RPM:            ", clean(reading.rpm))
            print("Boost (PSI):    ", reading.boost_psi)
            print("Coolant temp:   ", clean(reading.coolant_temp))
            print("Battery voltage:", clean(reading.voltage))

            status = self._detector.add_reading(reading.to_feature_list())
            if status is not None:
                print("ML status:      ", status)

            codes = self._vehicle.get_dtcs()
            if codes:
                print("CHECK ENGINE: codes present!")
                for code, description in codes:
                    # Explain each code ONCE, via the tiered router (instant
                    # offline fallback if Ollama is slow or unreachable).
                    if code not in self._seen_codes:
                        self._seen_codes.add(code)
                        result = self._router.explain_code(code)
                        print("AI (" + result["tier"] + "):", result["text"])
                        self._logger.log_event(
                            Event(code, description, result["text"],
                                  vin=self._vehicle.vin))
                    else:
                        print("Known code:", code, "-", description)
            else:
                print("Status:          All clear")

            for w in self._warnings.check(self._vehicle):
                print(w)

            print("===========================================")
            print("(Press Enter to open the menu)")

            batch.append(reading)
            counter = counter + 1
            if counter >= self._config.log_every:
                self._logger.safe_log_readings(batch)
                batch = []
                counter = 0

            time.sleep(1)

    # ---------- MENU ----------
    def _show_menu(self):
        with self._lock:
            self._menu_open = True
        self._states.to_menu()

        running_menu = True
        while running_menu:
            os.system("clear")
            print("==================================")
            print("  AUTOPI - MAIN MENU")
            print("==================================")
            print("  --- Live Dashboards ---")
            print("  1) Everyday Highway")
            print("  2) Track Mode")
            print("  3) Adventure Mode")
            print("  4) Camp Mode")
            print("  --- Tools ---")
            print("  5) Engineer Mode (all live data)")
            print("  6) Tuning Monitor")
            print("  7) Generate Report")
            print("  --- Diagnostics (deep dive) ---")
            print("  8) Freeze Frame")
            print("  9) Pending Codes")
            print("  10) Permanent Codes")
            print("  11) Readiness Monitors")
            print("  12) Monitor Tests (Mode 06)")
            print("  --- ")
            print("  0) Resume background watching")
            print("==================================")
            choice = input("Pick a number: ")

            if choice in self._factory.available_choices():
                self._factory.create(choice).run()
            elif choice == "5":
                EngineerView(self._port).run()
                self._vehicle.connect()   # reconnect after Engineer's own connection
            elif choice == "6":
                TuningView(self._vehicle, self._ai).run()
            elif choice == "7":
                ReportView(self._logger, self._detector, self._ai).generate_all()
                input("Report saved (txt + PDF). Press Enter...")
            elif choice == "8":
                DiagnosticView(FreezeFrameReader(self._vehicle, self._ai)).show()
            elif choice == "9":
                DiagnosticView(PendingCodesReader(self._vehicle, self._ai)).show()
            elif choice == "10":
                DiagnosticView(PermanentCodesReader(self._vehicle, self._ai)).show()
            elif choice == "11":
                DiagnosticView(ReadinessReader(self._vehicle, self._ai)).show()
            elif choice == "12":
                DiagnosticView(MonitorTestReader(self._vehicle, self._ai)).show()
            elif choice == "0":
                running_menu = False
            else:
                print("Not a valid choice.")
                time.sleep(1)

        # Resume background
        with self._lock:
            self._menu_open = False
        self._states.resume_from_menu(self._vehicle.read_number(
            __import__("obd").commands.SPEED))

    # ---------- MAIN ENTRY ----------
    def run(self):
        print("Connecting to vehicle...")
        print("Status:", self._vehicle.connect())
        self._logger.setup()
        print("Starting AutoPi...")
        time.sleep(1)

        watcher = threading.Thread(target=self._background_watcher, daemon=True)
        watcher.start()

        while True:
            input()            # Enter opens the menu (touch replaces this later)
            self._show_menu()