"""Phase 2: train the Isolation Forest anomaly detector from collected normal
driving data. Runs on the LAPTOP (not the Pi) - training is heavy, inference
is light. Sourced approach: train only on normal data, learn the boundary of
normal, flag deviations (EngineAD / predictive-maintenance literature).

Usage:  py train_model.py driving_log.csv
Outputs: models/anomaly_model.joblib (the trained model + scaler + metadata)

Pairs the Isolation Forest (multivariate anomaly score) with per-feature
statistical baselines (mean/std) so at inference we can say WHICH sensor is
off, not just 'something is off' - the interpretability layer that makes the
output human-readable ('coolant abnormally high')."""
import sys
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib
import os

# Feature columns - MUST match DataLogger.FIELDS order (minus timestamp).
FEATURES = ["rpm", "speed", "coolant_temp", "engine_load", "boost", "afr",
            "voltage", "timing", "throttle", "intake_temp", "stft_b1", "ltft_b1"]


def main():
    if len(sys.argv) < 2:
        print("Usage: py train_model.py <driving_log.csv>")
        return
    csv_path = sys.argv[1]

    # 1. LOAD -----------------------------------------------------------
    df = pd.read_csv(csv_path)
    print("Loaded", len(df), "rows from", csv_path)
    # Drop any rows with missing features (should be none - logger pre-cleans).
    df = df.dropna(subset=FEATURES)
    print("After cleaning:", len(df), "rows")
    if len(df) < 500:
        print("WARNING: <500 rows. Collect more driving data before training.")

    X = df[FEATURES].values

    # 2. SCALE ----------------------------------------------------------
    # StandardScaler so every feature contributes fairly (sourced: stage-1
    # preprocessing/normalization). Saved with the model so inference scales
    # identically.
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 3. TRAIN ISOLATION FOREST -----------------------------------------
    # contamination='auto' - we assume the log is mostly normal (it's your
    # normal driving). n_estimators=100 is the standard default. random_state
    # for reproducibility (showcase: results are repeatable).
    model = IsolationForest(n_estimators=100, contamination="auto",
                            random_state=42)
    model.fit(X_scaled)
    print("Isolation Forest trained on", X_scaled.shape[0], "samples,",
          X_scaled.shape[1], "features.")

    # 4. PER-FEATURE BASELINES (interpretability layer) -----------------
    # Mean/std per feature so inference can name WHICH sensor is anomalous.
    baselines = {}
    for i, feat in enumerate(FEATURES):
        baselines[feat] = {"mean": float(np.mean(X[:, i])),
                           "std": float(np.std(X[:, i]))}

    # 5. SCORE DISTRIBUTION (for setting the alert threshold) -----------
    # The model's anomaly scores on the training (normal) data. We record the
    # 1st percentile as a sensible 'this is unusually low' threshold.
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