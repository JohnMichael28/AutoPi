class OilTempMonitor:
    """Watches oil temperature IF the car exposes it (uses capability
    detection - adapts per car). Learns the car's normal operating range
    and flags when oil temp trends abnormally hot. Gracefully inactive on
    cars that don't report oil temp. Honest: many cars don't expose it."""

    def __init__(self, capabilities, hot_threshold=120, sample_window=30):
        self._caps = capabilities
        self._hot_threshold = hot_threshold   # absolute safety ceiling (C)
        self._window = sample_window
        self._readings = []          # recent oil temp samples
        self._active = False

    def check_availability(self):
        # Only activate if THIS car exposes oil temp (capability detection)
        self._active = self._caps.supports("OIL_TEMP")
        return self._active

    def is_active(self):
        return self._active

    def add_reading(self, oil_temp):
        # Feed a new oil temp sample (called each cycle when active)
        if not self._active or oil_temp is None:
            return
        self._readings.append(oil_temp)
        if len(self._readings) > self._window:
            self._readings.pop(0)   # rolling window

    def _baseline(self):
        # The car's "normal" - average of the earlier part of the window
        if len(self._readings) < 5:
            return None
        return sum(self._readings) / len(self._readings)

    def check(self, current_oil_temp):
        # Returns a warning dict, or None if all's well / inactive.
        if not self._active or current_oil_temp is None:
            return None

        # Signal 1: absolute safety ceiling (universal danger)
        if current_oil_temp >= self._hot_threshold:
            return {"level": "critical",
                    "message": "Oil temperature is critically high (" +
                               str(round(current_oil_temp)) + "C). "
                               "Ease off and let it cool."}

        # Signal 2: trending hot vs. this car's own normal (pattern-based)
        baseline = self._baseline()
        if baseline is not None and current_oil_temp > baseline + 15:
            return {"level": "warning",
                    "message": "Oil is running warmer than usual for your car. "
                               "Worth keeping an eye on."}
        return None