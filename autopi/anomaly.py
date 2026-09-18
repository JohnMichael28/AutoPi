"""Phase 3: on-Pi CONTEXTUAL inference. Load the per-regime bundle and score
live snapshots against the baseline FOR THE CURRENT OPERATING REGIME. LIGHT -
microseconds per call, safe every cycle on the Pi 4.

CONTEXTUAL DETECTION: each reading is first assigned a regime (cold_start /
high_load / normal, see regimes.py), then scored ONLY against that regime's
learned normal. This is the fix for the global model's false positives - a 65C
cold-start coolant is judged against cold-start normal (fine), not against a
warm-driving average (which flagged it). Formally: P(reading | regime).

Within each regime, the same two-layer detection as before:
  Layer A - Isolation Forest: unusual multivariate COMBINATIONS.
  Layer B - per-feature z-guard with per-feature bars: any single sensor far
            from THIS REGIME's normal. Stable diagnostic features get a low bar
            (4.0 sigma); noisy driving-dynamics features a high bar (6.0).
Plus a persistence state machine so momentary blips read 'watch' and only
sustained anomalies reach 'elevated'.

The rule-based WarningAdvisor remains the real-time SAFETY layer; this is the
complementary 'statistically unusual for what the car is doing' signal."""
import os

try:
    import joblib
    import numpy as np
except Exception:
    joblib = None
    np = None

try:
    from autopi.regimes import regime_of, REGIMES
except Exception:
    try:
        from regimes import regime_of, REGIMES
    except Exception:
        regime_of = None
        REGIMES = ["normal"]

STABLE_FEATURES = {"coolant_temp", "coolant_avg5", "coolant_d",
                   "voltage", "voltage_d", "afr",
                   "stft_b1", "ltft_b1", "timing"}
Z_STABLE = 4.0
Z_NOISY = 6.0
Z_NAME = 2.5

WATCH_AFTER = 1
ELEVATED_AFTER = 5
DECAY = 2


class AnomalyDetector:
    def __init__(self, path="models/anomaly_model.joblib"):
        self.ok = False
        self._bundle = None
        self._streak = 0
        self._clean = 0
        self._severity = "ok"
        self._last_regime = "normal"
        if joblib is None or not os.path.exists(path):
            return
        try:
            b = joblib.load(path)
            # Contextual (v2) bundle: {features, regimes:{name:{model,scaler,
            # baselines,threshold}}, fallback}. Guard so an old global bundle
            # doesn't crash - but we expect v2 from the current trainer.
            if not b.get("contextual"):
                # Old single-model bundle: wrap it as a one-regime 'normal'.
                self.features = b["features"]
                self._regimes = {"normal": {
                    "model": b["model"], "scaler": b["scaler"],
                    "baselines": b["baselines"], "threshold": b["threshold"]}}
                self._fallback = "normal"
            else:
                self.features = b["features"]
                self._regimes = b["regimes"]
                self._fallback = b.get("fallback", "normal")
            self.ok = "normal" in self._regimes or len(self._regimes) > 0
        except Exception:
            self.ok = False

    def _bar(self, feature):
        return Z_STABLE if feature in STABLE_FEATURES else Z_NOISY

    def _pick_regime(self, snapshot):
        if regime_of is None:
            return self._fallback
        try:
            name = regime_of(snapshot)
        except Exception:
            name = self._fallback
        # If that regime wasn't trained (too little data), fall back.
        if name not in self._regimes:
            name = self._fallback if self._fallback in self._regimes \
                else next(iter(self._regimes))
        return name

    def score(self, snapshot):
        """Score one snapshot dict against its regime's baseline. Returns:
           {available, severity, anomalous, score, reason, regime,
            culprits[(feature, z)]}. Missing engineered keys fall back to that
           regime's learned mean. Call once per cycle (maintains persistence)."""
        if not self.ok:
            return {"available": False, "severity": "ok"}

        regime = self._pick_regime(snapshot)
        self._last_regime = regime
        R = self._regimes[regime]
        baselines = R["baselines"]; scaler = R["scaler"]
        model = R["model"]; threshold = R["threshold"]

        row = []
        zmap = {}
        for f in self.features:
            v = snapshot.get(f, None)
            if not isinstance(v, (int, float)):
                v = baselines[f]["mean"]
            v = float(v)
            row.append(v)
            std = baselines[f]["std"]
            zmap[f] = (v - baselines[f]["mean"]) / std if std > 1e-9 else 0.0

        X = scaler.transform([row])
        s = float(model.score_samples(X)[0])
        forest_anom = s < threshold
        zhits = [f for f, z in zmap.items() if abs(z) >= self._bar(f)]
        anomalous = forest_anom or bool(zhits)

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
                "reason": reason, "regime": regime, "culprits": culprits}

    def reset(self):
        self._streak = 0; self._clean = 0; self._severity = "ok"