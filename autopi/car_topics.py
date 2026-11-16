"""What does the driver's question need? -> the right car data, with the
answering FACT already worked out by code.

Why this exists (Nov 15, real car): every question ("do I need gas", "how
much longer...") got the same canned "car is running smoothly" answer. The
word "gas" wasn't routed anywhere, so the model got a generic overview and a
small on-device model ignored the 338-mile fuel range sitting in it.

Now each TOPIC has the words that trigger it and a builder that returns a
small dict: {"fact": "<one plain sentence code computed>", ...raw values}.
Code does the reasoning that must be right (miles left, hot or not, codes or
not); the AutoPi AI only has to phrase it and add advice. A question can hit
several topics ("how's my car, do I need gas") - all of them are included.
Things the car does NOT report over OBD (oil life, tire pressures, outside
temperature...) get an honest "AutoPi can't read that" fact instead of a guess.

Thresholds come from config.json "copilot.topics". No f-strings.
"""
import json
import re


DEFAULTS = {
    "fuel_low_miles": 50,          # suggest filling up below this range
    "fuel_low_pct": 15,            # same as the low-fuel face
    "coolant_hot_c": 105,
    "coolant_warm_c": 85,
    "trim_normal_pct": 10,
    "trim_problem_pct": 15,
    "max_topics": 3,
}

# Speech recognition (Vosk small model) mishears some car words. Fix the
# common ones before matching - seen on the real car: "gods" for "codes".
HEARD_AS = {"gods": "codes", "god's": "codes", "coats": "codes",
            "gash": "gas", "guess": "gas", "cool and": "coolant",
            "cool aunt": "coolant", "rpms": "rpm", "r p m": "rpm"}


def _num(v):
    return v if isinstance(v, (int, float)) else None


class CarTopics:
    def __init__(self, snapshot_fn, dtc_fn=None, health_fn=None, dyno_fn=None,
                 cfg=None):
        self._snap = snapshot_fn
        self._dtcs = dtc_fn
        self._health = health_fn
        self._dyno = dyno_fn
        self._c = dict(DEFAULTS)
        self._c.update(cfg or {})
        # (name, trigger words, builder) - order = priority when trimming.
        self.TOPICS = [
            ("fuel", ("gas", "fuel level", "fuel range", "fuel left", "out of fuel",
                      "low on fuel", "need fuel", "tank", "range", "fill up", "refuel",
                      "empty", "miles left", "how far", "station", "petrol",
                      "how much longer"), self.fuel),
            ("codes", ("code", "check engine", "engine light", "cel ", "trouble",
                       "error", "dtc", "warning light", "light on"), self.codes),
            ("modules", ("module", "dcm", "telematics", "starlink", "eyesight",
                         "sonar", "camera", "radar", "airbag", "abs ", "brake",
                         "air condition", "a c ", "ac ", "dealer", "recall",
                         "steering", "keyless", "gateway"), self.modules),
            ("temperature", ("temp", "hot", "overheat", "coolant", "cold",
                             "warm", "heat"), self.temperature),
            ("warmup", ("ready", "drive off", "safe to drive", "idle", "cold start",
                        "wait", "warm up", "warming"), self.warmup),
            ("fuel_mix", ("trim", "rich", "lean", "mixture", "mpg", "mileage",
                          "economy", "o2", "oxygen", "sensor"), self.fuel_mix),
            ("engine", ("rpm", "speed", "fast", "load", "throttle", "rev",
                        "turbo", "boost", "running", "engine", "misfire",
                        "shaking", "rough"), self.engine),
            ("power", ("dyno", "horsepower", "hp", "power", "torque", "pull",
                       "0 to 60", "zero to sixty", "quick"), self.power),
            ("battery", ("battery", "volt", "alternator", "charging", "charge"),
             self.battery),
            ("cant_read", ("oil", "tire", "tyre", "pressure", "outside",
                           "weather", "service", "maintenance", "wiper",
                           "washer", "coolant level", "brake pad"), self.cant_read),
            ("overview", ("how is", "how's", "hows", "doing", "overall", "health",
                          "status", "everything", "anything wrong", "problem",
                          "okay", "ok ", "fine", "good"), self.overview),
        ]

    # ---- picking topics --------------------------------------------------
    @staticmethod
    def clean(text):
        q = " " + str(text).lower().replace("?", " ").replace(",", " ") + " "
        for wrong, right in HEARD_AS.items():
            q = q.replace(" " + wrong + " ", " " + right + " ")
        return q

    def pick(self, question, history=None):
        """Topic names for the question. A vague follow-up ("is that bad?")
        inherits the previous question's topics; nothing at all = overview."""
        found = self._match(question)
        if not found and history:
            found = self._match(history[-1][0])
        if not found:
            found = ["overview"]
        return found[:self._c["max_topics"]]

    def _match(self, text):
        # A trigger must start at a word ("abs " won't fire on "absolutely");
        # a trailing space in a trigger means "whole word only".
        q = self.clean(text)
        hits = []
        for name, words, _fn in self.TOPICS:
            for w in words:
                if re.search("(?<![a-z0-9'])" + re.escape(w), q):
                    hits.append(name)
                    break
        return hits

    @staticmethod
    def as_data(gathered):
        """Compact text for the model: one line per topic, FACT first."""
        lines = []
        for name, data in gathered:
            extra = ", ".join(k + "=" + (json.dumps(v) if isinstance(v, (list, dict)) else str(v))
                              for k, v in data.items() if k != "fact")
            lines.append(name.upper() + " FACT: " + str(data.get("fact", ""))
                         + ((" | " + extra) if extra else ""))
        return "\n".join(lines)

    def gather(self, question, history=None):
        """-> [(topic, data dict)] for the picked topics."""
        out = []
        builders = dict((n, fn) for n, _w, fn in self.TOPICS)
        for name in self.pick(question, history):
            try:
                out.append((name, builders[name]()))
            except Exception as err:
                out.append((name, {"fact": "AutoPi couldn't read the " + name
                                   + " data just now (" + str(err)[:40] + ")."}))
        return out

    # ---- helpers ---------------------------------------------------------
    def _s(self):
        return self._snap() or {}

    def _extra(self):
        try:
            return (self._health() if self._health else {}) or {}
        except Exception:
            return {}

    def _engine_codes(self):
        if self._dtcs is None:
            return None
        codes = self._dtcs() or []
        return [c[0] if isinstance(c, (list, tuple)) else str(c) for c in codes]

    # ---- topic builders ----------------------------------------------------
    def fuel(self):
        s = self._s()
        miles = _num(s.get("fuel"))
        pct = _num(s.get("fuel_pct"))
        low_m = self._c["fuel_low_miles"]
        low_p = self._c["fuel_low_pct"]
        if miles is None and pct is None:
            fact = "AutoPi can't read the fuel level right now - check the gas gauge."
        else:
            parts = []
            if miles is not None:
                parts.append("about " + str(int(miles)) + " miles of range left")
            if pct is not None:
                parts.append("the tank is about " + str(int(pct)) + "% full")
            low = (miles is not None and miles < low_m) or (pct is not None and pct <= low_p)
            fact = ("You have " + " and ".join(parts) + ". "
                    + ("Get gas soon." if low else "No need for gas yet."))
        return {"fact": fact, "range_miles": s.get("fuel", "--"),
                "tank_percent": s.get("fuel_pct", "--"),
                "note": "range is an estimate that swings with how you drive"}

    def codes(self):
        eng = self._engine_codes()
        mods = self._extra().get("modules")
        if eng is None:
            fact = "AutoPi couldn't read the engine codes right now."
        elif eng:
            fact = "The engine computer has " + str(len(eng)) + " code(s): " + ", ".join(eng) + "."
        else:
            fact = "The engine computer has no trouble codes - check-engine light off."
        out = {"fact": fact, "engine_codes": eng if eng else "none"}
        if mods:
            out["other_modules"] = mods
        return out

    def modules(self):
        mods = self._extra().get("modules") or ["module scan not run yet"]
        head = mods[0]
        fact = ("Other computers in the car (not the engine): " + head + "."
                + ("" if len(mods) > 1 else " Run DIAGNOSTICS > ALL-MODULE SCAN for details."
                   if "not run" in head else ""))
        return {"fact": fact, "modules": mods[1:] or "no codes"}

    def temperature(self):
        s = self._s()
        c = _num(s.get("coolant_temp"))
        if c is None:
            fact = "AutoPi can't read the coolant temperature right now."
        elif c >= self._c["coolant_hot_c"]:
            fact = ("Coolant is " + str(int(c)) + " C - that's HOT. Pull over safely, "
                    "let it idle or shut it off, and check the coolant level when cool.")
        elif c >= self._c["coolant_warm_c"]:
            fact = "Coolant is " + str(int(c)) + " C - normal operating temperature."
        else:
            fact = ("Coolant is " + str(int(c)) + " C - still warming up (normal is "
                    + str(self._c["coolant_warm_c"]) + "-" + str(self._c["coolant_hot_c"])
                    + " C). Drive gently until it's warm.")
        return {"fact": fact, "coolant_c": s.get("coolant_temp", "--"),
                "intake_air_c": s.get("intake_temp", "--")}

    def warmup(self):
        s = self._s()
        w = s.get("warmup") or {}
        head = w.get("headline") or ""
        if head:
            fact = "Warm-up: " + head + "."
        else:
            fact = "The engine is warm and the idle has settled - you're good to drive."
        return {"fact": fact, "rpm": s.get("rpm", "--"),
                "coolant_c": s.get("coolant_temp", "--")}

    def fuel_mix(self):
        s = self._s()
        lt = _num(s.get("ltft_b1"))
        st = _num(s.get("stft_b1"))
        normal = self._c["trim_normal_pct"]
        problem = self._c["trim_problem_pct"]
        if lt is None:
            fact = "AutoPi can't read the fuel trims right now."
        else:
            total = lt + (st or 0)
            way = "rich (the computer is taking fuel out)" if total < 0 else \
                "lean (the computer is adding fuel)"
            if abs(total) <= normal:
                fact = ("Fuel mix is normal: long-term trim " + str(lt) + " %, short-term "
                        + str(st) + " % (normal is within +/-" + str(normal) + " %).")
            elif abs(total) <= problem:
                fact = ("Fuel mix is slightly " + way + ": " + str(round(total, 1))
                        + " % total trim. Worth watching, not urgent.")
            else:
                fact = ("Fuel mix is " + way + ": " + str(round(total, 1))
                        + " % total trim - past " + str(problem) + " %, have it checked.")
        return {"fact": fact, "long_trim_pct": s.get("ltft_b1", "--"),
                "short_trim_pct": s.get("stft_b1", "--"),
                "rear_o2_v": s.get("o2_b1s2", "--")}

    def engine(self):
        s = self._s()
        fact = ("Engine right now: " + str(s.get("rpm", "--")) + " rpm, "
                + str(s.get("speed", "--")) + " mph, load " + str(s.get("engine_load", "--"))
                + " %, throttle " + str(s.get("throttle", "--")) + " %.")
        out = {"fact": fact, "rpm": s.get("rpm", "--"), "speed_mph": s.get("speed", "--"),
               "load_pct": s.get("engine_load", "--"), "airflow_g_s": s.get("boost", "--")}
        ml = self._extra()
        if ml.get("warnings_now") not in (None, "none"):
            out["autopi_warnings"] = ml.get("warnings_now")
        if ml.get("ml_unusual"):
            out["unusual_readings"] = ml.get("ml_unusual")
        return out

    def power(self):
        info = self._dyno() if self._dyno else None
        if not info:
            return {"fact": "No dyno pull recorded yet - AutoPi records one automatically "
                            "during a hard acceleration from low speed."}
        return {"fact": "Last dyno pull (" + str(info.get("time")) + "): about "
                        + str(info.get("peak_hp")) + " hp at " + str(info.get("peak_rpm"))
                        + " rpm (an estimate from acceleration, not a real dyno)."}

    def battery(self):
        s = self._s()
        return {"fact": "The voltage this car reports over OBD reads low even when "
                        "charging is fine, so AutoPi doesn't judge the battery from it. "
                        "No charging warning light = charging system OK.",
                "module_voltage_v": s.get("voltage", "--")}

    def cant_read(self):
        return {"fact": "This car doesn't report oil life/level, tire pressures, outside "
                        "temperature or service reminders over OBD, so AutoPi can't read "
                        "them - check the dashboard (the TPMS light covers tires)."}

    def overview(self):
        s = self._s()
        ex = self._extra()
        eng = self._engine_codes()
        bits = []
        c = _num(s.get("coolant_temp"))
        if c is not None:
            bits.append("coolant " + str(int(c)) + " C")
        miles = _num(s.get("fuel"))
        if miles is not None:
            bits.append(str(int(miles)) + " miles of fuel range")
        bits.append(("engine codes: " + ", ".join(eng)) if eng else "no engine codes")
        warns = ex.get("warnings_now")
        bits.append("AutoPi warnings: " + (", ".join(warns) if isinstance(warns, list)
                                           else "none"))
        mods = ex.get("modules") or []
        if mods:
            bits.append(mods[0])
        fact = "Car status: " + "; ".join(bits) + "."
        out = {"fact": fact}
        if ex.get("ml_monitor"):
            out["ml_monitor"] = ex["ml_monitor"]
        if len(mods) > 1:
            out["module_codes"] = mods[1:]
        return out
