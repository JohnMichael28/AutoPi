import time

import obd

from autopi.ai_router import AIRouter


# --- Fuel range constants ---
TANK_GALLONS = 18.5          # 2023 Subaru Outback Wilderness tank capacity
EPA_COMBINED_MPG = 22.0      # fallback MPG before we have live driving data
AFR_GASOLINE = 14.7          # stoichiometric air:fuel ratio (gasoline)
FUEL_DENSITY_GPL = 2830.0    # grams of gasoline per US gallon


class VehicleData:
    """Real data provider backed by a live Vehicle. Matches SimData's
    interface EXACTLY (Adapter pattern). Honest '--' when car lacks a PID.

    Also explains any trouble code ONCE via the tiered AIRouter (instant
    offline fallback), and exposes the latest explanation for the guardian
    UI to speak."""

    def __init__(self, vehicle, ai_client=None):
        self._vehicle = vehicle
        # Router is optional so ui_test/SimData path never needs an AI.
        self._router = AIRouter(ai_client) if ai_client is not None else None

        self.mode = "highway"
        self.has_codes = False
        self.needs_gas = False
        self._start_time = time.time()
        self._dtc_check_frame = 0
        self._cached_codes = []
        self._seen_codes = set()
        self._mpg_samples = []
        self._mpg_window = 60
        
        self._fuel_bad_streak = 0

        # Latest code explanation, for the guardian to speak.
        self.last_code = None
        self.last_explanation = None
        self.last_tier = None
        
        self._fuel_flag = None
        self._last_snapshot = {}      # cached snapshot for fuel_health() to read
        self._reading = None          # this cycle's Reading (helpers use it)
        
        import json
        with open("config.json") as f:
            dcfg = json.load(f).get("dyno", {})

        # Automatic screen switching + automatic dyno capture. All thresholds
        # live in config.json under "drive_mode" (defaults in drive_mode.py).
        from autopi.drive_mode import DriveModeSelector, AutoDyno
        with open("config.json") as f:
            mcfg = json.load(f).get("drive_mode", {})
        # Screens are ALWAYS automatic. A dashboard picked from the menu is a
        # temporary override: it holds until the driving situation changes
        # (e.g. you pick TRACK while cruising; when you stop, auto takes over).
        self._mode_selector = DriveModeSelector(mcfg)
        self._override = None          # mode the driver picked, or None
        self._override_base = None     # auto pick at the moment they picked it

        # Cold-start guide: blue face + "safe to drive off" message.
        from autopi.warmup import WarmupMonitor
        with open("config.json") as f:
            wcfg = json.load(f).get("warmup", {})
        self._warmup = WarmupMonitor(wcfg)
        self._warmup_status = self._warmup.status()
        # Automatic dyno: always on, records every hard pull by itself.
        self._auto_dyno = AutoDyno(dcfg)

    def update(self):
        pass

    def _instant_mpg(self):
        # MAF + speed come from this cycle's reading (no extra OBD queries -
        # each extra query was ~0.1-0.2 s of lag on every screen update).
        maf = getattr(self._reading, "boost_psi", None)      # carries MAF g/s
        speed = getattr(self._reading, "speed", None)
        if maf is None or speed is None or maf <= 0:
            return None
        speed_mph = speed * 0.621371
        if speed_mph < 3:
            return None
        grams_per_hour = maf * 3600.0
        gallons_per_hour = grams_per_hour / (AFR_GASOLINE * FUEL_DENSITY_GPL / 1000.0)
        if gallons_per_hour <= 0:
            return None
        return speed_mph / gallons_per_hour

    def _average_mpg(self):
        sample = self._instant_mpg()
        # Only accept plausible cruising samples. Hard-acceleration produces
        # real but meaningless single-digit instant MPG that would poison the
        # rolling average and crater the range estimate.
        if sample is not None and 8 < sample < 80:
            self._mpg_samples.append(sample)
            if len(self._mpg_samples) > self._mpg_window:
                self._mpg_samples.pop(0)
        if not self._mpg_samples:
            return EPA_COMBINED_MPG, False
        avg = sum(self._mpg_samples) / len(self._mpg_samples)
        return avg, True

    def _fuel_range(self, fuel_percent):
        if not isinstance(fuel_percent, (int, float)):
            return "--"
        gallons_left = (fuel_percent / 100.0) * TANK_GALLONS
        avg_mpg, _is_live = self._average_mpg()
        return round(gallons_left * avg_mpg)

    def _read_fuel_level(self):
        try:
            value = getattr(self._reading, "fuel_level", None)
            if value is None:
                return "--"
            return round(value)
        except Exception:
            return "--"

    def _run_time_minutes(self):
        # Real engine run time from the car (PID 011F), in minutes. This is
        # how long the ENGINE has been running since start - not app uptime.
        # Returns "--" if the car doesn't report it or the engine's off.
        seconds = getattr(self._reading, "run_time", None)
        if isinstance(seconds, (int, float)):
            return round(seconds / 60)
        return "--"

    def _check_codes(self):
        # Poll DTCs periodically (they're slow); explain any NEW code once.
        self._dtc_check_frame += 1
        if self._dtc_check_frame < 30:
            return
        self._dtc_check_frame = 0
        # Only trust DTC reads when the connection is solid. A garbage read
        # during a marginal connection must NOT create a phantom code.
        if not self._vehicle.is_connected():
            return
        try:
            codes = self._vehicle.get_dtcs() or []
        except Exception:
            return
        # Require a code to appear on TWO consecutive checks before trusting it
        # (a one-off garbage read during a hiccup won't trigger the red face).
        prev = set(c[0] if isinstance(c, (list, tuple)) else c for c in self._cached_codes)
        now = set(c[0] if isinstance(c, (list, tuple)) else c for c in codes)
        confirmed = prev & now      # only codes present BOTH times
        if not confirmed:
            self._cached_codes = codes    # remember for next comparison
            self.has_codes = False        # don't flag on a single read
            return
        self._cached_codes = codes

        for entry in self._cached_codes:
            # get_dtcs returns (code, description) tuples
            code = entry[0] if isinstance(entry, (list, tuple)) else entry
            if code and code not in self._seen_codes:
                self._seen_codes.add(code)
                if self._router is not None:
                    result = self._router.explain_code(code)
                    self.last_code = code
                    self.last_explanation = result["text"]
                    self.last_tier = result["tier"]

    def snapshot(self):
        reading = self._vehicle.read_current()
        self._reading = reading
        self._check_codes()
        self.has_codes = len(self._cached_codes) > 0

        fuel_pct = self._read_fuel_level()
        fuel_range = self._fuel_range(fuel_pct)
        # Low-fuel warning keys off actual tank level, NOT the volatile
        # range estimate (which swings wildly with instantaneous MPG during
        # acceleration). A real low-fuel light triggers on level, not range.
        if isinstance(fuel_pct, (int, float)):
            self.needs_gas = fuel_pct <= 15      # ~15% tank, like a real low-fuel lamp

    
        
        def safe(value):
            return value if value is not None else "--"
        

        def safe_round(value, places=1):
            # Round numeric readings for display; pass through "--" for missing.
            if isinstance(value, (int, float)):
                return round(value, places)
            return "--"
        # Feed the dyno from the shared reading - no separate OBD query.
        speed_mph = None
        if isinstance(reading.speed, (int, float)):
            speed_mph = reading.speed * 0.621371
        speed_t = None
        if hasattr(self._vehicle, "fast_sample") and self._vehicle.fast_lane_running():
            _v, speed_t = self._vehicle.fast_sample("SPEED")
        self._auto_dyno.feed(reading.rpm, speed_mph, reading.throttle,
                             reading.engine_load, speed_t=speed_t)

        # Pick the screen from how the car is being driven. A menu pick holds
        # only until the auto pick changes, then automatic takes over again.
        auto_pick = self._mode_selector.update(
            speed=speed_mph, rpm=reading.rpm, throttle=reading.throttle,
            load=reading.engine_load, offroad_flag=None)
        if self._override is not None and auto_pick != self._override_base:
            self._override = None
        self.mode = self._override if self._override is not None else auto_pick

        self._warmup_status = self._warmup.update(
            rpm=reading.rpm, coolant=reading.coolant_temp, speed=speed_mph,
            throttle=reading.throttle)
        
        # Only evaluate fuel warnings when we have a VALID live reading (engine
        # running, real rpm). Garbage/partial readings during a connection drop
        # must NOT trip the guardian - that's the false-alarm cause.
        bad_now = None
        if isinstance(reading.rpm, (int, float)) and reading.rpm > 0:
            for t in (reading.stft_b1, reading.ltft_b1):
                if isinstance(t, (int, float)) and abs(t) > 15:
                    bad_now = "bad"
                elif isinstance(t, (int, float)) and abs(t) > 10 and bad_now is None:
                    bad_now = "warn"
        if bad_now is not None:
            self._fuel_bad_streak += 1
        else:
            self._fuel_bad_streak = 0
        self._fuel_flag = bad_now if self._fuel_bad_streak >= 3 else None
        
        result = {
            "speed": int(round(reading.speed * 0.621371)) if isinstance(reading.speed, (int, float)) else "--",
            "boost": safe_round(reading.boost_psi, 1),
            "afr": safe_round(reading.afr, 1),
            "fuel": fuel_range,
            "engine_load": safe_round(reading.engine_load, 1),
            "oil_temp": "--",
            "rpm": safe_round(reading.rpm, 0),
            "run_time": self._run_time_minutes(),
            "coolant_temp": safe_round(reading.coolant_temp, 0),
            "voltage": safe_round(reading.voltage, 1),
            "timing": safe_round(reading.timing, 1),
            "throttle": safe_round(reading.throttle, 1),
            "intake_temp": safe_round(reading.intake_temp, 0),
            "stft_b1": safe_round(reading.stft_b1, 1),
            "ltft_b1": safe_round(reading.ltft_b1, 1),
            "o2_b1s2": safe_round(reading.o2_b1s2, 2),
            "mode": self.mode,
            "has_codes": self.has_codes,
            "needs_gas": self.needs_gas,
            "warmup": dict(self._warmup_status),
        }
        self._last_snapshot = result      # cache for fuel_health (no extra reads)
        return result

    def get_dyno_run(self):
        # Points of the newest auto-captured pull: [(rpm, hp), ...] or [].
        last = self._auto_dyno.last()
        return list(last["points"]) if last else []

    def get_dyno_info(self):
        # Newest pull: {"time", "points", "peak_hp", "peak_rpm", ...} or None.
        return self._auto_dyno.last()

    def dyno_history(self):
        return self._auto_dyno.history()

    def dyno_status(self):
        return self._auto_dyno.status()

    def set_manual_mode(self, mode):
        # Driver picked a dashboard from the menu: show it now, and hand
        # control back to automatic as soon as the driving situation changes.
        self.mode = mode
        self._override = mode
        self._override_base = self._mode_selector.mode

    def fast_values(self):
        """Freshest RPM / throttle / speed (mph) from the fast lane, for the
        screen to draw every frame. Empty dict if the fast lane is off."""
        if not hasattr(self._vehicle, "fast_lane_running") \
                or not self._vehicle.fast_lane_running():
            return {}
        f = self._vehicle.fast_values()
        out = {}
        if isinstance(f.get("RPM"), (int, float)):
            out["rpm"] = round(f["RPM"])
        if isinstance(f.get("THROTTLE_POS"), (int, float)):
            out["throttle"] = round(f["THROTTLE_POS"], 1)
        if isinstance(f.get("SPEED"), (int, float)):
            out["speed"] = int(round(f["SPEED"] * 0.621371))
        return out

    def rpm_rate_hz(self):
        if hasattr(self._vehicle, "rpm_rate_hz"):
            return self._vehicle.rpm_rate_hz()
        return 0.0

    def priority(self):
        """Context manager: diagnostics get the OBD port first."""
        if hasattr(self._vehicle, "priority"):
            return self._vehicle.priority()
        import contextlib
        return contextlib.nullcontext()

    def warmup_status(self):
        return dict(self._warmup_status)
    
    def fuel_mood_flag(self):
        # For the guardian mood - None/warn/bad from the cached snapshot trims.
        return self._fuel_flag

    def fuel_health(self):
        # Read trims + O2 from the CACHED snapshot - NOT fresh OBD reads. This
        # avoids colliding with the snapshot thread on one connection (two
        # readers at once destabilize the ELM327). The background poller already
        # collects these on its slow-PID rotation. B2 is None on 4-cyl (Bank 1
        # only), which is correct for this engine.
        snap = self._last_snapshot
        trims = {
            "stft_b1": snap.get("stft_b1", "--"),
            "ltft_b1": snap.get("ltft_b1", "--"),
            "stft_b2": None,      # 4-cyl: Bank 2 not present
            "ltft_b2": None,
        }
        o2 = snap.get("o2_b1s2", "--")
        worst = 0.0
        for v in trims.values():
            if isinstance(v, (int, float)) and abs(v) > worst:
                worst = abs(v)
        if worst > 15:
            verdict = "bad"
        elif worst > 10:
            verdict = "warn"
        else:
            verdict = "ok"
        return {"trims": trims, "o2": o2, "worst": worst, "verdict": verdict}