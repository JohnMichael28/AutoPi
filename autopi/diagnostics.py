import obd
from abc import ABC, abstractmethod


class DiagnosticReader(ABC):
    """Abstract base for on-demand diagnostic readers (Model).
    Each subclass reads one OBD diagnostic service (freeze frame, pending
    codes, readiness, etc). The base provides shared AI translation so
    every reader can explain its jargon in plain English.

    On-demand only: these fire when the user opens the mode, never in the
    always-on loop - so O(1) time/space, zero cost to the background Face."""

    def __init__(self, vehicle, ai_client):
        self._vehicle = vehicle          # aggregation
        self._ai_client = ai_client      # for plain-English translation

    @abstractmethod
    def name(self):
        # Human-readable name of this diagnostic mode
        pass

    @abstractmethod
    def read(self):
        # Query the car for this mode's data. Return raw result (or None).
        pass

    def explain(self, raw):
        # Translate raw jargon to plain English via local AI. Offline-safe:
        # if the AI is unreachable (no WiFi/Ollama in the car), return a clear
        # note instead of hanging - the raw data above is still fully shown.
        if raw is None or raw == [] or raw == {}:
            return "Nothing to report for " + self.name() + "."
        if self._ai_client is None:
            return "(AI explanation unavailable - showing raw data only.)"
        prompt = ("A car's " + self.name() + " diagnostic returned this data: "
                  + str(raw) + ". Explain in plain English what this means for "
                  "the owner, briefly and factually. Do NOT tell them to visit "
                  "a mechanic unless the data clearly shows a real fault. If the "
                  "data looks normal or empty, say things look fine.")
        try:
            return self._ai_client.ask(prompt)
        except Exception:
            return "(AI explanation unavailable offline - raw data shown above.)"

    def report(self):
        # Template method: read the raw data AND get the plain-English version.
        # Returns a dict with both, so the View can show jargon or plain English.
        raw = self.read()
        return {
            "mode": self.name(),
            "raw": raw,
            "plain_english": self.explain(raw),
        }
class FreezeFrameReader(DiagnosticReader):
    """Mode 02 - the sensor snapshot captured when a code set.
    Shows the conditions at the moment of the fault."""

    def name(self):
        return "Freeze Frame (conditions when fault occurred)"

    def read(self):
        # FREEZE_DTC = the code that triggered the frame; plus key snapshot values
        frame = {}
        dtc = self._vehicle.read_value(obd.commands.FREEZE_DTC)
        frame["trigger_code"] = dtc
        # Sensor values at fault time use the DTC_ prefixed commands
        frame["rpm"] = self._vehicle.read_number(obd.commands.DTC_RPM)
        frame["speed"] = self._vehicle.read_number(obd.commands.DTC_SPEED)
        frame["coolant"] = self._vehicle.read_number(obd.commands.DTC_COOLANT_TEMP)
        frame["load"] = self._vehicle.read_number(obd.commands.DTC_ENGINE_LOAD)
        # If nothing was captured (no fault), trigger_code will be None
        if frame["trigger_code"] is None:
            return None
        return frame


class PendingCodesReader(DiagnosticReader):
    """Mode 07 - 'two-trip' pending codes. Early warning: a fault set once
    but hasn't lit the check-engine light yet."""

    def name(self):
        return "Pending Codes (early warnings)"

    def read(self):
        codes = self._vehicle.read_value(obd.commands.GET_CURRENT_DTC)
        if not codes:
            return None
        return codes


class PermanentCodesReader(DiagnosticReader):
    """Mode 0A - permanent codes stored in non-volatile memory. Can't be
    cleared by disconnecting the battery; clear only after real repair."""

    def name(self):
        return "Permanent Codes (cannot be cleared until fixed)"

    def read(self):
        # Mode 0A support varies; guard it so a missing command never crashes.
        try:
            cmd = obd.commands.GET_DTC_PERMANENT
        except AttributeError:
            return "Permanent code reading not available on this setup."
        codes = self._vehicle.read_value(cmd)
        if not codes:
            return None
        return codes


class ReadinessReader(DiagnosticReader):
    """Mode 01 PID 01 - emissions readiness monitors. Tells you if the car
    has completed its self-tests (i.e. ready for an inspection)."""

    def name(self):
        return "Readiness Monitors (inspection readiness)"

    def read(self):
        status = self._vehicle.read_value(obd.commands.STATUS)
        if status is None:
            return None
        # Build a simple dict of each monitor: available + complete
        result = {}
        # python-OBD exposes named monitors on the status object
        for monitor_name in ["MISFIRE_MONITORING", "FUEL_SYSTEM_MONITORING",
                             "COMPONENT_MONITORING", "CATALYST_MONITORING",
                             "OXYGEN_SENSOR_MONITORING", "EGR_VVT_MONITORING"]:
            try:
                m = getattr(status, monitor_name)
                result[monitor_name] = {"available": m.available,
                                        "complete": m.complete}
            except AttributeError:
                pass   # this monitor not reported by this vehicle
        return result if result else None


class MonitorTestReader(DiagnosticReader):
    """Mode 06 - on-board monitor test results with pass/fail limits.
    NOTE: python-OBD flags Mode 06 as EXPERIMENTAL / not real-vehicle tested."""

    def name(self):
        return "Monitor Test Results (Mode 06 - experimental)"

    def read(self):
        # Mode 06 is experimental; try a common monitor, guard everything.
        try:
            cmd = obd.commands.MONITOR_MISFIRE_GENERAL
        except AttributeError:
            return "Mode 06 not available on this setup."
        try:
            result = self._vehicle.read_value(cmd)
            if result is None:
                return None
            return str(result)
        except Exception as e:
            return "Mode 06 read failed (experimental): " + str(e)