"""Tool registry with permission profiles (CS 5390 Week 7 + Topic 5B/6A).

Each tool = name + description + argument schema + a Python function + a
permission ("read" or "actuate"). The model only ever sees the CATALOG for the
active profile and only ever sends a REQUEST. This registry is the harness:

  - unknown tool names are rejected (not in catalog = doesn't exist)
  - arguments are type-checked against the schema; extra args are rejected
  - "actuate" tools are never listed in, and never run from, a profile that
    doesn't allow them (least privilege)
  - every actuate call also passes authorize(): a per-call check against the
    car's own live data (speed must be 0, etc.), never against model text

"MCP is the delivery, not the decision" - the same holds here.
"""
import json


class ToolError(Exception):
    """A tool refused or failed. Message is safe to show the model."""


class Tool:
    TYPES = {"string": str, "number": (int, float), "integer": int,
             "boolean": bool, "array": list}

    def __init__(self, name, description, params, func, permission="read",
                 required=None):
        self.name = name
        self.description = description
        self.params = params            # {"arg": {"type": "number", "description": ...}}
        self.func = func
        self.permission = permission
        self.required = required if required is not None else list(params)

    def catalog_entry(self):
        return {"name": self.name, "description": self.description,
                "arguments": self.params, "required": self.required}

    def check_args(self, args):
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        for key in args:
            if key not in self.params:
                raise ToolError("unknown argument '" + str(key) + "' for " + self.name)
        for key in self.required:
            if key not in args:
                raise ToolError("missing argument '" + key + "' for " + self.name)
        for key in args:
            want = self.TYPES.get(self.params[key].get("type"), object)
            value = args[key]
            if isinstance(value, bool) and want is not bool:
                raise ToolError("argument '" + key + "' has the wrong type")
            if not isinstance(value, want):
                raise ToolError("argument '" + key + "' must be "
                                + self.params[key].get("type", "?"))


class ToolRegistry:
    def __init__(self, profiles, live_snapshot=None):
        # profiles: {"driving": {"allow": ["read"]}, "service": {"allow": ["read", "actuate"]}}
        self._tools = {}
        self._profiles = profiles
        self._live = live_snapshot      # callable -> latest snapshot dict

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError("duplicate tool " + tool.name)
        self._tools[tool.name] = tool

    def has(self, name):
        return name in self._tools

    def _allowed(self, tool, profile):
        allow = self._profiles.get(profile, {}).get("allow", ["read"])
        return tool.permission in allow

    def catalog(self, profile):
        return [t.catalog_entry() for t in self._tools.values()
                if self._allowed(t, profile)]

    def authorize(self, tool, args):
        """Per-call check for anything that touches the car. Uses ONLY live
        data from AutoPi's own reader. Never trusts the model or tool text."""
        if tool.permission != "actuate":
            return
        snap = self._live() if self._live is not None else {}
        speed = snap.get("speed")
        if not isinstance(speed, (int, float)) or speed != 0:
            raise ToolError("refused: the car must be stopped (speed 0) for "
                            + tool.name)

    def run(self, name, args, profile):
        """Validate, authorize, execute. Returns a JSON string (the model's
        observation). Raises ToolError with a reason the model may read."""
        tool = self._tools.get(name)
        if tool is None or not self._allowed(tool, profile):
            # Same message either way: a hidden tool must look nonexistent.
            raise ToolError("no tool named '" + str(name) + "' is available")
        tool.check_args(args)
        self.authorize(tool, args)
        result = tool.func(**args)
        return json.dumps(result, default=str)


# --------------------------------------------------------------------------
# AutoPi's built-in READ tools. Pure functions over data AutoPi already has.
# The model never does engine math - it calls these (Week 7 + Topic 6A:
# "trusted Python code calculates").
# --------------------------------------------------------------------------

LIVE_KEYS = ["rpm", "speed", "coolant_temp", "intake_temp", "engine_load",
             "throttle", "voltage", "timing", "afr", "stft_b1", "ltft_b1",
             "o2_b1s2", "boost", "fuel", "fuel_pct", "run_time", "mode"]


NORMAL_RANGES = {
    "coolant_temp_C_warm": [85, 105],
    # No voltage range on purpose: this car's OBD module voltage reads low
    # (under 12V) even when charging is fine, so it must not be judged.
    "voltage_note": "module voltage is NOT judged on this car - do not call it low",
    "fuel_trim_pct_normal": [-10, 10],
    "fuel_trim_pct_problem_beyond": 15,
    "o2_b1s2_V_typical": [0.1, 0.9],
}


OVERVIEW_KEYS = ["rpm", "speed", "coolant_temp", "engine_load", "stft_b1",
                 "ltft_b1", "o2_b1s2", "fuel", "run_time"]


def build_default_registry(profiles, snapshot_fn, dtc_fn=None, explain_fn=None,
                           dyno_fn=None, health_fn=None):
    reg = ToolRegistry(profiles, live_snapshot=snapshot_fn)

    def get_live_data(keys):
        snap = snapshot_fn() or {}
        out = {}
        for k in keys:
            if k not in LIVE_KEYS:
                raise ToolError("unknown live data key '" + str(k) + "'. Valid: "
                                + ", ".join(LIVE_KEYS))
            out[k] = snap.get(k, "--")
        out["_units"] = "speed mph, temps C, voltage V, trims %, boost=MAF g/s"
        # Reference ranges come from code, so the model can quote them and
        # the judge can verify them (numbers must be in the evidence).
        out["_normal_ranges"] = NORMAL_RANGES
        return out

    reg.register(Tool(
        "get_live_data",
        "Read current live values from the car. Use for any question about "
        "what the engine is doing right now.",
        {"keys": {"type": "array", "description": "any of: " + ", ".join(LIVE_KEYS)}},
        get_live_data))

    def get_trouble_codes():
        if dtc_fn is None:
            raise ToolError("trouble codes are not available")
        codes = []
        for entry in dtc_fn() or []:
            if isinstance(entry, (list, tuple)):
                codes.append({"code": str(entry[0]),
                              "description": str(entry[1]) if len(entry) > 1 else ""})
            else:
                codes.append({"code": str(entry), "description": ""})
        return {"stored_codes": codes, "count": len(codes)}

    reg.register(Tool(
        "get_trouble_codes",
        "Read stored (check-engine) trouble codes from the engine computer.",
        {}, get_trouble_codes))

    def explain_code(code):
        code = code.upper().strip()
        if len(code) != 5 or code[0] not in "PCBU":
            raise ToolError("not a valid trouble code: " + code)
        if explain_fn is None:
            raise ToolError("no code reference available")
        return {"code": code, "reference": explain_fn(code)}

    reg.register(Tool(
        "explain_code",
        "Look up the reference meaning of one trouble code like P0301.",
        {"code": {"type": "string", "description": "5-character code, e.g. P0420"}},
        explain_code))

    def injector_duty(pulse_width_ms, rpm):
        if pulse_width_ms <= 0 or rpm <= 0:
            raise ToolError("pulse_width_ms and rpm must be positive")
        duty = pulse_width_ms * rpm / 1200.0
        return {"duty_cycle_pct": round(duty, 1),
                "formula": "duty% = PW_ms x RPM / 1200 (sequential, 720 deg)",
                "note": "over 85% means the injector is near its limit"}

    reg.register(Tool(
        "injector_duty",
        "Compute fuel injector duty cycle from pulse width and RPM.",
        {"pulse_width_ms": {"type": "number", "description": "injector on-time in ms"},
         "rpm": {"type": "number", "description": "engine RPM"}},
        injector_duty))

    def relative_compression(peak_amps, trough_amps):
        peaks = [float(p) for p in peak_amps]
        if len(peaks) < 2:
            raise ToolError("need at least 2 compression peaks")
        rises = [p - float(trough_amps) for p in peaks]
        top = max(rises)
        if top <= 0:
            raise ToolError("peaks must be above the trough current")
        pct = [round(r / top * 100.0, 1) for r in rises]
        low = min(pct)
        return {"relative_pct_by_hump": pct, "lowest_pct": low,
                "spread_pct": round(100.0 - low, 1),
                "note": "relative only; confirm a low hump with a real "
                        "compression or leak-down test"}

    reg.register(Tool(
        "relative_compression",
        "Compute relative compression per cylinder hump from starter-current "
        "peaks captured by the scope during cranking.",
        {"peak_amps": {"type": "array", "description": "peak current of each hump"},
         "trough_amps": {"type": "number", "description": "current between humps"}},
        relative_compression))

    def last_dyno_pull():
        info = dyno_fn() if dyno_fn is not None else None
        if not info:
            return {"available": False}
        return {"available": True, "time": info["time"],
                "peak_hp_estimate": info["peak_hp"], "at_rpm": info["peak_rpm"],
                "note": "OBD acceleration estimate, not a calibrated dyno"}

    reg.register(Tool(
        "last_dyno_pull",
        "Get the most recent automatically captured dyno pull (estimate).",
        {}, last_dyno_pull))

    def car_health_overview():
        # One look at everything: live core values, engine codes, AutoPi's
        # current warnings, the ML monitor, warm-up, and the last module scan.
        snap = snapshot_fn() or {}
        out = {"live": {k: snap.get(k, "--") for k in OVERVIEW_KEYS}}
        try:
            codes = dtc_fn() if dtc_fn is not None else []
            out["engine_codes"] = [c[0] if isinstance(c, (list, tuple)) else str(c)
                                   for c in (codes or [])] or "none"
        except Exception as err:
            out["engine_codes"] = "could not read (" + str(err)[:40] + ")"
        extra = {}
        if health_fn is not None:
            try:
                extra = health_fn() or {}
            except Exception:
                extra = {}
        out.update(extra)
        out["_units"] = "speed mph, temps C, trims %, run_time min"
        out["_normal_ranges"] = NORMAL_RANGES
        return out

    reg.register(Tool(
        "car_health_overview",
        "Everything at once: live engine values, engine trouble codes, AutoPi's "
        "active warnings, the ML monitor, warm-up state and codes found in other "
        "modules (DCM, EyeSight...). Use for 'how is my car doing' questions.",
        {}, car_health_overview))

    return reg
