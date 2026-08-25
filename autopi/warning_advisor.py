"""WarningAdvisor - the rule-based "autopilot" that turns live snapshot data
into one-line, situation-aware warnings for the issue face.

Each warning is a short sentence (what's wrong + what to do), sourced from
mechanic / off-road / track references. Warnings are SITUATION-AWARE: the same
reading means different things while idling vs. cruising vs. climbing vs.
driving hard, so we detect the situation first and gate warnings accordingly
(you never get "you're idling too long" while cruising the highway).

This is separate from the ML model: these are explicit, sourced rules. When the
ML anomaly model is trained later, it feeds ONE MORE warning into the same list
through add_ml_warning(), so the issue face shows rule-based + learned warnings
together.

Thresholds live here as named constants (not magic numbers) so they're easy to
tune, and each carries a source comment.
"""


# ---- Situations -----------------------------------------------------------
IDLING = "idling"        # stopped, engine on
CRUISING = "cruising"    # moving at steady moderate load
STRAINING = "straining"  # high load at low speed (climbing / crawling / towing)
HARD = "hard"            # high rpm + high load (spirited / track)
UNKNOWN = "unknown"


class WarningAdvisor:
    def __init__(self, config=None):
        # Optional config dict can override any threshold later. For now the
        # sourced defaults are inline. Streak counters debounce warnings so a
        # single noisy reading can't trigger an alarm (needs N in a row).
        self._streaks = {}
        self._need = 3            # consecutive cycles before a warning fires
        # Track recent "hard driving" so we can advise a turbo cooldown after a
        # pull ends (sourced: idle briefly at low load before shutoff).
        self._hard_recent = 0
        self._hard_decay = 40     # ~snapshots to remember a recent hard pull
        self._last_timing = None      # for timing-drop knock detection

    # -- situation detection ------------------------------------------------
    def _situation(self, s):
        rpm = _num(s.get("rpm"))
        speed = _num(s.get("speed"))       # mph (snapshot already converts)
        load = _num(s.get("engine_load"))  # percent
        if rpm is None:
            return UNKNOWN
        if speed is not None and speed <= 2 and rpm > 0:
            return IDLING
        # STRAINING: high load but low speed, sustained = climbing a grade or
        # crawling in low range. Sourced: low-range/steep-climb generates heat
        # with little airflow (mechaniquad, advancedtransmission).
        if load is not None and speed is not None and load >= 85 and speed < 15:
            return STRAINING
        # HARD: high rpm AND high load = spirited/track driving.
        if load is not None and rpm is not None and load >= 80 and rpm >= 4000:
            return HARD
        if speed is not None and speed >= 15:
            return CRUISING
        return UNKNOWN

    # -- streak helper: only fire after _need consecutive trips -------------
    def _fire(self, key, active):
        n = self._streaks.get(key, 0)
        if active:
            n += 1
        else:
            n = 0
        self._streaks[key] = n
        return n >= self._need

    # -- main entry ---------------------------------------------------------
    def check(self, snap):
        """Return a list of one-line warning strings for this snapshot,
        highest priority first. Empty list = all good."""
        warnings = []
        sit = self._situation(snap)

        # track recent hard driving (for the cooldown-after-pull advice)
        if sit == HARD:
            self._hard_recent = self._hard_decay
        elif self._hard_recent > 0:
            self._hard_recent -= 1

        # ---- 1. KNOCK (sharp timing DROP under real load) -----------------
        # Sourced (skanyx): knock = ECM pulling timing under load. But negative
        # or low timing alone is NORMAL at idle/cruise/decel - the real signal
        # is a SHARP DROP in timing while genuinely under load. Matches the
        # TuningAnalyzer method: load = rpm>=2500 AND throttle>=40, and timing
        # must drop >=5 degrees from the previous reading. This avoids the
        # false alarms that firing on "timing<0" alone produces.
        timing = _num(snap.get("timing"))
        rpm = _num(snap.get("rpm"))
        throttle = _num(snap.get("throttle"))
        under_load = (rpm is not None and throttle is not None
                      and rpm >= 2500 and throttle >= 40)
        knock = False
        if under_load and timing is not None and self._last_timing is not None:
            drop = self._last_timing - timing
            knock = drop >= 5.0
        if timing is not None:
            self._last_timing = timing
        if self._fire("knock", knock):
            warnings.append("KNOCK - timing pulled sharply under load, ease off")

        # ---- 2. COOLANT (situation-aware thresholds) ----------------------
        # Sourced: fully warm 88-105C (skanyx/foxwell). Track concern >100C
        # (hpacademy). Warn ~106C, critical ~113C. Climbing/track get a
        # slightly higher warn bar since load-heat is expected, but still flag.
        coolant = _num(snap.get("coolant_temp"))
        if coolant is not None:
            if self._fire("coolant_crit", coolant >= 113):
                warnings.append("COOLANT CRITICAL (" + str(int(coolant)) + "C) - stop safely and let it cool")
            elif self._fire("coolant_warn", coolant >= 106):
                warnings.append("COOLANT HIGH (" + str(int(coolant)) + "C) - ease off and let it cool")

        # ---- 3. LOW VOLTAGE (charging/battery) ----------------------------
        # Sourced: running voltage should be ~13.5-14.5V; below ~13.0V while
        # running means the alternator isn't keeping up (fleetrabbit + your own
        # confirmed low-voltage catch). Only meaningful with engine running.
        volts = _num(snap.get("voltage"))
        rpm = _num(snap.get("rpm"))
        if volts is not None and rpm is not None and rpm > 0:
            if self._fire("volt_crit", volts < 12.2):
                warnings.append("LOW VOLTAGE (" + str(volts) + "V) - charging issue, you may stall")
            elif self._fire("volt_warn", volts < 13.0):
                warnings.append("VOLTAGE LOW (" + str(volts) + "V) - alternator not keeping up")

        # ---- 4. FUEL TRIM DRIFT -------------------------------------------
        # Sourced (fleetrabbit/skanyx): STFT beyond +/-10%, LTFT beyond
        # +/-15-20% signals a fuel system issue (vacuum/exhaust leak, injector,
        # sensor) - often months before a check-engine light. Trims are only
        # meaningful warm and in closed loop, so gate on coolant being warm.
        stft = _num(snap.get("stft_b1"))
        ltft = _num(snap.get("ltft_b1"))
        warm = coolant is not None and coolant >= 70
        if warm and ltft is not None:
            if self._fire("ltft", abs(ltft) >= 15):
                sign = "rich" if ltft < 0 else "lean"
                warnings.append("FUEL TRIM DRIFT (LTFT " + str(ltft) + "%, " + sign + ") - check for a leak/fuel issue")
        if warm and stft is not None:
            if self._fire("stft", abs(stft) >= 12):
                warnings.append("FUEL TRIM SWING (STFT " + str(stft) + "%) - running off-target")

        # ---- 5. STRAINING - let it breathe (off-road) ---------------------
        # Sourced (advancedtransmission/mechaniquad): sustained high load at low
        # speed (climbing, crawling, towing) builds heat with little airflow;
        # take breaks to let it cool. Only fires in the STRAINING situation, so
        # it never nags during normal cruising.
        if sit == STRAINING:
            if self._fire("strain", True):
                warnings.append("STRAINING - high load at low speed, pause to let it cool if you can")
        else:
            self._fire("strain", False)

        # ---- 6. TURBO COOLDOWN after a hard pull --------------------------
        # Sourced (bddiesel/conflux): after a hard pull, idle briefly at light
        # load before shutoff so oil carries heat out of the turbo; avoid
        # hot-soak key-off. Fires when a recent hard pull is followed by idling.
        if sit == IDLING and self._hard_recent > 0:
            if self._fire("turbo_cool", True):
                warnings.append("TURBO HOT - idle a minute before shutoff to cool the turbo")
        else:
            self._fire("turbo_cool", False)

        return warnings
    def face_mood(self, warnings):
        """Map the current warnings to a face mood so the issue light and the
        warnings list are ONE system. Critical warnings -> red; any other
        warning -> yellow; none -> None (let normal mode color show)."""
        if not warnings:
            return None
        for w in warnings:
            # Critical-severity phrases get the red light; everything else yellow.
            if ("CRITICAL" in w or "KNOCK" in w or "LOW VOLTAGE" in w):
                return "red"
        return "yellow"

    def add_ml_warning(self, warnings, ml_message):
        """Hook for later: the ML model appends its own one-liner here so the
        issue face shows rule-based + learned warnings together."""
        if ml_message:
            warnings.append(ml_message)
        return warnings


def _num(v):
    """Snapshot values are numbers or '--'. Return a float or None."""
    return v if isinstance(v, (int, float)) else None