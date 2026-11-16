"""Full diagnostic report - the one you hand to a mechanic.

collect()      reads everything once (OBD priority, so it's quick):
               vehicle ID (VIN, ECU), check-engine status, distance / time
               since codes were cleared and with the light on, the full
               HealthCheck (codes, pending, permanent, readiness, Mode 06,
               live sensors vs ranges, warm-up, ML), freeze frame, this
               drive's warning log, the last dyno pull and a live snapshot.
screen_items() same layout as every other screen: GREEN raw, PURPLE meaning.
to_html()      a self-contained, printable page (opens on any phone; Share >
               Print > Save as PDF to email it to the shop).
to_text()      plain text copy of the same content.
save()         writes both into reports/ and returns the paths.

Honest by design: every value is labelled with where it came from, and
anything estimated (dyno, ML) says so.
"""
import html
import os
import time

from autopi.palette import HTML_PURPLE, HTML_PURPLE_PRINT

try:
    import obd
except Exception:            # tests / simulator
    obd = None


VEHICLE_PIDS = [
    # (label, python-OBD command name, unit)
    ("VIN", "VIN", ""),
    ("ECU name", "ECU_NAME", ""),
    ("OBD standard", "OBD_COMPLIANCE", ""),
    ("Fuel system status", "FUEL_STATUS", ""),
    ("Distance since codes cleared", "DISTANCE_SINCE_DTC_CLEAR", ""),
    ("Warm-ups since codes cleared", "WARMUPS_SINCE_DTC_CLEAR", ""),
    ("Time since codes cleared", "TIME_SINCE_DTC_CLEARED", ""),
    ("Distance with check-engine light on", "DISTANCE_W_MIL", ""),
    ("Run time with check-engine light on", "RUN_TIME_MIL", ""),
]

# Fuel system status (Mode 01 PID 03) in plain words. python-OBD gives a
# tuple (fuel system 1, fuel system 2); system 2 is '' on one-bank engines.
# First match wins, so the fault wordings are checked before plain "closed loop".
FUEL_STATUS_WORDS = [
    ("system failure", "Open loop - SYSTEM FAULT",
     "Open loop = the engine computer is NOT fine-tuning the fuel mix, because "
     "something in the feedback system failed. Check for codes - this needs attention."),
    ("fault in the feedback", "Closed loop, but with a sensor fault",
     "The computer is still fine-tuning the fuel mix, but one oxygen sensor or part "
     "of the feedback system has a problem. Check for codes."),
    ("insufficient engine temperature", "Open loop - engine still cold (normal)",
     "Open loop = the oxygen sensors aren't warm enough to trust yet, so the computer "
     "uses a preset fuel recipe. Normal for the first minute or two after a cold start."),
    ("engine load", "Open loop - hard acceleration or coasting (normal)",
     "The computer stops fine-tuning for a moment: extra fuel at full throttle, or "
     "fuel shut off while coasting. Normal while it lasts."),
    ("closed loop", "Closed loop (normal)",
     "Closed loop = the engine computer reads the oxygen sensor in the exhaust and "
     "fine-tunes the fuel mix many times a second, like cruise control for the "
     "air/fuel mix. This is how a warm, healthy engine should run."),
]


def describe_fuel_status(value):
    """python-OBD FUEL_STATUS value -> (short text, plain-English note).
    note is '' when the wording isn't recognised (the raw text is shown)."""
    if value is None:
        return None, ""
    parts = value if isinstance(value, (tuple, list)) else [value]
    parts = [str(p).strip() for p in parts if p not in (None, "") and str(p).strip()]
    if not parts:
        return None, ""
    shorts, notes = [], []
    for part in parts:
        low = part.lower()
        for key, short, note in FUEL_STATUS_WORDS:
            if key in low:
                shorts.append(short)
                notes.append(note)
                break
        else:
            shorts.append(part)
    if len(shorts) == 2 and shorts[0] != shorts[1]:
        short = "system 1: " + shorts[0] + "; system 2: " + shorts[1]
    else:
        short = shorts[0]
    note = notes[0] if notes else ""
    return short, note


LIVE_FIELDS = [("rpm", "RPM", ""), ("speed", "Speed", " mph"),
               ("coolant_temp", "Coolant", " C"), ("intake_temp", "Intake air", " C"),
               ("engine_load", "Engine load", " %"), ("throttle", "Throttle", " %"),
               ("boost", "MAF airflow", " g/s"), ("timing", "Ignition timing", " deg"),
               ("stft_b1", "Short fuel trim B1", " %"), ("ltft_b1", "Long fuel trim B1", " %"),
               ("o2_b1s2", "Rear O2 B1S2", " V"), ("afr", "Commanded air/fuel", ""),
               ("fuel", "Fuel range", " mi"), ("run_time", "Engine run time", " min")]


class DiagnosticReport:
    def __init__(self, vehicle, health_check, snapshot_fn, priority_fn=None,
                 readers=None, warn_log=None, dyno_info_fn=None,
                 car_name="2023 Subaru Outback Wilderness (FA24 turbo, CVT)"):
        self._vehicle = vehicle
        self._health = health_check
        self._snap = snapshot_fn
        self._priority = priority_fn
        self._readers = readers or {}
        self._warn_log = warn_log
        self._dyno = dyno_info_fn
        self.car_name = car_name

    # ---- reading --------------------------------------------------------
    def _read_raw(self, name):
        if self._vehicle is None or obd is None:
            return None
        cmd = getattr(obd.commands, name, None)
        if cmd is None:
            return None
        try:
            return self._vehicle.read_value(cmd)
        except Exception:
            return None

    def _read(self, name):
        v = self._read_raw(name)
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            v = v.decode(errors="ignore")
        return str(v).strip()

    def _mil(self):
        if self._vehicle is None or obd is None:
            return None, None
        try:
            st = self._vehicle.read_value(obd.commands.STATUS)
            return bool(st.MIL), int(st.DTC_count)
        except Exception:
            return None, None

    def collect(self):
        def body():
            data = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "car": self.car_name, "vehicle": [], "mil": None,
                    "dtc_count": None}
            data["vehicle_notes"] = {}
            for label, name, unit in VEHICLE_PIDS:
                if name == "FUEL_STATUS":
                    short, note = describe_fuel_status(self._read_raw(name))
                    data["vehicle"].append((label, short))
                    if note:
                        data["vehicle_notes"][label] = note
                    continue
                data["vehicle"].append((label, self._read(name)))
            data["mil"], data["dtc_count"] = self._mil()
            data["sections"] = self._health.run()
            ff = self._readers.get("FREEZE FRAME")
            try:
                data["freeze"] = ff.read() if ff is not None else None
            except Exception:
                data["freeze"] = None
            data["live"] = dict(self._snap() or {})
            data["warnings"] = (self._warn_log.session_entries()
                                if self._warn_log is not None else [])
            data["dyno"] = self._dyno() if self._dyno else None
            return data
        if self._priority is not None:
            with self._priority():
                return body()
        return body()

    # ---- shared helpers ------------------------------------------------
    @staticmethod
    def _fmt(v, unit=""):
        if isinstance(v, float):
            v = round(v, 2)
        return "--" if v in (None, "--", "") else str(v) + unit

    def verdict(self, data):
        secs = data["sections"]
        fixes = [s for s in secs if s.level == "fix"]
        watches = [s for s in secs if s.level == "watch"]
        if fixes:
            return "fix", str(len(fixes)) + " to fix, " + str(len(watches)) + " to watch"
        if watches:
            return "watch", str(len(watches)) + " to watch, nothing urgent"
        return "ok", "PASS - nothing needs attention"

    def facts(self, data):
        return self._health.facts(data["sections"])

    # ---- on-screen -----------------------------------------------------
    def screen_items(self, data, summary=None):
        level, text = self.verdict(data)
        tag = {"fix": "[FIX] ", "watch": "[WATCH] ", "ok": "[OK] "}[level]
        out = [("--- " + data["car"] + " ---", "head"),
               ("RAW  report " + data["time"], "raw")]
        notes = data.get("vehicle_notes", {})
        for label, val in data["vehicle"]:
            if val is not None:
                out.append(("RAW  " + label + ": " + val, "raw"))
                if notes.get(label):
                    out.append(("  " + notes[label], "ai"))
        if data["mil"] is not None:
            out.append(("RAW  check-engine light: " + ("ON" if data["mil"] else "off")
                        + ", codes reported: " + str(data["dtc_count"]), "raw"))
        out.append((tag + "OVERALL: " + text, "ai"))
        out.append(("--- AI SUMMARY ---", "head"))
        out.append((summary or "AI translating...", "ai"))
        out.append(("", "raw"))
        out.extend(self._health.render_sections(data["sections"]))
        out.append(("--- FREEZE FRAME (MODE 02) ---", "head"))
        ff = data.get("freeze")
        if ff:
            for k in ff:
                out.append(("RAW  " + str(k) + ": " + self._fmt(ff[k]), "raw"))
            out.append(("Conditions the moment the first code was stored.", "ai"))
        else:
            out.append(("RAW  none stored", "raw"))
        out.append(("--- LIVE SNAPSHOT ---", "head"))
        live = data["live"]
        for key, label, unit in LIVE_FIELDS:
            out.append(("RAW  " + label.ljust(20) + self._fmt(live.get(key), unit), "raw"))
        out.append(("--- WARNINGS THIS DRIVE ---", "head"))
        if data["warnings"]:
            for stamp, w in data["warnings"][:15]:
                out.append(("RAW  " + stamp.split(" ")[-1] + "  " + w, "raw"))
        else:
            out.append(("RAW  none", "raw"))
        out.append(("--- LAST DYNO PULL (ESTIMATE) ---", "head"))
        dy = data.get("dyno")
        if dy:
            out.append(("RAW  " + dy["time"] + "  peak " + str(dy["peak_hp"]) + " hp @ "
                        + str(dy["peak_rpm"]) + " rpm", "raw"))
        else:
            out.append(("RAW  no pull recorded", "raw"))
        return out

    # ---- export --------------------------------------------------------
    def to_text(self, data, summary=None):
        lines = ["AUTOPI FULL DIAGNOSTIC REPORT", "=" * 40]
        for text, kind in self.screen_items(data, summary or "(AI summary not available)"):
            if kind == "head":
                lines.append("")
                lines.append(text.strip("- "))
            elif text:
                lines.append(text)
        lines.append("")
        lines.append("Read by AutoPi over OBD-II (read-only). Dyno and ML values are estimates.")
        return "\n".join(lines) + "\n"

    def to_html(self, data, summary=None):
        e = html.escape
        level, verdict_text = self.verdict(data)
        color = {"fix": "#ff5a5a", "watch": HTML_PURPLE, "ok": "#41ff78"}[level]
        css = ("body{background:#070d08;color:#41ff78;font-family:Consolas,Menlo,monospace;"
               "margin:0;padding:16px;max-width:900px}"
               "h1{font-size:22px;margin:0 0 4px}h2{color:#2f8a48;font-size:15px;"
               "border-bottom:1px solid #1e4d2a;padding-bottom:3px;margin:22px 0 8px;"
               "text-transform:uppercase;letter-spacing:1px}"
               ".sub{color:#2f8a48;font-size:12px}.raw{color:#41ff78}.ai{color:" + HTML_PURPLE + "}"
               ".card{border:2px solid " + color + ";padding:10px 12px;margin:14px 0;"
               "font-size:18px;color:" + color + "}"
               "table{border-collapse:collapse;width:100%;font-size:13px}"
               "td{padding:3px 6px;border-bottom:1px solid #12301a;vertical-align:top}"
               "td.k{color:#2f8a48;width:42%}.tag{font-weight:bold}"
               ".fix{color:#ff5a5a}.watch{color:" + HTML_PURPLE + "}.ok{color:#41ff78}"
               "ol{margin:4px 0 4px 18px;color:" + HTML_PURPLE + "}p{margin:4px 0}"
               ".legend{font-size:12px;color:#2f8a48;margin-top:6px}"
               "@media print{body{background:#fff;color:#000}.raw{color:#000}"
               ".ai,ol{color:" + HTML_PURPLE_PRINT + "}h2,.sub,td.k,.legend{color:#333}"
               ".card{color:#000}}")
        h = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>AutoPi report " + e(data["time"]) + "</title><style>" + css
             + "</style></head><body>",
             "<h1>AUTOPI // FULL DIAGNOSTIC REPORT</h1>",
             "<div class='sub'>" + e(data["car"]) + " &middot; " + e(data["time"]) + "</div>",
             "<div class='legend'>Green = raw data read from the car. Purple = AutoPi's "
             "explanation / what to do.</div>",
             "<div class='card'>OVERALL: " + e(verdict_text) + "</div>"]
        if summary:
            h.append("<h2>AI summary</h2><p class='ai'>" + e(summary) + "</p>")

        h.append("<h2>Vehicle</h2><table>")
        notes = data.get("vehicle_notes", {})
        for label, val in data["vehicle"]:
            h.append("<tr><td class='k'>" + e(label) + "</td><td class='raw'>"
                     + e(val if val is not None else "not reported") + "</td></tr>")
            if val is not None and notes.get(label):
                h.append("<tr><td></td><td class='ai'>" + e(notes[label]) + "</td></tr>")
        if data["mil"] is not None:
            h.append("<tr><td class='k'>Check-engine light</td><td class='raw'>"
                     + ("ON" if data["mil"] else "off") + " (" + e(str(data["dtc_count"]))
                     + " codes reported)</td></tr>")
        h.append("</table>")

        for n, s in enumerate(data["sections"]):
            h.append("<h2>" + str(n + 1) + ". " + e(s.title) + "</h2><table>")
            for line in s.raw:
                h.append("<tr><td class='raw' colspan='2'>" + e(line) + "</td></tr>")
            h.append("</table><p class='" + s.level + "'><span class='tag'>["
                     + s.level.upper() + "]</span> " + e(s.meaning) + "</p>")
            if s.steps:
                h.append("<ol>" + "".join("<li>" + e(x) + "</li>" for x in s.steps if x) + "</ol>")

        h.append("<h2>Freeze frame (Mode 02)</h2><table>")
        ff = data.get("freeze")
        if ff:
            for k in ff:
                h.append("<tr><td class='k'>" + e(str(k)) + "</td><td class='raw'>"
                         + e(self._fmt(ff[k])) + "</td></tr>")
        else:
            h.append("<tr><td class='raw'>none stored</td></tr>")
        h.append("</table>")

        h.append("<h2>Live snapshot at report time</h2><table>")
        for key, label, unit in LIVE_FIELDS:
            h.append("<tr><td class='k'>" + e(label) + "</td><td class='raw'>"
                     + e(self._fmt(data["live"].get(key), unit)) + "</td></tr>")
        h.append("</table>")

        h.append("<h2>Warnings this drive</h2><table>")
        if data["warnings"]:
            for stamp, w in data["warnings"][:30]:
                h.append("<tr><td class='k'>" + e(stamp) + "</td><td class='raw'>"
                         + e(w) + "</td></tr>")
        else:
            h.append("<tr><td class='raw'>none</td></tr>")
        h.append("</table>")

        h.append("<h2>Last dyno pull (estimate)</h2>")
        dy = data.get("dyno")
        if dy:
            h.append("<p class='raw'>" + e(dy["time"]) + ": peak " + str(dy["peak_hp"])
                     + " hp @ " + str(dy["peak_rpm"]) + " rpm (OBD acceleration method, "
                     "estimate only)</p>")
        else:
            h.append("<p class='raw'>no pull recorded</p>")

        h.append("<h2>About this report</h2><p class='sub'>Read by AutoPi over the OBD-II "
                 "port (read-only - AutoPi does not change any engine settings). Ranges: "
                 "coolant warm 85-105 C, fuel trims normal within +/-10 %, problem beyond "
                 "15 %. Dyno and ML anomaly values are estimates.</p>")
        h.append("</body></html>")
        return "\n".join(h)

    def to_qr_text(self, data, summary=None, limit=900):
        """Short plain-text report that fits INSIDE a QR code (works with
        no network at all - the phone camera just shows the text)."""
        level, verdict_text = self.verdict(data)
        lines = ["AUTOPI REPORT " + data["time"], data["car"]]
        for label, val in data["vehicle"]:
            if label == "VIN" and val:
                lines.append("VIN " + val)
        lines.append("OVERALL: " + verdict_text)
        for s in data["sections"]:
            if s.level in ("fix", "watch"):
                lines.append("[" + s.level.upper() + "] " + s.title + ": " + s.meaning)
                for raw in s.raw[:4]:
                    lines.append("  " + raw)
        if summary:
            lines.append("AI: " + summary)
        text = "\n".join(lines)
        if len(text) > limit:
            text = text[:limit - 30] + "\n...(full report on AutoPi)"
        return text

    def save(self, data, summary=None, folder="reports"):
        os.makedirs(folder, exist_ok=True)
        stem = "autopi_report_" + time.strftime("%Y%m%d_%H%M%S")
        html_path = os.path.join(folder, stem + ".html")
        txt_path = os.path.join(folder, stem + ".txt")
        with open(html_path, "w") as f:
            f.write(self.to_html(data, summary))
        with open(txt_path, "w") as f:
            f.write(self.to_text(data, summary))
        return html_path, txt_path
