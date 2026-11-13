"""Automatic screen/mode selection for AutoPi.

Picks which screen the guardian shows from live data, with no tapping:

    camp       - engine running, car stopped        -> purple idle/camp screen
    highway    - normal driving                     -> green smile
    track      - driving hard / fast                -> track screen
    adventure  - off-road (X-Mode / 4x4 / heuristic) -> orange adventure screen

Plus AutoDyno: every hard pull is captured automatically and kept with a
timestamp, so tapping the screen shows the LAST pull and when it happened.
(Its thresholds live in config.json "dyno".)

Design notes
- Pure logic (no OBD, no pygame) so it is testable on a laptop.
- Uses only universal Mode 01 PIDs (speed, rpm, throttle, load). Off-road
  detection uses an optional vehicle-specific flag (e.g. Subaru X-Mode via a
  Mode 22 DID once discovered) and falls back to a heuristic.
- Hysteresis everywhere: a mode must hold for `*_enter_s` seconds before we
  switch in, and we only leave when the exit threshold (lower than the enter
  threshold) holds for `*_exit_s`. Stops the face flickering at 4.9 / 5.1 mph.
- Priority when several match: adventure > track > camp (idle) > highway.
- Mode names match the rest of AutoPi (FaceState, MODE_STATS): highway,
  track, adventure, camp.
- All thresholds come from config.json ("drive_mode" block). Units are mph
  and percent, matching DynoCapture.
"""
import time


DEFAULTS = {
    "idle_max_mph": 2.0,          # at or below this = stopped
    "idle_enter_s": 3.0,          # must be stopped this long to show idle
    "moving_exit_mph": 5.0,       # above this = definitely moving again

    "track_enter_mph": 75.0,      # sustained speed that means "going fast"
    "track_exit_mph": 65.0,
    "track_throttle_pct": 70.0,   # OR hard throttle...
    "track_rpm": 4500.0,          # ...at high rpm counts as track
    "track_enter_s": 2.0,
    "track_exit_s": 8.0,          # stay on track screen through brief lifts

    "offroad_max_mph": 25.0,      # heuristic: slow...
    "offroad_load_pct": 55.0,     # ...but working hard
    "offroad_enter_s": 10.0,
    "offroad_exit_s": 15.0,
}


class DriveModeSelector:
    def __init__(self, cfg=None, clock=None):
        self._cfg = dict(DEFAULTS)
        if cfg:
            self._cfg.update(cfg)
        self._clock = clock or time.time
        self._mode = "highway"
        self._pending = None          # candidate mode being timed
        self._pending_since = None
        self._exit_since = None       # when the current mode's exit test started passing

    @property
    def mode(self):
        return self._mode

    def _num(self, v):
        return v if isinstance(v, (int, float)) else None

    # ---- raw (instantaneous) tests, no timing ----

    def _wants_idle(self, speed, rpm):
        c = self._cfg
        return (rpm is not None and rpm > 0
                and speed is not None and speed <= c["idle_max_mph"])

    def _wants_track(self, speed, rpm, throttle):
        c = self._cfg
        fast = speed is not None and speed >= c["track_enter_mph"]
        hard = (throttle is not None and rpm is not None
                and throttle >= c["track_throttle_pct"]
                and rpm >= c["track_rpm"])
        return fast or hard

    def _still_track(self, speed, throttle):
        c = self._cfg
        if speed is not None and speed >= c["track_exit_mph"]:
            return True
        return throttle is not None and throttle >= c["track_throttle_pct"]

    def _wants_offroad(self, speed, load, offroad_flag):
        if offroad_flag is True:            # vehicle told us (X-Mode, 4WD low)
            return True
        if offroad_flag is False:           # vehicle explicitly says no
            return False
        c = self._cfg                       # None = unknown -> heuristic
        return (speed is not None and load is not None
                and 3.0 < speed <= c["offroad_max_mph"]
                and load >= c["offroad_load_pct"])

    # ---- main entry point, call once per snapshot ----

    def update(self, speed=None, rpm=None, throttle=None, load=None,
               offroad_flag=None):
        """Feed one snapshot. Returns the (possibly unchanged) mode string.
        offroad_flag: True/False if the car reports it, None if unknown."""
        speed = self._num(speed)
        rpm = self._num(rpm)
        throttle = self._num(throttle)
        load = self._num(load)
        now = self._clock()
        c = self._cfg

        # 1. Is the CURRENT mode still valid? (exit hysteresis)
        if self._mode == "track":
            if self._still_track(speed, throttle):
                self._exit_since = None
            else:
                if self._exit_since is None:
                    self._exit_since = now
                if now - self._exit_since < c["track_exit_s"]:
                    return self._mode          # hold through brief lifts
        elif self._mode == "adventure":
            if self._wants_offroad(speed, load, offroad_flag) or (
                    offroad_flag is None and speed is not None
                    and speed <= c["offroad_max_mph"]):
                self._exit_since = None
            else:
                if self._exit_since is None:
                    self._exit_since = now
                if now - self._exit_since < c["offroad_exit_s"]:
                    return self._mode
        elif self._mode == "camp":
            if speed is not None and speed < c["moving_exit_mph"]:
                return self._mode              # creeping in traffic stays idle

        # 2. What SHOULD we be? Priority order.
        if self._wants_offroad(speed, load, offroad_flag):
            target, hold = "adventure", c["offroad_enter_s"]
            if offroad_flag is True:
                hold = 0.0                     # trust the car's own flag now
        elif self._wants_track(speed, rpm, throttle):
            target, hold = "track", c["track_enter_s"]
        elif self._wants_idle(speed, rpm):
            target, hold = "camp", c["idle_enter_s"]
        else:
            target, hold = "highway", 0.0

        # 3. Entry hysteresis: target must hold for `hold` seconds.
        if target == self._mode:
            self._pending = None
            return self._mode
        if self._pending != target:
            self._pending = target
            self._pending_since = now
        if now - self._pending_since >= hold:
            self._mode = target
            self._pending = None
            self._exit_since = None
        return self._mode


class AutoDyno:
    """Automatic dyno: watches every drive and records each hard pull by
    itself - no START button, nothing to arm.

    Why it was rebuilt (Nov 2026): the old version decided "pull over" the
    moment two snapshots in a row had the same speed. Once live data got
    fast (snapshots every ~0.15 s, speed in whole km/h), almost every pull
    had repeated speed samples, so pulls were thrown away within a second
    and nothing was ever saved.

    Now:
      - acceleration = slope of a least-squares line through the last
        ~1.2 s of DISTINCT speed samples (time-stamped by the fast lane),
        so 1 km/h steps and repeated samples don't matter;
      - a pull starts on (throttle >= pull_throttle_pct OR load >=
        pull_load_pct) while accelerating, and only ends after a short
        grace period (CVT step-shifts and brief dips don't cut it off);
      - power includes aero drag + rolling resistance (road-load), not just
        mass x acceleration, so high-speed points aren't under-read;
      - points are averaged into RPM bins for a clean curve.
    Still an ESTIMATE (OBD speed is coarse) - the screen says so.

    feed(rpm, speed_mph, throttle, load=None, speed_t=None) every snapshot.
    last() -> {"time", "points": [(rpm, hp)], "peak_hp", "peak_rpm",
               "duration_s", "start_mph", "end_mph"} or None
    """

    DEFAULTS = {
        "vehicle_weight_lbs": 4200,   # car + driver
        "drivetrain_loss": 0.15,      # wheel -> crank estimate
        "min_pull_speed_mph": 10,
        "max_pull_speed_mph": 90,
        "pull_throttle_pct": 60.0,    # pedal signal...
        "pull_load_pct": 85.0,        # ...or calculated load (turbo WOT)
        "start_accel_ms2": 0.8,       # must really be accelerating to start
        "keep_accel_ms2": 0.2,        # still accelerating = still pulling
        "end_grace_s": 0.8,           # tolerate shifts / brief dips
        "min_pull_s": 2.0,            # shorter = not a pull, discard
        "min_points": 5,
        "accel_window_s": 1.2,
        "cda_m2": 0.86,               # drag area (Cd ~0.33 x ~2.6 m2)
        "crr": 0.012,                 # rolling resistance, road tires
        "rpm_bin": 100,
        "keep": 10,                   # pulls kept in history
    }

    def __init__(self, cfg=None, clock=None):
        merged = dict(self.DEFAULTS)
        if cfg:
            for k in merged:
                if k in cfg:
                    merged[k] = cfg[k]
        self._c = merged
        # monotonic, same clock as the fast lane's sample timestamps
        self._clock = clock or time.monotonic
        self._samples = []        # (t, speed_mph) distinct samples
        self._history = []        # newest last
        self._pulling = False
        self._points = []         # (rpm, hp) during the current pull
        self._start_t = None
        self._last_good = None
        self._start_mph = None
        self._last_mph = None

    @staticmethod
    def _num(v):
        return v if isinstance(v, (int, float)) else None

    def _accel(self, now):
        """m/s^2 from a least-squares slope over the recent window."""
        win = self._c["accel_window_s"]
        pts = [(t, v) for t, v in self._samples if now - t <= win + 0.3]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.35:
            return None
        n = float(len(pts))
        mt = sum(p[0] for p in pts) / n
        mv = sum(p[1] for p in pts) / n
        den = sum((p[0] - mt) ** 2 for p in pts)
        if den <= 0:
            return None
        slope_mph_s = sum((p[0] - mt) * (p[1] - mv) for p in pts) / den
        return slope_mph_s * 0.44704

    def _power_hp(self, accel_ms2, speed_mph):
        c = self._c
        mass = c["vehicle_weight_lbs"] * 0.453592
        v = speed_mph * 0.44704
        force = mass * accel_ms2 + 0.5 * 1.2 * c["cda_m2"] * v * v + c["crr"] * mass * 9.81
        wheel_hp = force * v / 745.7
        return wheel_hp / (1.0 - c["drivetrain_loss"])

    def feed(self, rpm, speed_mph, throttle, load=None, speed_t=None):
        c = self._c
        now = self._clock()
        rpm = self._num(rpm)
        speed = self._num(speed_mph)
        throttle = self._num(throttle)
        load = self._num(load)
        t = speed_t if isinstance(speed_t, (int, float)) else now
        if speed is not None:
            if (not self._samples or (t > self._samples[-1][0]
                    and (speed != self._samples[-1][1]
                         or t - self._samples[-1][0] >= 0.7))):
                self._samples.append((t, speed))
            keep_from = now - 3 * c["accel_window_s"]
            while self._samples and self._samples[0][0] < keep_from:
                self._samples.pop(0)
        accel = self._accel(t)
        pedal = ((throttle is not None and throttle >= c["pull_throttle_pct"])
                 or (load is not None and load >= c["pull_load_pct"]))
        in_range = (speed is not None
                    and c["min_pull_speed_mph"] <= speed <= c["max_pull_speed_mph"])

        if not self._pulling:
            if pedal and in_range and accel is not None and accel >= c["start_accel_ms2"]:
                self._pulling = True
                self._points = []
                self._start_t = now
                self._last_good = now
                self._start_mph = speed
        if self._pulling:
            if pedal and in_range and accel is not None and accel >= c["keep_accel_ms2"]:
                self._last_good = now
                self._last_mph = speed
                if rpm is not None and rpm > 0:
                    hp = self._power_hp(accel, speed)
                    if hp > 0:
                        self._points.append((rpm, hp))
            elif now - self._last_good > c["end_grace_s"]:
                self._finish()

    def _finish(self):
        c = self._c
        duration = (self._last_good or 0) - (self._start_t or 0)
        pts = self._points
        self._pulling = False
        self._points = []
        if duration < c["min_pull_s"] or len(pts) < c["min_points"]:
            return
        bins = {}
        for rpm, hp in pts:
            b = int(round(rpm / c["rpm_bin"])) * c["rpm_bin"]
            bins.setdefault(b, []).append(hp)
        curve = sorted((b, round(sum(v) / len(v))) for b, v in bins.items())
        peak_rpm, peak_hp = max(curve, key=lambda p: p[1])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._history.append({"time": stamp, "points": curve,
                              "peak_hp": peak_hp, "peak_rpm": peak_rpm,
                              "duration_s": round(duration, 1),
                              "start_mph": self._start_mph,
                              "end_mph": self._last_mph})
        if len(self._history) > c["keep"]:
            self._history = self._history[-c["keep"]:]

    def last(self):
        return self._history[-1] if self._history else None

    def history(self):
        return list(self._history)

    def is_pulling(self):
        return self._pulling

    def status(self):
        c = self._c
        if self._pulling:
            secs = (self._clock() - self._start_t) if self._start_t else 0
            return "RECORDING PULL... " + str(round(secs, 1)) + " s"
        return ("AUTO: " + str(int(c["pull_throttle_pct"])) + "%+ throttle, "
                + str(int(c["min_pull_speed_mph"])) + "-" + str(int(c["max_pull_speed_mph"]))
                + " mph - records by itself")
