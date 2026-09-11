"""Phase 2: train the Isolation Forest anomaly detector from collected normal
driving data. Runs on the LAPTOP (not the Pi) - training is heavy, inference is
light. Sourced approach: train only on normal data, learn the boundary of
normal, flag deviations (EngineAD / predictive-maintenance literature).

Usage:  python train_model.py driving_log.csv
Outputs: models/anomaly_model.joblib (model + scaler + feature list + baselines)

This version feeds the SQL-engineered features (build_features.py -> features.sql)
into the model instead of raw PIDs, so (a) sparse/tiered columns are forward-
filled instead of dropped, and (b) rolling-window context features let the model
catch drift, not just instantaneous spikes."""
import sys
import os
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features

# The model's input vector = the engineered feature columns (NO timestamp, NO
# intake_temp). Order is fixed and saved with the model so inference matches.
FEATURES = ["rpm", "speed", "coolant_temp", "engine_load", "throttle",
            "boost", "afr", "voltage", "timing", "stft_b1", "ltft_b1",
            "coolant_avg5", "load_avg5", "rpm_avg5",
            "coolant_d", "voltage_d", "rpm_d"]

MAX_TRAIN = 50000   # sample cap - IF needs no more; keeps training fast


def main():
    if len(sys.argv) < 2:
        print("Usage: python train_model.py <driving_log.csv>")
        return
    csv_path = sys.argv[1]

    # 1. LOAD + SQL FEATURE ENGINEERING --------------------------------
    df = build_features(csv_path)
    print("Engineered feature rows:", len(df))
    df = df.dropna(subset=FEATURES)     # safety net; SQL already fills
    print("After final clean:", len(df), "rows")
    if len(df) < 500:
        print("WARNING: <500 rows. Collect more driving data before training.")
        if len(df) == 0:
            return

    # Sample down if huge (Isolation Forest gains nothing past ~tens of k).
    if len(df) > MAX_TRAIN:
        df = df.sample(MAX_TRAIN, random_state=42)
        print("Sampled down to", len(df), "rows for training.")

    X = df[FEATURES].values

    # 2. SCALE ----------------------------------------------------------
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 3. TRAIN ISOLATION FOREST -----------------------------------------
    model = IsolationForest(n_estimators=100, contamination="auto",
                            random_state=42, n_jobs=-1)
    model.fit(X_scaled)
    print("Isolation Forest trained on", X_scaled.shape[0], "samples,",
          X_scaled.shape[1], "features.")

    # 4. PER-FEATURE BASELINES (interpretability layer) -----------------
    baselines = {}
    for i, feat in enumerate(FEATURES):
        baselines[feat] = {"mean": float(np.mean(X[:, i])),
                           "std": float(np.std(X[:, i]))}

    # 5. SCORE THRESHOLD ------------------------------------------------
    scores = model.score_samples(X_scaled)
    threshold = float(np.percentile(scores, 1.0))   # bottom 1% of normal
    print("Anomaly score threshold (1st pctile of normal):", round(threshold, 4))

    # 6. SAVE -----------------------------------------------------------
    os.makedirs("models", exist_ok=True)
    bundle = {"model": model, "scaler": scaler, "features": FEATURES,
              "baselines": baselines, "threshold": threshold}
    joblib.dump(bundle, "models/anomaly_model.joblib")
    print("Saved -> models/anomaly_model.joblib")
    print("Deploy: scp models/anomaly_model.joblib "
          "john288@autopi.local:~/autopi-project/models/")


if __name__ == "__main__":
    main()