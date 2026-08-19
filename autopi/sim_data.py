import math
import random


class SimData:
    """Fake data provider for testing the UI on a laptop (no car).
    Produces believable changing values. On the Pi, a real VehicleData
    provider (backed by Vehicle) replaces this - same interface."""

    def __init__(self):
        self._t = 0
        self.mode = "highway"
        self.has_codes = False
        self.needs_gas = False
        self._coolant = 90
        self._voltage = 14.0

    def update(self):
        self._t += 1

    def snapshot(self):
        # Return the current car state as a dict (the UI reads this)
        self._t += 1
        return {
            "speed": round(60 + 15 * math.sin(self._t * 0.02) + random.uniform(-2, 2)),
            "boost": round(8 + 5 * math.sin(self._t * 0.05) + random.uniform(-0.8, 0.8), 1),
            "afr": round(14.7 + 0.5 * math.sin(self._t * 0.03), 1),
            "fuel": max(0, 72 - (self._t // 600)),
            "engine_load": round(45 + 20 * math.sin(self._t * 0.04)),
            "oil_temp": round(95 + 10 * math.sin(self._t * 0.015)),
            "rpm": round(2500 + 1200 * math.sin(self._t * 0.03)),
            "run_time": round(self._t / 60),   # minutes idling (sim)
            "coolant_temp": self._coolant,
            "voltage": self._voltage,
            "mode": self.mode,
            "has_codes": self.has_codes,
            "needs_gas": self.needs_gas,
        }

    def get_dyno_run(self):
        # Fake a dyno pull: (rpm, hp) pairs
        pairs = []
        for rpm in range(1500, 6500, 50):
            frac = rpm / 5600
            hp = 260 * (1.1 * frac - 0.35 * frac**2)
            hp = max(0, hp + random.uniform(-3, 3))
            pairs.append((rpm, hp))
        return pairs