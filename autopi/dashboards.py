import os
import time
import obd
from abc import ABC, abstractmethod
from autopi.tuning import TuningAnalyzer
from autopi.vehicle import Event


class Dashboard(ABC):
    """Abstract base for all live dashboards (Template Method pattern).
    run() defines the shared skeleton EVERY dashboard uses - the loop,
    reading, ML check, warnings, logging. Subclasses implement only
    title() and display() - the parts that actually differ.
    Cannot be instantiated directly (it's abstract)."""

    def __init__(self, vehicle, logger, warning_checker, ai_client,
                 detector, log_every=5):
        self._vehicle = vehicle              # aggregation - uses, doesn't own
        self._logger = logger
        self._warning_checker = warning_checker
        self._ai_client = ai_client
        self._detector = detector
        self._log_every = log_every

    # ---- Abstract: subclasses MUST implement these ----
    @abstractmethod
    def title(self):
        # The header string for this dashboard
        pass

    @abstractmethod
    def display(self, reading):
        # Print THIS dashboard's specific view of the reading
        pass

    # ---- Hook: subclasses MAY override. Default = no code check. ----
    def wants_code_check(self):
        # Highway and Camp override this to True; others use False
        return False

    # ---- Shared helper: explain + log trouble codes ----
    def _handle_codes(self):
        codes = self._vehicle.get_dtcs()
        if codes:
            print("CHECK ENGINE: codes present!")
            for code, description in codes:
                explanation = self._ai_client.explain_code(code)
                print("AI says:", explanation)
                self._logger.log_event(
                    Event(code, description, explanation, vin=self._vehicle.vin))
        else:
            print("Status:          All clear")

    # ---- THE TEMPLATE METHOD: shared skeleton (do not override) ----
    def run(self):
        print("")
        print("Starting", self.title(), "...")
        print("Press Ctrl+C to return to the menu.")
        time.sleep(2)

        batch = []
        counter = 0
        try:
            while True:
                os.system("clear")
                reading = self._vehicle.read_current()

                # --- the part that differs (filled by subclass) ---
                self.display(reading)

                # --- optional trouble-code check (hook) ---
                if self.wants_code_check():
                    self._handle_codes()

                # --- shared: ML anomaly check ---
                status = self._detector.add_reading(reading.to_feature_list())
                if status is not None:
                    print("ML status:      ", status)

                # --- shared: warnings ---
                for w in self._warning_checker.check(self._vehicle):
                    print(w)

                print("(Ctrl+C to exit to menu)")

                # --- shared: batched logging ---
                batch.append(reading)
                counter = counter + 1
                if counter >= self._log_every:
                    self._logger.safe_log_readings(batch)
                    batch = []
                    counter = 0

                time.sleep(1)
        except KeyboardInterrupt:
            if batch:
                self._logger.safe_log_readings(batch)
            print("")
            print("Returning to menu...")
            time.sleep(1)
            # ---- Small display helper (shared) ----
def clean(value):
    # Round a value for display; keep None as-is
    if value is None:
        return None
    try:
        return round(value, 1)
    except TypeError:
        return value
class HighwayDashboard(Dashboard):
    def title(self):
        return "EVERYDAY HIGHWAY"

    def wants_code_check(self):
        return True

    def display(self, reading):
        print("======= EVERYDAY HIGHWAY =======")
        print("Speed:          ", clean(reading.speed))
        print("RPM:            ", clean(reading.rpm))
        print("Boost (PSI):    ", reading.boost_psi)
        print("Coolant temp:   ", clean(reading.coolant_temp))
        print("Battery voltage:", clean(reading.voltage))


class TrackDashboard(Dashboard):
    def title(self):
        return "TRACK MODE"

    def display(self, reading):
        timing = self._vehicle.read_number(obd.commands.TIMING_ADVANCE)
        print("=========== TRACK MODE ===========")
        print("Boost (PSI):    ", reading.boost_psi)
        print("RPM:            ", clean(reading.rpm))
        print("Speed:          ", clean(reading.speed))
        print("Throttle:       ", clean(reading.throttle))
        print("Engine load:    ", clean(reading.engine_load))
        print("Timing advance: ", clean(timing))
        print("Coolant temp:   ", clean(reading.coolant_temp))
        print("Intake air temp:", clean(reading.intake_temp))


class AdventureDashboard(Dashboard):
    def title(self):
        return "ADVENTURE MODE"

    def display(self, reading):
        print("========== ADVENTURE MODE ==========")
        print("Coolant temp:   ", clean(reading.coolant_temp))
        print("Intake air temp:", clean(reading.intake_temp))
        print("Boost (PSI):    ", reading.boost_psi)
        print("Engine load:    ", clean(reading.engine_load))
        print("RPM:            ", clean(reading.rpm))
        print("Speed:          ", clean(reading.speed))
        print("Throttle:       ", clean(reading.throttle))
        print("Battery voltage:", clean(reading.voltage))


class CampDashboard(Dashboard):
    def title(self):
        return "CAMP MODE"

    def wants_code_check(self):
        return True

    def display(self, reading):
        runtime = self._vehicle.read_number(obd.commands.RUN_TIME)
        print("=========== CAMP MODE ===========")
        print("Battery voltage:", clean(reading.voltage))
        print("Coolant temp:   ", clean(reading.coolant_temp))
        print("Intake air temp:", clean(reading.intake_temp))
        print("RPM (idle):     ", clean(reading.rpm))
        print("Engine load:    ", clean(reading.engine_load))
        print("Idle run time:  ", clean(runtime))

class EngineerView:
    """Engineer Mode - shows ALL live PIDs the car supports, via an Async
    connection (researched best method for continuous multi-PID). Not a
    Dashboard subclass: it uses its own Async connection and discovery,
    so it's a separate View (inheritance only where it fits)."""

    SKIP = ["GET_DTC", "GET_CURRENT_DTC", "STATUS", "DTC_STATUS",
            "O2_SENSORS", "PIDS_A", "PIDS_B", "PIDS_C", "PIDS_9A",
            "MIDS_A", "ELM_VERSION", "ELM_VOLTAGE",
            "CLEAR_DTC", "HYBRID_BATTERY_REMAINING", "FUEL_STATUS",
            "OBD_COMPLIANCE", "FUEL_TYPE"]

    def __init__(self, port="/dev/pts/2"):
        self.__port = port

    def __clean(self, value):
        if value is None:
            return None
        try:
            return round(value.magnitude, 2)
        except AttributeError:
            return value

    def run(self):
        connection = obd.Async(self.__port)
        print("Discovering what this vehicle supports...")
        supported = sorted(connection.supported_commands, key=lambda c: c.name)
        live = []
        for cmd in supported:
            if cmd.name not in self.SKIP and not cmd.name.startswith("DTC_"):
                live.append(cmd)
        for cmd in live:
            connection.watch(cmd)
        connection.start()
        print("This vehicle exposes", len(live), "live data channels.")
        print("Press Ctrl+C to exit.")
        time.sleep(2)
        try:
            while True:
                os.system("clear")
                print("============ ENGINEER MODE (ALL LIVE DATA) ============")
                for cmd in live:
                    response = connection.query(cmd)
                    print(cmd.name, ":", self.__clean(response.value))
                print("=======================================================")
                print("(Ctrl+C to exit)")
                time.sleep(1)
        except KeyboardInterrupt:
            print("")
            print("Exiting Engineer Mode...")
        finally:
            connection.stop()


class TuningView:
    """Tuning Monitor - live boost/timing/fuel-trim/knock watching with
    contextual knock detection (via TuningAnalyzer). Its own View because
    it needs timing-history and the analyzer, not the standard loop."""

    def __init__(self, vehicle, ai_client):
        self._vehicle = vehicle
        self._ai_client = ai_client
        self._analyzer = TuningAnalyzer()

    def run(self):
        print("Starting Tuning Monitor... (Ctrl+C to exit)")
        time.sleep(2)
        last_timing = None
        try:
            while True:
                os.system("clear")
                timing = self._vehicle.read_number(obd.commands.TIMING_ADVANCE)
                rpm = self._vehicle.read_number(obd.commands.RPM)
                throttle = self._vehicle.read_number(obd.commands.THROTTLE_POS)
                boost = self._vehicle.get_boost()
                stft = self._vehicle.read_number(obd.commands.SHORT_FUEL_TRIM_1)
                ltft = self._vehicle.read_number(obd.commands.LONG_FUEL_TRIM_1)
                load = self._vehicle.read_number(obd.commands.ENGINE_LOAD)
                subaru = self._analyzer.read_subaru_knock()

                print("============= TUNING MONITOR =============")
                print("Boost (PSI):    ", boost)
                print("Timing advance: ", clean(timing))
                print("RPM:            ", clean(rpm))
                print("Throttle %:     ", clean(throttle))
                print("Engine load %:  ", clean(load))
                print("Short fuel trim:", clean(stft))
                print("Long fuel trim: ", clean(ltft))
                print("--- Subaru knock (needs verified PIDs) ---")
                print("DAM:            ", subaru["DAM"])
                print("FBKC:           ", subaru["FBKC"])
                print("FLKC:           ", subaru["FLKC"])

                alerts = self._analyzer.evaluate(timing, last_timing, rpm,
                                                 throttle, subaru)
                if alerts:
                    print("-----------------------------------------")
                    for a in alerts:
                        print("!! " + a)
                else:
                    if self._analyzer.under_load(rpm, throttle):
                        print("Status: under load, no knock detected")
                    else:
                        print("Status: cruising/idle (knock check paused)")

                print("=========================================")
                print("(Ctrl+C to exit)")
                last_timing = timing
                time.sleep(1)
        except KeyboardInterrupt:
            print("")
            print("Exiting Tuning Monitor...")