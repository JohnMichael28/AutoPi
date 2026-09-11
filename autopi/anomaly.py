"""Phase 3: on-Pi inference. Load the trained bundle and score live snapshots.
LIGHT - a scored sample is microseconds, safe every cycle on the Pi 4.

TWO-LAYER DETECTION (the 'harness around a weak model' idea, applied to ML):
  Layer A - Isolation Forest: catches weird COMBINATIONS of sensors (the thing
            rules can't see). Good at joint anomalies, weak on a single sensor.
  Layer B - Per-feature z-guard: if any one sensor is too many standard
            deviations from its learned normal, flag it regardless of the
            forest. Catches single-sensor faults the forest dilutes across 17
            dims (overheat, charging fault, lean/rich trims - all missed by the
            forest alone in validation).

WHY PER-FEATURE BARS (not one global sigma): real driving is naturally spiky on
some channels - a hard launch throws rpm_d/rpm/load several sigma every time,
which is NORMAL. Coolant, voltage, fuel trims, timing and AFR are stable, so any
big deviation there is a real problem. So noisy driving-dynamics features get a
HIGH bar; stable diagnostic features get a LOW bar. This is what keeps the
false-positive rate low while still catching genuine single-sensor faults.

The rule-based WarningAdvisor remains the real-time SAFETY layer; this is the
'unknown / unusual' layer that complements it."""
import os

try:
    import joblib
    import numpy as np
except Exception:
    joblib = None
    np = None

# Stable diagnostic features: any sizeable deviation here is meaningful.
STABLE_FEATURES = {"coolant_temp", "coolant_avg5", "coolant_d",
                   "voltage", "voltage_d", "afr",
                   "stft_b1", "ltft_b1", "timing"}
Z_STABLE = 4.0     # bar for stable features
Z_NOISY = 6.0      # bar for spiky driving-dynamics features (rpm, load, speed...)
Z_NAME = 2.5       # features past this sigma are named as culprits

# Persistence -> severity (readings at ~0.75s each).
WATCH_AFTER = 1       # 1 anomalous reading -> "watch"
ELEVATED_AFTER = 5    # ~4s sustained     -> "elevated"
DECAY = 2             # clean readings needed to step severity back down


class AnomalyDetector:
    def __init__(self, path="models/anomaly_model.joblib"):
        self.ok = False
        self._bundle = None
        self._streak = 0          # consecutive anomalous readings
        self._clean = 0           # consecutive clean readings
        self._severity = "ok"
        if joblib is None or not os.path.exists(path):
            return
        try:
            self._bundle = joblib.load(path)
            self.features = self._bundle["features"]
            self.scaler = self._bundle["scaler"]
            self.model = self._bundle["model"]
            self.baselines = self._bundle["baselines"]
            self.threshold = self._bundle["threshold"]
            self.ok = True
        except Exception:
            self.ok = False
 
    def _bar(self, feature):
        return Z_STABLE if feature in STABLE_FEATURES else Z_NOISY
 
    def score(self, snapshot):
        """Score one snapshot dict. Returns:
           {available, severity('ok'|'watch'|'elevated'), anomalous(bool),
            score(float), reason(str), culprits[list of (feature, z)]}
        Missing engineered keys fall back to learned mean (neutral). Call once
        per snapshot cycle - it maintains the persistence state machine."""
        if not self.ok:
            return {"available": False, "severity": "ok"}
 
        row = []
        zmap = {}
        for f in self.features:
            v = snapshot.get(f, None)
            if not isinstance(v, (int, float)):
                v = self.baselines[f]["mean"]
            v = float(v)
            row.append(v)
            std = self.baselines[f]["std"]
            zmap[f] = (v - self.baselines[f]["mean"]) / std if std > 1e-9 else 0.0
 
        X = self.scaler.transform([row])
        s = float(self.model.score_samples(X)[0])
        forest_anom = s < self.threshold
        zhits = [f for f, z in zmap.items() if abs(z) >= self._bar(f)]
        anomalous = forest_anom or bool(zhits)
 
        # Persistence state machine.
        if anomalous:
            self._streak += 1
            self._clean = 0
        else:
            self._clean += 1
            if self._clean >= DECAY:
                self._streak = 0
 
        if self._streak >= ELEVATED_AFTER:
            self._severity = "elevated"
        elif self._streak >= WATCH_AFTER:
            self._severity = "watch"
        elif self._streak == 0:
            self._severity = "ok"
        # else: hold current severity while decaying
 
        if not anomalous and self._severity == "ok":
            reason = "ok"
        elif forest_anom and zhits:
            reason = "unusual combination + sensor far from normal"
        elif forest_anom:
            reason = "unusual combination of readings"
        elif zhits:
            reason = "sensor far from normal"
        else:
            reason = "settling"
 
        culprits = sorted(
            [(f, round(z, 1)) for f, z in zmap.items() if abs(z) >= Z_NAME],
            key=lambda c: -abs(c[1]))
 
        return {"available": True, "severity": self._severity,
                "anomalous": anomalous, "score": round(s, 4),
                "reason": reason, "culprits": culprits}
 
    def reset(self):
        # Clear persistence (e.g. engine off / new drive).
        self._streak = 0; self._clean = 0; self._severity = "ok"
 
