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

    def explain(self, raw, on_status=None, still_wanted=None):
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
            # Retries by itself while the screen is open (on_status shows
            # "retrying in N s"), so you never have to back out and reopen.
            if hasattr(self._ai_client, "ask_with_retry"):
                return self._ai_client.ask_with_retry(
                    prompt, on_status=on_status, still_wanted=still_wanted)
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


# Mode 03/07/0A replies on CAN: [service+0x40, count, then 2 bytes per code].
OBD_LETTERS = "PCBU"
OBD_REPLY_NAMES = {0x7E8: "ENGINE", 0x7E9: "TRANSMISSION", 0x7EA: "HYBRID/OTHER"}
OBD_NRC = {0x11: "service not supported", 0x12: "not supported",
           0x22: "conditions not correct", 0x31: "request out of range"}


def decode_obd_dtc(b0, b1):
    """2 OBD bytes -> 'P2096'."""
    return (OBD_LETTERS[(b0 >> 6) & 0x03] + str((b0 >> 4) & 0x03)
            + "%X" % (b0 & 0x0F) + "%02X" % b1)


def decode_obd_dtc_reply(payload, service=0x0A):
    """One module's reply bytes -> list of codes, or None if the reply isn't
    a positive answer to `service`. Handles the count byte that CAN cars
    send, and older replies without it."""
    if not payload or payload[0] != service + 0x40:
        return None
    body = payload[1:]
    if len(body) % 2 == 1 and body and body[0] * 2 == len(body) - 1:
        body = body[1:]                  # CAN: first byte is the code count
    codes = []
    for i in range(0, len(body) - 1, 2):
        if body[i] == 0 and body[i + 1] == 0:
            continue                     # padding, not a code
        codes.append(decode_obd_dtc(body[i], body[i + 1]))
    return codes


def obd_code_description(code):
    try:
        from obd.codes import DTC        # python-OBD's generic code book
        return DTC.get(code, "")
    except Exception:
        return ""


class PermanentCodesReader(DiagnosticReader):
    """Mode 0A - permanent codes stored in non-volatile memory. Can't be
    cleared with Mode 04 or by disconnecting the battery; the ECU removes
    one only after the repair is proven by a passing drive cycle.

    python-OBD 0.7.2 has no Mode 0A command, and it refuses commands that
    aren't on the car's Mode 01 "supported" list - so the old reader gave up
    without ever asking the car. This one asks the car directly (raw request
    "0A" to the OBD broadcast address 7DF, same raw path as the all-module
    scan) and decodes the reply itself.

    read() returns: None = car answered, no permanent codes;
    [(code, description)] = permanent codes; str = could not read, and why."""

    def name(self):
        return "Permanent Codes (cannot be cleared until fixed)"

    def read(self):
        request = getattr(self._vehicle, "uds_request", None)
        if request is None:
            return "Permanent codes need a direct adapter connection (simulator has none)."
        try:
            replies = request("7DF", "0A")
        except Exception as err:
            return "Permanent codes not read: " + str(err)
        if not replies:
            return "Car did not answer the permanent-code request (try with the engine running)."
        found = []
        answered = False
        refusals = []
        for can_id, payload in replies:
            if len(payload) >= 3 and payload[0] == 0x7F and payload[1] == 0x0A:
                if payload[2] == 0x78:
                    continue                     # "busy, wait" - real answer follows
                refusals.append(OBD_NRC.get(payload[2], "refused 0x%02X" % payload[2]))
                continue
            codes = decode_obd_dtc_reply(payload, 0x0A)
            if codes is None:
                continue
            answered = True
            where = OBD_REPLY_NAMES.get(can_id, "%03X" % can_id)
            for code in codes:
                desc = obd_code_description(code)
                found.append((code, (desc + " " if desc else "") + "(" + where + ")"))
        if found:
            return found
        if answered:
            return None
        if refusals:
            return "Car refused the permanent-code request: " + refusals[0] + "."
        return "Car did not answer the permanent-code request (try with the engine running)."


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