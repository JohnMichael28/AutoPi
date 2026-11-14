"""HealthCheck: one tap, AutoPi inspects everything it can read by itself.

Fixed section order, same layout every time (consistent with every other
diagnostic screen):

    --- 1. STORED CODES (MODE 03) ---      dim header
    RAW  P0171 System Too Lean Bank 1      GREEN  = exactly what the car said
    [FIX] Engine is running lean...        YELLOW = translation / what it means
      1. Listen for a hiss at idle...      YELLOW = what to do

Sections: stored codes, pending (07), permanent (0A), readiness monitors,
Mode 06 misfire test, live sensors vs sourced ranges, warm-up, ML monitor.
The translation here is rule-based (instant, offline, can't hallucinate);
the UI adds an AI summary at the bottom afterwards, also in yellow.

Ranges (sourced, same as tools.NORMAL_RANGES / WarningAdvisor): coolant
fully warm 85-105 C; fuel trims normal within +/-10 %, problem beyond 15 %;
rear (post-cat) O2 0.1-0.9 V.
"""
import time


class Section:
    def __init__(self, title):
        self.title = title
        self.raw = []          # list of str - exactly what was read
        self.level = "ok"      # "ok" | "watch" | "fix"
        self.meaning = ""      # one plain-English sentence
        self.steps = []        # what to do

    def set(self, level, meaning, steps=None):
        order = {"ok": 0, "watch": 1, "fix": 2}
        if order[level] >= order[self.level]:
            self.level = level
            self.meaning = meaning
            if steps is not None:
                self.steps = list(steps)


class HealthCheck:
    TAG = {"fix": "[FIX] ", "watch": "[WATCH] ", "ok": "[OK] "}

    def __init__(self, vehicle, readers, snapshot_fn, playbook,
                 anomaly_status_fn=None, anomaly_result_fn=None,
                 warmup_fn=None, module_scan_fn=None, module_label_fn=None):
        self._vehicle = vehicle
        self._readers = readers or {}
        self._snap = snapshot_fn
        self._book = playbook
        self._ml_status = anomaly_status_fn
        self._ml_result = anomaly_result_fn
        self._warmup = warmup_fn
        self._modules = module_scan_fn       # -> [(mod, result)] (UDS, all modules)
        self._mod_label = module_label_fn or (lambda m: m.get("req", "?"))

    @staticmethod
    def _num(v):
        return v if isinstance(v, (int, float)) else None

    def _read(self, name):
        reader = self._readers.get(name)
        if reader is None:
            return None, "reader not available (no car connected)"
        try:
            return reader.read(), ""
        except Exception as err:
            return None, "read failed: " + str(err)[:60]

    # ---- sections -----------------------------------------------------
    def _codes(self):
        sec = Section("STORED CODES (MODE 03)")
        if self._vehicle is None:
            sec.raw.append("no OBD connection")
            sec.set("watch", "Car not connected.",
                    ["Plug in the OBDLink, key ON, run HEALTH CHECK again."])
            return sec
        try:
            codes = self._vehicle.get_dtcs() or []
        except Exception as err:
            sec.raw.append("read failed: " + str(err)[:60])
            sec.set("watch", "Could not read stored codes this time.")
            return sec
        if not codes:
            sec.raw.append("none")
            sec.set("ok", "No check-engine codes stored.")
            return sec
        for entry in codes:
            code = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
            desc = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else ""
            sec.raw.append(str(code) + "  " + str(desc))
        sec.set("fix", str(len(codes)) + " stored code(s) - the check-engine light is (or was) on.",
                ["Open READ CODES and tap a code for its plain-English cause.",
                 "Fix the cause first; clearing only hides it until it sets again.",
                 "FREEZE FRAME shows the conditions when it set."])
        return sec

    def _pending(self):
        sec = Section("PENDING CODES (MODE 07)")
        raw, err = self._read("PENDING CODES")
        if err:
            sec.raw.append(err)
            sec.set("watch", "Pending codes could not be read.")
        elif raw:
            sec.raw.append(str(raw))
            sec.set("watch", "A fault was seen once but hasn't turned on the light yet.",
                    ["If it appears again next drive it becomes a stored code - fix it now."])
        else:
            sec.raw.append("none")
            sec.set("ok", "No early-warning codes.")
        return sec

    def _permanent(self):
        sec = Section("PERMANENT CODES (MODE 0A)")
        raw, err = self._read("PERMANENT CODES")
        if err or isinstance(raw, str):
            sec.raw.append(err or raw)
            sec.set("watch", "Permanent codes are not readable on this setup.")
        elif raw:
            sec.raw.append(str(raw))
            sec.set("fix", "Codes the ECU keeps until the repair is proven.",
                    ["They clear only after the fix AND a drive cycle where that monitor passes.",
                     "Clearing codes will not remove them."])
        else:
            sec.raw.append("none")
            sec.set("ok", "No permanent codes.")
        return sec

    def _readiness(self):
        sec = Section("READINESS MONITORS (MODE 01 PID 01)")
        raw, err = self._read("READINESS MONITORS")
        if err or not isinstance(raw, dict):
            sec.raw.append(err or "no data")
            sec.set("watch", "Readiness could not be read.")
            return sec
        avail = []
        incomplete = []
        for name, st in raw.items():
            short = name.replace("_MONITORING", "").lower()
            if st.get("available"):
                avail.append(short)
                done = "complete" if st.get("complete") else "NOT complete"
                if not st.get("complete"):
                    incomplete.append(short)
            else:
                done = "not used on this car"
            sec.raw.append(short.ljust(16) + done)
        if incomplete:
            sec.set("watch", str(len(avail) - len(incomplete)) + " of " + str(len(avail))
                    + " self-tests done - not inspection-ready yet.",
                    ["Normal after codes were cleared or the battery was off.",
                     "Drive 10+ min mixing city and steady highway, starting cold."])
        else:
            sec.set("ok", "All " + str(len(avail)) + " self-tests complete - inspection-ready.")
        return sec

    def _mode06(self):
        sec = Section("MISFIRE TEST (MODE 06)")
        raw, err = self._read("MONITOR TESTS (06)")
        if err:
            sec.raw.append(err)
            sec.set("watch", "Mode 06 could not be read.")
        elif raw is None:
            sec.raw.append("no data reported")
            sec.set("ok", "The car reported no misfire test results.")
        else:
            sec.raw.append(str(raw))
            if "FAIL" in str(raw).upper():
                sec.set("fix", "A misfire self-test is failing.",
                        ["Check spark plug and coil on the failing cylinder (swap coils to confirm)."])
            else:
                sec.set("ok", "Misfire self-test within limits.")
        return sec

    def _live(self):
        sec = Section("LIVE SENSORS (MODE 01)")
        snap = self._snap() or {}
        rpm = self._num(snap.get("rpm"))
        if rpm is None or rpm <= 0:
            sec.raw.append("rpm " + str(snap.get("rpm", "--")))
            sec.set("watch", "Engine off - live checks skipped.",
                    ["Start the engine and run HEALTH CHECK again."])
            return sec
        coolant = self._num(snap.get("coolant_temp"))
        stft = self._num(snap.get("stft_b1"))
        ltft = self._num(snap.get("ltft_b1"))
        o2 = self._num(snap.get("o2_b1s2"))
        run_min = self._num(snap.get("run_time")) or 0
        for label, v, unit in (("rpm", rpm, ""), ("coolant", coolant, " C"),
                               ("short trim", stft, " %"), ("long trim", ltft, " %"),
                               ("rear O2", o2, " V")):
            sec.raw.append(label.ljust(12) + ("--" if v is None else str(v) + unit))
        warm = coolant is not None and coolant >= 70
        sec.set("ok", "Sensors are in their normal ranges."
                + ("" if warm else " (Engine still warming up: trims not judged.)"))
        if coolant is not None and coolant > 105:
            sec.set("fix", "Coolant is above 105 C - running hot.",
                    self._book.steps_for_warning("COOLANT HIGH"))
        elif warm and coolant < 85 and run_min > 15:
            sec.set("watch", "Coolant never reached 85 C after 15+ min - thermostat may be stuck open.",
                    ["Check whether the heater blows hot; replace the thermostat if not."])
        for v in (ltft, stft):
            if not warm or v is None:
                continue
            sign = "lean" if v > 0 else "rich"
            if abs(v) > 15:
                sec.set("fix", "Fuel trim " + str(v) + " % - engine running " + sign + ".",
                        self._book.steps_for_warning("FUEL TRIM DRIFT " + sign))
            elif abs(v) > 10:
                sec.set("watch", "Fuel trim " + str(v) + " % - drifting " + sign + ".",
                        self._book.steps_for_warning("FUEL TRIM DRIFT " + sign)[:2])
        if warm and o2 is not None and (o2 < 0.1 or o2 > 0.95):
            sec.set("watch", "Rear O2 outside 0.1-0.9 V - possible weak catalytic converter or exhaust leak.",
                    ["Watch the O2 / CAT graph; a P0420 code would confirm the cat."])
        return sec

    def _warm(self):
        sec = Section("WARM-UP")
        st = (self._warmup() if self._warmup else None) or {}
        sec.raw.append("phase " + str(st.get("phase", "--")))
        if st.get("cold"):
            sec.set("watch", st.get("headline", "Engine cold."), [st.get("detail", "")])
        else:
            sec.set("ok", "Engine is not in a cold-start phase.")
        return sec

    def _ml(self):
        sec = Section("ML ANOMALY MONITOR")
        st = (self._ml_status() if self._ml_status else None) or {}
        if not st.get("loaded"):
            sec.raw.append("OFF: " + str(st.get("reason", "unknown")))
            sec.set("watch", "The learned-normal monitor is not running.",
                    ["Copy models/anomaly_model.joblib into ~/autopi-project/models/.",
                     "In the Pi venv run: pip install scikit-learn joblib"])
            return sec
        res = (self._ml_result() if self._ml_result else None) or {}
        sev = res.get("severity", "ok")
        sec.raw.append("regime " + str(res.get("regime", "--")) + "   status " + sev.upper()
                       + "   score " + str(res.get("score", "--")))
        for name in self._book.describe_culprits(res, limit=3):
            sec.raw.append(name)
        if sev == "ok":
            sec.set("ok", "Readings match what is normal for THIS car.")
        else:
            sec.set("watch" if sev == "watch" else "fix",
                    "Readings are unusual for this car (" + sev + ").",
                    self._book.steps_for_anomaly(res))
        return sec

    def _all_modules(self):
        sec = Section("ALL MODULES (UDS - ABS, AIRBAG, BODY, DCM...)")
        if self._modules is None:
            sec.raw.append("module access not available (no OBD connection)")
            sec.set("watch", "Only the engine computer was checked.")
            return sec
        results = self._modules()
        answered = [r for r in results if r[1]["ok"]]
        bad = [r for r in answered if r[1]["codes"]]
        sec.raw.append(str(len(results)) + " modules asked, " + str(len(answered)) + " answered")
        for mod, res in results:
            if not res["ok"]:
                sec.raw.append(self._mod_label(mod) + ": " + res["error"])
            elif res["codes"]:
                sec.raw.append(self._mod_label(mod) + ": " + ", ".join(
                    c["code"] + " (" + c["status_text"] + ")" for c in res["codes"]))
            else:
                sec.raw.append(self._mod_label(mod) + ": no codes")
        confirmed = [r for r in bad if any(c.get("severity") == "fix" for c in r[1]["codes"])]
        if bad and not confirmed:
            sec.set("watch", "A module logged an intermittent failure (not confirmed): "
                    + ", ".join(self._mod_label(m) for m, _r in bad) + ".",
                    ["Re-run ALL-MODULE SCAN after a drive; if it comes back, have it checked."])
            return sec
        if bad:
            steps = []
            for mod, res in bad:
                for c in res["codes"]:
                    if c.get("meaning"):
                        steps.append(c["code"].split("-")[0] + ": " + c["meaning"])
            steps.append("Open DIAGNOSTICS > MODULES and tap the module for detail.")
            sec.set("fix", "Codes stored outside the engine (" + ", ".join(
                self._mod_label(m) for m, _r in bad) + ").", steps)
        elif answered:
            sec.set("ok", "No codes in any module that answered.")
        else:
            sec.set("watch", "No module answered the all-module scan (key ON?).")
        return sec

    # ------------------------------------------------------------------
    def run(self):
        sections = []
        for part in (self._codes, self._pending, self._permanent,
                     self._readiness, self._mode06, self._live, self._warm,
                     self._ml, self._all_modules):
            try:
                sections.append(part())
            except Exception as err:
                sec = Section(part.__name__.strip("_").upper())
                sec.raw.append("check failed: " + str(err)[:60])
                sec.set("watch", "This check could not run.")
                sections.append(sec)
        return sections

    def render(self, sections):
        """-> [(text, kind)], kind in head/raw/ai. Same layout every run."""
        out = []
        fixes = [s for s in sections if s.level == "fix"]
        watches = [s for s in sections if s.level == "watch"]
        out.append(("--- RESULT  " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---", "head"))
        if not fixes and not watches:
            out.append(("PASS - nothing needs attention.", "ai"))
        else:
            out.append((str(len(fixes)) + " TO FIX, " + str(len(watches)) + " TO WATCH", "ai"))
            for s in fixes + watches:
                out.append(("  " + self.TAG[s.level] + s.title, "ai"))
        out.append(("", "raw"))
        return out + self.render_sections(sections)

    def render_sections(self, sections):
        """Just the numbered sections (used inside the full REPORT too)."""
        out = []
        for n, s in enumerate(sections):
            out.append(("--- " + str(n + 1) + ". " + s.title + " ---", "head"))
            for line in s.raw:
                out.append(("RAW  " + line, "raw"))
            out.append((self.TAG[s.level] + s.meaning, "ai"))
            for i, step in enumerate(s.steps):
                if step:
                    out.append(("  " + str(i + 1) + ". " + step, "ai"))
            out.append(("", "raw"))
        return out

    @staticmethod
    def facts(sections):
        """Compact raw facts for the AI summary prompt."""
        parts = []
        for s in sections:
            parts.append(s.title + ": " + "; ".join(s.raw) + " => " + s.level)
        return "\n".join(parts)
