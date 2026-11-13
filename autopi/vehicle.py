import obd
import time


class Reading:
    """A single snapshot of sensor values. Replaces the loose dict from
    build_reading() with a real object that knows how to convert itself."""

    def __init__(self, coolant_temp=None, intake_temp=None, boost_psi=None,
                 rpm=None, speed=None, engine_load=None, throttle=None,
                 voltage=None, timing=None, afr=None, fuel_level=None,
                 stft_b1=None, ltft_b1=None, o2_b1s2=None,
                 run_time=None, vin="TEST_OUTBACK"):
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.vin = vin
        self.coolant_temp = coolant_temp
        self.intake_temp = intake_temp
        self.boost_psi = boost_psi
        self.rpm = rpm
        self.speed = speed
        self.engine_load = engine_load
        self.throttle = throttle
        self.voltage = voltage
        self.timing = timing
        self.afr = afr
        self.fuel_level = fuel_level
        self.stft_b1 = stft_b1
        self.ltft_b1 = ltft_b1
        self.o2_b1s2 = o2_b1s2
        self.run_time = run_time          # engine run time, seconds
        

    def to_dict(self):
        # For the database logger (matches the readings table columns)
        return {
            "timestamp": self.timestamp,
            "vin": self.vin,
            "coolant_temp": self.coolant_temp,
            "intake_temp": self.intake_temp,
            "boost_psi": self.boost_psi,
            "rpm": self.rpm,
            "speed": self.speed,
            "engine_load": self.engine_load,
            "throttle": self.throttle,
            "voltage": self.voltage,
        }

    def to_feature_list(self):
        # For the ML model - MUST match ml FEATURES order:
        # coolant, intake, boost, rpm, speed, load, throttle, voltage
        return [self.coolant_temp, self.intake_temp, self.boost_psi,
                self.rpm, self.speed, self.engine_load,
                self.throttle, self.voltage]

    def is_complete(self):
        # True only if every value is present (for ML - no None allowed)
        return None not in self.to_feature_list()


class Event:
    """A trouble code event: the code, its description, AI explanation,
    and eventual fix outcome. Replaces raw event tuples."""

    def __init__(self, code, description, explanation="", outcome="",
                 vin="TEST_OUTBACK"):
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.vin = vin
        self.code = code
        self.description = description
        self.explanation = explanation
        self.outcome = outcome
class Vehicle:
    """Encapsulates the OBD-II connection to the car. The rest of the app
    talks to the car ONLY through this class - the raw obd connection is
    private and hidden (encapsulation / information hiding)."""

    # FAST LANE: these PIDs are read on their own thread as fast as the
    # adapter answers (RPM gets every other slot), so the screen follows
    # the throttle instead of lagging ~2 s behind it.
    FAST_SEQUENCE = ["RPM", "THROTTLE_POS", "RPM", "SPEED"]

    def __init__(self, port="/dev/pts/2", vin="TEST_OUTBACK", fast=False,
                 timeout=1.0):
        self.__port = port          # private
        self.__fast_mode = bool(fast)    # python-OBD fast mode (see connect)
        self.__timeout = timeout
        self.vin = vin
        self.__connection = None    # private - the raw obd connection
        self.__last_reconnect = 0.0       # time of last reconnect attempt
        self.__reconnect_cooldown = 5.0   # was 5.0 - less frequent reconnect
        # Tiered polling: rotate through slow PIDs ONE per call so each
        # snapshot reads 4 fast + 1 slow = 5 queries, never an 11-query burst.
        self.__slow_cache = {}
        # Throttle lives in the FAST tier (read every cycle): auto track mode
        # and AutoDyno need it fresh, not ~7 s stale from the rotation.
        self.__slow_keys = ["intake_temp", "timing", "fuel_level",
                            "boost_psi", "afr", "stft_b1", "ltft_b1",
                            "o2_b1s2", "run_time", "boost_psi"]
        self.__slow_index = 0
        self.__dead_reads = 0     # consecutive all-failed read cycles
        import threading
        self.__lock = threading.Lock()   # serialize OBD access (not thread-safe)
        self.__fast = {}                 # name -> (value, monotonic time)
        self.__fast_thread = None
        self.__fast_running = False
        self.__fast_gap = 0.005
        self.__priority = 0              # >0 = a diagnostic read is running
        self.__priority_lock = threading.Lock()
        self.__rpm_times = []            # for the measured RPM refresh rate
        self.__last_reading = None
    def connect(self):
        # Open the connection. fast=False + a short timeout are the documented
        # fixes for Raspberry Pi/ELM327 query hangs (python-OBD issue #149 and
        # the barracuda-fsh fork README). check_voltage=False stops false
        # disconnects when car power is electrically noisy. Short timeout means
        # a wedged query FAILS FAST (returns None) instead of freezing the UI.
        import time as _t
        for attempt in range(5):
            try:
                self.__connection = obd.OBD(self.__port, fast=self.__fast_mode,
                                            timeout=self.__timeout,
                                            check_voltage=False)
                if self.__connection.is_connected():
                    return self.__connection.status()
            except Exception:
                pass
            _t.sleep(1.0)      # wait a second, try again
        return "connect failed after retries"

    def reconnect(self):
        # Re-establish after a drop. Post-drop, the ELM327 can be in a wedged
        # state, and python-OBD's init handshake is known to desync (issues
        # #226/#159: "ATH1/ATE0 did not return OK"). A clean close + a brief
        # settle before reopening gives the adapter time to recover, which
        # makes reconnects far more reliable. Throttled so a flickering adapter
        # can't loop-block. The OBDLink re-enumerates on the same port, so no
        # port scan is needed (it only added multi-minute blocking on failure).
        now = time.time()
        if now - self.__last_reconnect < self.__reconnect_cooldown:
            return False
        self.__last_reconnect = now
        try:
            if self.__connection is not None:
                self.__connection.close()
        except Exception:
            pass
        time.sleep(1.0)     # let the adapter settle after close before reopening
        try:
            self.__connection = obd.OBD(self.__port, fast=self.__fast_mode,
                                        timeout=self.__timeout,
                                        check_voltage=False)
            if self.__connection.is_connected():
                return True
        except Exception:
            pass
        return False

    def read_value(self, command):
        # Query one command, serialized with a lock because python-OBD is not
        # thread-safe: the background snapshot thread and any main-thread
        # diagnostic read must take turns on the single serial port, or they
        # collide and cause "Device disconnected while reading".
        if self.__connection is None:
            return None
        # Skip PIDs this car doesn't support. Without this, python-OBD logs
        # "'b'010F': Intake Air Temp' is not supported" on EVERY poll, which
        # floods the console and wastes a serial round-trip. The supported
        # list comes from the car's own Mode 01 PID bitmaps at connect time.
        try:
            if not self.__connection.supports(command):
                return None
        except Exception:
            pass
        with self.__lock:
            try:
                response = self.__connection.query(command)
                if response is None or response.is_null():
                    return None
                return response.value
            except Exception:
                return None

    # ---- fast lane --------------------------------------------------------
    def start_fast_lane(self, gap_s=0.005):
        """Start the RPM/throttle/speed thread. gap_s is a tiny pause after
        every read so the snapshot thread and diagnostics get their turn on
        the serial port (the lock is not fair on its own)."""
        import threading
        if self.__fast_thread is not None:
            return
        self.__fast_gap = gap_s
        self.__fast_running = True
        self.__fast_thread = threading.Thread(target=self.__fast_loop, daemon=True)
        self.__fast_thread.start()

    def stop_fast_lane(self):
        self.__fast_running = False

    def fast_lane_running(self):
        return self.__fast_running and self.__fast_thread is not None

    def __fast_loop(self):
        index = 0
        while self.__fast_running:
            if self.__priority > 0 or self.__connection is None:
                time.sleep(0.05)          # a diagnostic read has the port
                continue
            name = self.FAST_SEQUENCE[index % len(self.FAST_SEQUENCE)]
            index += 1
            cmd = getattr(obd.commands, name, None)
            value = self.read_number(cmd) if cmd is not None else None
            now = time.monotonic()
            if value is not None:
                self.__fast[name] = (value, now)
                if name == "RPM":
                    self.__rpm_times.append(now)
                    if len(self.__rpm_times) > 40:
                        self.__rpm_times = self.__rpm_times[-40:]
            time.sleep(self.__fast_gap)

    def fast_values(self, max_age_s=2.0):
        """{"RPM": v, "THROTTLE_POS": v, "SPEED": v (km/h)} - None if stale."""
        now = time.monotonic()
        out = {}
        for name in ("RPM", "THROTTLE_POS", "SPEED"):
            item = self.__fast.get(name)
            out[name] = item[0] if item and now - item[1] <= max_age_s else None
        return out

    def fast_sample(self, name):
        """(value, monotonic time it was read) or (None, None)."""
        item = self.__fast.get(name)
        if not item:
            return None, None
        return item

    def rpm_rate_hz(self):
        """Measured RPM refreshes per second over the last ~40 reads."""
        t = self.__rpm_times
        if len(t) < 2 or time.monotonic() - t[-1] > 2.0:
            return 0.0
        return round((len(t) - 1) / (t[-1] - t[0]), 1) if t[-1] > t[0] else 0.0

    def priority(self):
        """with vehicle.priority(): ...  - pauses the fast lane so a
        diagnostic read (codes, readiness, Mode 06) gets the port right away
        instead of queueing behind live-data reads."""
        import contextlib

        @contextlib.contextmanager
        def _hold():
            with self.__priority_lock:
                self.__priority += 1
            try:
                yield self
            finally:
                with self.__priority_lock:
                    self.__priority -= 1
        return _hold()

    def close(self):
        self.stop_fast_lane()
        if self.__connection is not None:
            self.__connection.close()

    def status(self):
        if self.__connection is None:
            return "not connected"
        return self.__connection.status()

    def __to_number(self, value):
        # Private helper: strip pint units to a plain number, or None
        if value is None:
            return None
        try:
            return value.magnitude
        except AttributeError:
            return value


    def read_number(self, command):
        # Query one command, return a plain number (units stripped)
        return self.__to_number(self.read_value(command))

    def get_boost(self):
        # Boost in PSI = (manifold pressure - barometric) converted, or None
        manifold = self.read_value(obd.commands.INTAKE_PRESSURE)
        baro = self.read_value(obd.commands.BAROMETRIC_PRESSURE)
        if manifold is not None and baro is not None:
            boost_kpa = manifold.magnitude - baro.magnitude
            return round(boost_kpa * 0.145038, 1)
        return None
    
    def get_afr(self):
        cmd = getattr(obd.commands, "COMMANDED_EQUIV_RATIO", None)
        if cmd is None:
            return None
        lam = self.read_number(cmd)
        if lam is not None and lam > 0:
            return round(14.7 * lam, 1)
        return None
    
    def get_fuel_trims(self):
        # Short & long term fuel trim, banks 1 & 2. THE key diagnostic PIDs
        # (all 6 researched sources rank these #1). Normal +/-10%; beyond
        # +/-15% signals vacuum leak, bad O2, injectors, or exhaust leak.
        # 4-cyl (like the XT5 2.0T) has only bank 1; bank 2 reads None.
        def trim(name):
            cmd = getattr(obd.commands, name, None)
            return self.read_number(cmd) if cmd is not None else None
        return {
            "stft_b1": trim("SHORT_FUEL_TRIM_1"),
            "ltft_b1": trim("LONG_FUEL_TRIM_1"),
            "stft_b2": trim("SHORT_FUEL_TRIM_2"),
            "ltft_b2": trim("LONG_FUEL_TRIM_2"),
        }

    def get_o2_voltage(self):
        # O2 sensor voltage (bank 1 sensor 2). Healthy cycles 0.1-0.9V.
        cmd = getattr(obd.commands, "O2_B1S2", None)
        return self.read_number(cmd) if cmd is not None else None

    def get_dtcs(self):
        # Get stored/confirmed trouble codes (Mode 03) - the ones that light
        # the CEL. Retries because a flaky ELM327 read can return null on one
        # query even with the light on, which is indistinguishable from "no
        # codes". If EVERY attempt is null, raise so the UI says "read failed"
        # instead of lying "engine clean". An empty list is a real "no codes".
        if self.__connection is None:
            raise IOError("no connection")
        with self.__lock:
            for attempt in range(3):
                try:
                    response = self.__connection.query(obd.commands.GET_DTC)
                except Exception:
                    response = None
                if response is not None and not response.is_null():
                    return response.value or []
                time.sleep(0.3)
        raise IOError("DTC read returned null on every attempt")
    
    def clear_dtcs(self):
        # Mode 04 - clears stored DTCs AND freeze-frame data. Locked so it can't
        # collide with the snapshot thread on the serial port.
        if self.__connection is None:
            return False
        with self.__lock:
            try:
                self.__connection.query(obd.commands.CLEAR_DTC)
                return True
            except Exception:
                return False

    def read_current(self):
        # is_connected() is known to report True even when the car connection is
        # dead (python-OBD issues #70 and #205: "connected" status but every
        # read returns None). So we do NOT trust it alone - we also count
        # consecutive failed core reads and force a reconnect after 3, which is
        # what actually detects a real drop.
        if not self.is_connected():
            self.reconnect()
            return Reading(vin=self.vin)

        def try_cmd(name):
            cmd = getattr(obd.commands, name, None)
            return self.read_number(cmd) if cmd is not None else None

        # A diagnostic read has priority: reuse the last reading instead of
        # queueing more live-data requests in front of it.
        if self.__priority > 0 and self.__last_reading is not None:
            return self.__last_reading

        if self.fast_lane_running():
            # RPM/throttle/speed come from the fast lane (already fresh);
            # this cycle only spends serial time on the slower values.
            fast = self.fast_values()
            rpm = fast.get("RPM")
            speed = fast.get("SPEED")
            throttle = fast.get("THROTTLE_POS")
        else:
            rpm = try_cmd("RPM")
            speed = try_cmd("SPEED")
            throttle = try_cmd("THROTTLE_POS")
        coolant = try_cmd("COOLANT_TEMP")
        load = try_cmd("ENGINE_LOAD")

        # DEAD-CONNECTION DETECTION: if the core reads all fail, the connection
        # is really dead even though is_connected() may still claim otherwise.
        # After 3 consecutive all-fail cycles, force a reconnect.
        if rpm is None and speed is None and coolant is None and load is None:
            self.__dead_reads += 1
            if self.__dead_reads >= 3:
                self.__dead_reads = 0
                self.reconnect()
            return Reading(vin=self.vin)
        else:
            self.__dead_reads = 0

        # ... (the rest of your slow-tier code and Reading return stays the same)
        key = self.__slow_keys[self.__slow_index]
        self.__slow_index = (self.__slow_index + 1) % len(self.__slow_keys)
        if key == "boost_psi":
            # This car doesn't expose manifold pressure (PID 0x0B), so true
            # boost (MAP - baro) can't be derived. MAF (mass air flow, g/s) is
            # a supported MEASURED value and the best turbo-activity indicator:
            # airflow spikes when the turbo spools. Real data, not an estimate.
            self.__slow_cache["boost_psi"] = try_cmd("MAF")
        elif key == "afr":
            self.__slow_cache["afr"] = self.get_afr()
        elif key == "intake_temp":
            self.__slow_cache["intake_temp"] = try_cmd("INTAKE_TEMP")
        elif key == "run_time":
            self.__slow_cache["run_time"] = try_cmd("RUN_TIME")
        elif key == "timing":
            self.__slow_cache["timing"] = try_cmd("TIMING_ADVANCE")
        elif key == "fuel_level":
            self.__slow_cache["fuel_level"] = try_cmd("FUEL_LEVEL")
        elif key == "stft_b1":
            cmd = getattr(obd.commands, "SHORT_FUEL_TRIM_1", None)
            self.__slow_cache["stft_b1"] = self.read_number(cmd) if cmd else None
        elif key == "ltft_b1":
            cmd = getattr(obd.commands, "LONG_FUEL_TRIM_1", None)
            self.__slow_cache["ltft_b1"] = self.read_number(cmd) if cmd else None
        elif key == "o2_b1s2":
            cmd = getattr(obd.commands, "O2_B1S2", None)
            self.__slow_cache["o2_b1s2"] = self.read_number(cmd) if cmd else None
        c = self.__slow_cache
        self.__last_reading = Reading(
            coolant_temp=coolant,
            intake_temp=c.get("intake_temp"),
            boost_psi=c.get("boost_psi"),
            rpm=rpm,
            speed=speed,
            engine_load=load,
            throttle=throttle,
            voltage=c.get("voltage"),
            timing=c.get("timing"),
            afr=c.get("afr"),
            fuel_level=c.get("fuel_level"),
            stft_b1=c.get("stft_b1"),
            ltft_b1=c.get("ltft_b1"),
            o2_b1s2=c.get("o2_b1s2"),
            run_time=c.get("run_time"),
            vin=self.vin,
        )
        return self.__last_reading  

    def is_connected(self):
        try:
            return self.__connection is not None and self.__connection.is_connected()
        except Exception:
            return False