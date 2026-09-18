"""Phase 2: train the CONTEXTUAL Isolation Forest anomaly detector from
collected normal driving data. Runs on the LAPTOP (not the Pi).

CONTEXTUAL / PER-REGIME: instead of one global model, we split the data into
operating regimes (see regimes.py) and train a SEPARATE Isolation Forest +
per-feature baselines + threshold FOR EACH regime. At inference the detector
picks the regime first, then scores against that regime's normal. This fixes
the false positives the global model produced, where legitimate cold-start and
high-load readings looked 'anomalous' against a blurred all-driving average.

Usage:  python train_model.py driving_log.csv
Outputs: models/anomaly_model.joblib  (a dict of per-regime bundles + meta)
"""
import sys
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
from regimes import regime_of, REGIMES

FEATURES = ["rpm", "speed", "coolant_temp", "engine_load", "throttle",
            "boost", "afr", "voltage", "timing", "stft_b1", "ltft_b1",
            "coolant_avg5", "load_avg5", "rpm_avg5",
            "coolant_d", "voltage_d", "rpm_d"]

MAX_TRAIN_PER_REGIME = 50000   # cap per regime; IF needs no more
MIN_ROWS_PER_REGIME = 300      # below this, a regime can't train reliably


def train_one(df_reg, name):
    """Train one regime's forest + baselines + threshold. Returns a bundle
    dict, or None if there's too little data to be trustworthy."""
    df_reg = df_reg.dropna(subset=FEATURES)
    n = len(df_reg)
    if n < MIN_ROWS_PER_REGIME:
        print("  %-11s %6d rows  -> SKIP (too few, will fall back to 'normal')"
              % (name, n))
        return None
    if n > MAX_TRAIN_PER_REGIME:
        df_reg = df_reg.sample(MAX_TRAIN_PER_REGIME, random_state=42)
    X = df_reg[FEATURES].values
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model = IsolationForest(n_estimators=100, contamination="auto",
                            random_state=42, n_jobs=-1)
    model.fit(Xs)
    baselines = {f: {"mean": float(np.mean(X[:, i])),
                     "std": float(np.std(X[:, i]))}
                 for i, f in enumerate(FEATURES)}
    scores = model.score_samples(Xs)
    threshold = float(np.percentile(scores, 1.0))
    print("  %-11s %6d rows  -> trained (threshold %.4f)"
          % (name, len(df_reg), threshold))
    return {"model": model, "scaler": scaler, "baselines": baselines,
            "threshold": threshold, "n": int(n)}


def main():
    if len(sys.argv) < 2:
        print("Usage: python train_model.py <driving_log.csv>")
        return
    csv_path = sys.argv[1]

    df = build_features(csv_path)
    print("Engineered feature rows:", len(df))
    df = df.dropna(subset=FEATURES)
    df["regime"] = df.apply(regime_of, axis=1)
    print("Training one model per regime:")

    regime_bundles = {}
    for name in REGIMES:
        sub = df[df["regime"] == name]
        bundle = train_one(sub, name)
        if bundle is not None:
            regime_bundles[name] = bundle

    if "normal" not in regime_bundles:
        print("ERROR: 'normal' regime failed to train - aborting.")
        return

    out = {"version": 2, "contextual": True, "features": FEATURES,
           "regimes": regime_bundles, "fallback": "normal"}
    os.makedirs("models", exist_ok=True)
    joblib.dump(out, "models/anomaly_model.joblib")
    print("Saved -> models/anomaly_model.joblib  (%d regimes: %s)"
          % (len(regime_bundles), ", ".join(regime_bundles.keys())))
    print("Deploy: scp models/anomaly_model.joblib "
          "john288@autopi.local:~/autopi-project/models/")


if __name__ == "__main__":
    main()