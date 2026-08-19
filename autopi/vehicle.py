import obd
import time


class Reading:
    """A single snapshot of sensor values. Replaces the loose dict from
    build_reading() with a real object that knows how to convert itself."""

    def __init__(self, coolant_temp=None, intake_temp=None, boost_psi=None,
                 rpm=None, speed=None, engine_load=None, throttle=None,
                 voltage=None, timing=None, afr=None, fuel_level=None,
                 stft_b1=None, ltft_b1=None, o2_b1s2=None,
                 vin="TEST_OUTBACK"):
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

    def __init__(self, port="/dev/pts/2", vin="TEST_OUTBACK"):
        self.__port = port          # private
        self.vin = vin
        self.__connection = None    # private - the raw obd connection
        self.__last_reconnect = 0.0       # time of last reconnect attempt
        self.__reconnect_cooldown = 10.0   # was 5.0 - less frequent reconnect
        # Tiered polling: rotate through slow PIDs ONE per call so each
        # snapshot reads 4 fast + 1 slow = 5 queries, never an 11-query burst.
        self.__slow_cache = {}
        self.__slow_keys = ["intake_temp", "throttle", "voltage",
                            "timing", "fuel_level", "boost_psi", "afr",
                            "stft_b1", "ltft_b1", "o2_b1s2"]
        self.__slow_index = 0
    def connect(self):
        # Open the connection. fast=False + a short timeout are the documented
        # fixes for Raspberry Pi/ELM327 query hangs (python-OBD issue #149 and
        # the barracuda-fsh fork README). check_voltage=False stops false
        # disconnects when car power is electrically noisy. Short timeout means
        # a wedged query FAILS FAST (returns None) instead of freezing the UI.
        try:
            self.__connection = obd.OBD(self.__port, fast=False, timeout=1.0,
                                        check_voltage=False)
            return self.__connection.status()
        except Exception as err:
            self.__connection = None
            return "connect failed: " + str(err)

    def reconnect(self):
        # Re-establish after a drop, throttled so a flickering adapter can't
        # put us in a blocking reconnect loop. On a drop the OBDLink can
        # re-enumerate as a DIFFERENT port (e.g. ttyUSB0 -> ttyUSB1), so if the
        # known port fails we scan for wherever it came back. (obd.scan_serial,
        # per python-OBD docs, finds connected adapters.) This is the fix for
        # "drops on the highway and never comes back until restart."
        now = time.time()
        if now - self.__last_reconnect < self.__reconnect_cooldown:
            return False
        self.__last_reconnect = now
        try:
            if self.__connection is not None:
                self.__connection.close()
        except Exception:
            pass
        # Try the known port first.
        try:
            self.__connection = obd.OBD(self.__port, fast=False, timeout=1.0,
                                        check_voltage=False)
            if self.__connection.is_connected():
                return True
        except Exception:
            pass
        # Known port failed - the adapter may have re-enumerated. Scan for it.
        try:
            ports = obd.scan_serial()      # returns a list of adapter ports
            for port in ports:
                try:
                    self.__connection = obd.OBD(port, fast=False, timeout=1.0,
                                                check_voltage=False)
                    if self.__connection.is_connected():
                        self.__port = port     # remember the new port
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def read_value(self, command):
        # Query one command. If the connection is already known-dead, return
        # immediately (don't wait out the ~10s serial-port timeout on a dropped
        # adapter). On any failure, mark dead so subsequent reads skip fast.
        if self.__connection is None:
            return None
        try:
            response = self.__connection.query(command)
            if response is None or response.is_null():
                return None
            return response.value
        except Exception:
            return None

    def close(self):
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
        # Get trouble codes (list of (code, description) tuples), or empty
        return self.read_value(obd.commands.GET_DTC)
    
    def clear_dtcs(self):
        # Mode 04 - clears stored DTCs AND freeze-frame data. Per OBD-II spec,
        # permanent (Mode 0A) codes are NOT cleared by this; they clear only
        # after the monitor re-runs and passes. Returns True if the command sent.
        try:
            self.__connection.query(obd.commands.CLEAR_DTC)
            return True
        except Exception:
            return False

    def read_current(self):
        # If the adapter has dropped, don't attempt reads - each would block on
        # the serial timeout. Try a throttled reconnect and return empties;
        # the UI shows "--" and stays responsive until the adapter returns.
        if not self.is_connected():
            self.reconnect()
            return Reading(vin=self.vin)   # all None -> gauges show "--"
        def try_cmd(name):
            cmd = getattr(obd.commands, name, None)
            return self.read_number(cmd) if cmd is not None else None

        rpm = try_cmd("RPM")
        speed = try_cmd("SPEED")
        coolant = try_cmd("COOLANT_TEMP")
        load = try_cmd("ENGINE_LOAD")

        # SLOW tier - refresh only ONE slow PID per call, rotating through the
        # list. Each snapshot reads 4 fast + 1 slow = 5 queries max, never an
        # 11-query burst. Each slow PID refreshes every ~7 calls (~3.5s), which
        # is plenty for values that barely change.
        key = self.__slow_keys[self.__slow_index]
        self.__slow_index = (self.__slow_index + 1) % len(self.__slow_keys)
        if key == "boost_psi":
            self.__slow_cache["boost_psi"] = self.get_boost()
        elif key == "afr":
            self.__slow_cache["afr"] = self.get_afr()
        elif key == "intake_temp":
            self.__slow_cache["intake_temp"] = try_cmd("INTAKE_TEMP")
        elif key == "throttle":
            self.__slow_cache["throttle"] = try_cmd("THROTTLE_POS")
        elif key == "voltage":
            self.__slow_cache["voltage"] = try_cmd("CONTROL_MODULE_VOLTAGE")
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
        return Reading(
            coolant_temp=coolant,
            intake_temp=c.get("intake_temp"),
            boost_psi=c.get("boost_psi"),
            rpm=rpm,
            speed=speed,
            engine_load=load,
            throttle=c.get("throttle"),
            voltage=c.get("voltage"),
            timing=c.get("timing"),
            afr=c.get("afr"),
            fuel_level=c.get("fuel_level"),
            stft_b1=c.get("stft_b1"),
            ltft_b1=c.get("ltft_b1"),
            o2_b1s2=c.get("o2_b1s2"),
            vin=self.vin,
        )  

    def is_connected(self):
        try:
            return self.__connection is not None and self.__connection.is_connected()
        except Exception:
            return False