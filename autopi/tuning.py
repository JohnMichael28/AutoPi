class TuningAnalyzer:
    """Encapsulates tuning/knock analysis logic. Pure evaluation (Model) -
    takes readings and returns alerts based on Cobb's real FA24 thresholds.
    Contextual: only flags knock under load (idle readings are noise).
    Separated from display so the logic can be unit-tested."""

    # --- Context: knock is only meaningful UNDER LOAD (research-backed) ---
    LOAD_RPM_MIN = 2500
    LOAD_THROTTLE_MIN = 40

    # --- Cobb's real FA24 danger thresholds ---
    DAM_OPTIMAL = 1.0
    FBKC_WOT_DANGER = -2.8
    FLKC_WOT_DANGER = -2.8
    KNOCK_CRUISE_DANGER = -4.2

    # Timing-drop knock proxy (standard-PID method)
    TIMING_DROP_WARN = 5.0

    # Subaru-specific PIDs not yet verified on real car (honest placeholder)
    SUBARU_PIDS_VERIFIED = False

    def under_load(self, rpm, throttle):
        # Is the engine working hard enough for knock to be meaningful?
        if rpm is None or throttle is None:
            return False
        return rpm >= self.LOAD_RPM_MIN and throttle >= self.LOAD_THROTTLE_MIN

    def read_subaru_knock(self):
        # Returns DAM/FBKC/FLKC - all None until PIDs verified on the real car.
        if not self.SUBARU_PIDS_VERIFIED:
            return {"DAM": None, "FBKC": None, "FLKC": None}
        # (Once verified, real Mode-22 custom queries go here)
        return {"DAM": None, "FBKC": None, "FLKC": None}

    def evaluate(self, timing, last_timing, rpm, throttle, subaru):
        # Returns a list of knock/tuning alert strings. Contextual.
        alerts = []
        loaded = self.under_load(rpm, throttle)

        # Standard-PID proxy: sharp timing drop under load = possible knock
        if loaded and timing is not None and last_timing is not None:
            drop = last_timing - timing
            if drop >= self.TIMING_DROP_WARN:
                alerts.append("KNOCK? Timing pulled " + str(round(drop, 1))
                              + " deg under load - possible knock")

        # Subaru-specific (only fire if values are actually available)
        dam = subaru.get("DAM")
        if dam is not None and dam < self.DAM_OPTIMAL:
            alerts.append("DAM dropped to " + str(dam)
                          + " (optimal 1.0) - knock detected")

        fbkc = subaru.get("FBKC")
        if fbkc is not None:
            if loaded and fbkc <= self.FBKC_WOT_DANGER:
                alerts.append("FBKC " + str(fbkc) + " at load - real knock, ease off")
            elif not loaded and fbkc <= self.KNOCK_CRUISE_DANGER:
                alerts.append("FBKC " + str(fbkc) + " at cruise - problem, check fuel")

        flkc = subaru.get("FLKC")
        if flkc is not None:
            if loaded and flkc <= self.FLKC_WOT_DANGER:
                alerts.append("FLKC " + str(flkc)
                              + " learned at load - knock history here")

        return alerts