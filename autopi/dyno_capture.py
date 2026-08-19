"""Captures a dyno pull using the acceleration method. Power = Force x
velocity, Force = mass x acceleration. HP = TQ*RPM/5252 (sourced dyno
physics). ESTIMATE only - OBD speed is slow; accuracy improves later with a
hardware accelerometer.

IMPORTANT ARCHITECTURE: this does NOT query the OBD connection itself. It is
fed rpm + speed from the shared snapshot (the SnapshotThread is the single
reader). Multiple threads querying one OBD connection collide and break the
link (python-OBD / DuckDB thread-safety: only one query active per
connection at a time). So the dyno reads cached snapshot data - zero extra
OBD traffic, no collision."""
import time


class DynoCapture:
    def __init__(self, weight_lbs=4200, drivetrain_loss=0.15,
                 min_speed_mph=15, max_speed_mph=80):
        self._weight_lbs = weight_lbs
        self._drivetrain_loss = drivetrain_loss
        self._min_speed = min_speed_mph
        self._max_speed = max_speed_mph
        self._armed = False          # only capture when the user taps START
        self._in_pull = False
        self._current = []          # [(rpm, hp), ...] for the live pull
        self._last_run = []
        self._last_speed_mph = None
        self._last_time = None

    def arm(self):
        # User tapped START - ready to capture the next pull.
        self._armed = True
        self._in_pull = False
        self._current = []
        self._last_speed_mph = None
        self._last_time = None

    def disarm(self):
        # User tapped STOP - save the current pull if one is in progress.
        if self._in_pull and len(self._current) >= 5:
            self._last_run = list(self._current)
        self._armed = False
        self._in_pull = False
        self._current = []

    def is_armed(self):
        return self._armed

    def feed(self, rpm, speed_mph):
        # Only capture when armed. Ignores all acceleration otherwise.
        if not self._armed:
            return
        # Data dropped out (connection hiccup) - abort any pull in progress so
        # a gap can't corrupt the curve. Discard the partial pull.
        if rpm is None or speed_mph is None:
            if self._in_pull:
                self._in_pull = False
                self._current = []
            self._last_speed_mph = None
            self._last_time = None
            return
        now = time.time()
        if self._last_speed_mph is not None and self._last_time is not None:
            dt = now - self._last_time
            # Big time gap = the connection stalled mid-pull. Discard it - the
            # acceleration math across a gap produces a garbage data point.
            if dt > 1.5:
                if self._in_pull:
                    self._in_pull = False
                    self._current = []
                self._last_speed_mph = speed_mph
                self._last_time = now
                return
            if dt > 0:
                accel_ms2 = ((speed_mph - self._last_speed_mph) * 0.44704) / dt
                if accel_ms2 > 1.0 and not self._in_pull:
                    self._in_pull = True
                    self._current = []
                if self._in_pull:
                    if accel_ms2 > 0.2:
                        hp = self._power_hp(accel_ms2, speed_mph)
                        if hp > 0:
                            self._current.append((round(rpm), round(hp)))
                    else:
                        if len(self._current) >= 5:
                            self._last_run = list(self._current)
                        self._in_pull = False
                        self._current = []
        self._last_speed_mph = speed_mph
        self._last_time = now

    def _power_hp(self, accel_ms2, speed_mph):
        mass_kg = self._weight_lbs * 0.453592
        velocity_ms = speed_mph * 0.44704
        force_n = mass_kg * accel_ms2
        power_watts = force_n * velocity_ms
        power_hp = power_watts / 745.7
        return power_hp / (1.0 - self._drivetrain_loss)

    def get_last_run(self):
        return list(self._last_run)

    def is_pulling(self):
        return self._in_pull

    def stop(self):
        pass    # no thread to stop anymore