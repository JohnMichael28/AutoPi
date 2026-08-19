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
        
        from autopi.dyno_capture import DynoCapture
        import json
        with open("config.json") as f:
            dcfg = json.load(f).get("dyno", {})
        self._dyno = DynoCapture(
            weight_lbs=dcfg.get("vehicle_weight_lbs", 4200),
            drivetrain_loss=dcfg.get("drivetrain_loss", 0.15),
            min_speed_mph=dcfg.get("min_pull_speed_mph", 15),
            max_speed_mph=dcfg.get("max_pull_speed_mph", 80))

    def update(self):
        pass

    def _instant_mpg(self):
        maf = self._vehicle.read_number(obd.commands.MAF)
        speed = self._vehicle.read_number(obd.commands.SPEED)
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
            value = self._vehicle.read_number(obd.commands.FUEL_LEVEL)
            if value is None:
                return "--"
            return round(value)
        except Exception:
            return "--"

    def _run_time_minutes(self):
        # Real engine run time from the car (PID 011F), in minutes. This is
        # how long the ENGINE has been running since start - not app uptime.
        # Returns "--" if the car doesn't report it or the engine's off.
        seconds = self._vehicle.read_number(obd.commands.RUN_TIME)
        if isinstance(seconds, (int, float)):
            return round(seconds / 60)
        return "--"

    def _check_codes(self):
        # Poll DTCs periodically (they're slow); explain any NEW code once.
        self._dtc_check_frame += 1
        if self._dtc_check_frame < 30:
            return
        self._dtc_check_frame = 0
        try:
            self._cached_codes = self._vehicle.get_dtcs() or []
        except Exception:
            self._cached_codes = []

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
        self._dyno.feed(reading.rpm, speed_mph)
        
        # Guardian mood reacts to fuel trims, but only after the condition
        # PERSISTS - a single noisy reading won't flip the face. Debounced.
        bad_now = None
        for t in (reading.stft_b1, reading.ltft_b1):
            if isinstance(t, (int, float)) and abs(t) > 15:
                bad_now = "bad"
            elif isinstance(t, (int, float)) and abs(t) > 10 and bad_now is None:
                bad_now = "warn"
        if bad_now is not None:
            self._fuel_bad_streak += 1
        else:
            self._fuel_bad_streak = 0
        # Only show the flag once it's been consistent for 3+ readings.
        self._fuel_flag = bad_now if self._fuel_bad_streak >= 3 else None
        
        return {
            "speed": int(round(reading.speed * 0.621371)) if isinstance(reading.speed, (int, float)) else "--",
            "boost": safe_round(reading.boost_psi, 1),
            "afr": safe_round(reading.afr, 1),
            "fuel": fuel_range,
            "engine_load": safe_round(reading.engine_load, 1),
            "oil_temp": "--",                          # not supported on most cars
            "rpm": safe_round(reading.rpm, 0),
            "run_time": self._run_time_minutes(),
            "coolant_temp": safe_round(reading.coolant_temp, 0),
            "voltage": safe_round(reading.voltage, 1),
            "timing": safe_round(reading.timing, 1),   # NOW populated
            "throttle": safe_round(reading.throttle, 1),  # NOW populated
            "intake_temp": safe_round(reading.intake_temp, 0),
            "stft_b1": safe_round(reading.stft_b1, 1),
            "ltft_b1": safe_round(reading.ltft_b1, 1),
            "mode": self.mode,
            "has_codes": self.has_codes,
            "needs_gas": self.needs_gas,
        }

    def get_dyno_run(self):
        return self._dyno.get_last_run()
    
    def arm_dyno(self):
        # UI START button - begin capturing the next pull.
        self._dyno.arm()

    def disarm_dyno(self):
        # UI STOP button - finish and save the current pull.
        self._dyno.disarm()

    def dyno_armed(self):
        # For the UI to show ARMED vs idle state.
        return self._dyno.is_armed()
    
    def fuel_mood_flag(self):
        # For the guardian mood - None/warn/bad from the cached snapshot trims.
        return self._fuel_flag

    def fuel_health(self):
        # Fresh read of all trims + O2 for the FUEL tab. Returns data + a
        # verdict from the researched thresholds: +/-10% ok, +/-15% warn, more bad.
        trims = self._vehicle.get_fuel_trims()
        o2 = self._vehicle.get_o2_voltage()
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