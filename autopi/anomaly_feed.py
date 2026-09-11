"""Bridges the live snapshot to the AnomalyDetector. The model was trained on
SQL-engineered features (5-row rolling averages + 10-row deltas); the live
snapshot only has instantaneous values. This keeps a small rolling buffer and
computes the SAME engineered features online, so inference matches training.

Usage in ui.py:
    from autopi.anomaly_feed import AnomalyFeed
    self._anomaly = AnomalyFeed()          # in __init__ (after other setup)
    ...
    # once per snapshot cycle (e.g. in update(), on face/menu with real data):
    result = self._anomaly.push(snap)      # snap = self._snapshotter.latest()
    # result["severity"] in {"ok","watch","elevated"}; result["culprits"]

O(1) per push. Safe if the model file is missing (returns severity 'ok',
available False) - never crashes the app."""
from collections import deque

try:
    from autopi.anomaly import AnomalyDetector
except Exception:
    try:
        from anomaly import AnomalyDetector      # local/testing import
    except Exception:
        AnomalyDetector = None


class AnomalyFeed:
    # Raw snapshot keys the model needs (before rolling features are derived).
    RAW = ["rpm", "speed", "coolant_temp", "engine_load", "throttle",
           "boost", "afr", "voltage", "timing", "stft_b1", "ltft_b1"]

    def __init__(self, model_path="models/anomaly_model.joblib"):
        self._det = AnomalyDetector(model_path) if AnomalyDetector else None
        self._buf = deque(maxlen=11)      # last 11 rows for avg5 + lag10
        self._last = {}                   # last seen value per key (forward-fill)

    def available(self):
        return self._det is not None and getattr(self._det, "ok", False)

    def _num(self, v):
        return v if isinstance(v, (int, float)) else None

    def push(self, snap):
        """Add one live snapshot, return the detector result. Only meaningful
        when the engine is running; caller should gate on rpm>0 if desired."""
        if not self.available():
            return {"available": False, "severity": "ok"}

        # Forward-fill sparse/tiered PIDs, exactly like features.sql LOCF.
        row = {}
        for k in self.RAW:
            v = self._num(snap.get(k))
            if v is None:
                v = self._last.get(k)     # carry last known
            else:
                self._last[k] = v
            row[k] = v
        # Need core values to proceed; if still missing, skip this cycle.
        if any(row[k] is None for k in ("rpm", "coolant_temp", "engine_load")):
            return {"available": True, "severity": self._det._severity}

        self._buf.append(row)

        # Rolling features (match features.sql): avg over last 5, delta vs 10 ago.
        def avg5(key):
            vals = [r[key] for r in list(self._buf)[-5:] if r[key] is not None]
            return sum(vals) / len(vals) if vals else row[key]

        def delta10(key):
            if len(self._buf) >= 11 and self._buf[0][key] is not None \
                    and row[key] is not None:
                return row[key] - self._buf[0][key]
            return 0.0

        feat = dict(row)
        feat["coolant_avg5"] = avg5("coolant_temp")
        feat["load_avg5"] = avg5("engine_load")
        feat["rpm_avg5"] = avg5("rpm")
        feat["coolant_d"] = delta10("coolant_temp")
        feat["voltage_d"] = delta10("voltage")
        feat["rpm_d"] = delta10("rpm")

        return self._det.score(feat)

    def reset(self):
        self._buf.clear(); self._last.clear()
        if self._det:
            self._det.reset()