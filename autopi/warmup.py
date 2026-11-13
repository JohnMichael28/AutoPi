"""Cold-start warm-up guide: tells the driver WHEN it is safe to drive off.

Every modern engine runs a fast idle right after a cold start (the ECU holds
RPM up to heat the catalytic converter), then the idle drops on its own.
Sourced guidance (motor1 / AAA, autoblog, mechanicbase, consumerguide):
  - wait only until oil has circulated and the idle has dropped
    (about 15-30 s, up to ~60 s on a very cold morning)
  - then drive GENTLY - no high RPM, no full throttle - until the engine
    reaches operating temperature. Long idling does not help.

Phases (pure logic, no pygame, testable with a fake clock):
    off          engine not running
    fast_idle    just started cold, idle still high      -> WAIT       (blue)
    drive_gently idle settled, engine still cold         -> GO GENTLY  (blue)
    all_clear    coolant reached warm temp (shown briefly)
    warm         normal - nothing to show

"Warm" uses the same 70 C line as the ML model's cold_start regime
(regimes.py), so the screen and the anomaly model agree on what "cold" is.
All thresholds come from config.json "warmup".
"""
import time


OFF = "off"
FAST_IDLE = "fast_idle"
DRIVE_GENTLY = "drive_gently"
ALL_CLEAR = "all_clear"
WARM = "warm"


class WarmupMonitor:
    DEFAULTS = {
        "running_rpm": 400,       # below this the engine is off
        "settled_rpm": 1000,      # idle at/below this = fast idle is over
        "settle_hold_s": 3,       # ...and it has to stay there this long
        "min_wait_s": 20,         # oil circulation, even if idle drops fast
        "max_wait_s": 90,         # cold-weather idle can stay up; go anyway
        "warm_coolant_c": 70,     # matches regimes.COLD_START_COOLANT_C
        "gentle_rpm": 3500,       # "keep it under" while cold
        "gentle_throttle_pct": 60,
        "all_clear_show_s": 8,
        "moving_mph": 3,          # driver already rolling = past fast idle
    }

    def __init__(self, cfg=None, clock=None):
        merged = dict(self.DEFAULTS)
        if cfg:
            merged.update(cfg)
        self._c = merged
        self._clock = clock or time.monotonic
        self.reset()

    def reset(self):
        self.phase = OFF
        self._start = None
        self._settled_since = None
        self._clear_since = None
        self._start_rpm = None

    @staticmethod
    def _num(v):
        return v if isinstance(v, (int, float)) else None

    # ------------------------------------------------------------------
    def update(self, rpm=None, coolant=None, speed=None, throttle=None):
        """Feed one live reading. Returns status() for this cycle."""
        rpm = self._num(rpm)
        coolant = self._num(coolant)
        speed = self._num(speed)
        throttle = self._num(throttle)
        now = self._clock()
        c = self._c

        # A missing RPM is a dropped read, not an engine-off: keep state.
        if rpm is None:
            return self.status(rpm, coolant, throttle)

        if rpm < c["running_rpm"]:
            self.reset()
            return self.status(rpm, coolant, throttle)

        if self.phase == OFF:
            self._start = now
            self._start_rpm = rpm
            self._settled_since = None
            if coolant is not None and coolant >= c["warm_coolant_c"]:
                self.phase = WARM          # warm restart: nothing to wait for
            else:
                self.phase = FAST_IDLE

        if self.phase == FAST_IDLE:
            elapsed = now - self._start
            if rpm <= c["settled_rpm"]:
                if self._settled_since is None:
                    self._settled_since = now
            else:
                self._settled_since = None
            settled = (self._settled_since is not None
                       and now - self._settled_since >= c["settle_hold_s"])
            moving = speed is not None and speed >= c["moving_mph"]
            if (settled and elapsed >= c["min_wait_s"]) \
                    or elapsed >= c["max_wait_s"] or moving:
                self.phase = DRIVE_GENTLY

        if self.phase in (FAST_IDLE, DRIVE_GENTLY):
            if coolant is not None and coolant >= c["warm_coolant_c"]:
                self.phase = ALL_CLEAR
                self._clear_since = now

        if self.phase == ALL_CLEAR:
            if now - self._clear_since >= c["all_clear_show_s"]:
                self.phase = WARM

        return self.status(rpm, coolant, throttle)

    # ------------------------------------------------------------------
    def is_cold(self):
        return self.phase in (FAST_IDLE, DRIVE_GENTLY)

    def elapsed_s(self):
        if self._start is None:
            return 0
        return int(self._clock() - self._start)

    def status(self, rpm=None, coolant=None, throttle=None):
        """{phase, cold, headline, detail} - what the face shows."""
        c = self._c
        coolant_txt = (str(int(coolant)) + "C") if coolant is not None else "--"
        target = str(int(c["warm_coolant_c"])) + "C"
        out = {"phase": self.phase, "cold": self.is_cold(),
               "headline": "", "detail": ""}
        if self.phase == FAST_IDLE:
            secs = self.elapsed_s()
            clock = str(secs // 60) + ":" + str(secs % 60).zfill(2)
            rpm_txt = str(int(rpm)) if rpm is not None else "--"
            out["headline"] = "COLD START - WAIT, FAST IDLE " + rpm_txt + " RPM"
            out["detail"] = ("idle is settling (" + clock + "), oil circulating."
                             " Coolant " + coolant_txt)
        elif self.phase == DRIVE_GENTLY:
            too_hard = ((rpm is not None and rpm > c["gentle_rpm"])
                        or (throttle is not None
                            and throttle > c["gentle_throttle_pct"]))
            if too_hard:
                out["headline"] = "EASE OFF - ENGINE STILL COLD"
            else:
                out["headline"] = "SAFE TO DRIVE OFF - GO GENTLY"
            out["detail"] = ("Coolant " + coolant_txt + " -> " + target
                             + ": stay under " + str(int(c["gentle_rpm"]))
                             + " RPM, no full throttle")
        elif self.phase == ALL_CLEAR:
            out["headline"] = "ENGINE WARM - ALL CLEAR"
            out["detail"] = "Coolant " + coolant_txt + ". Drive normally."
        return out
