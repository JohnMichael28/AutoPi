"""Test DEBOUNCE: with Z_stable=4.0 (keeps charging fault), require an anomaly
to persist N consecutive readings before it counts. Real faults persist; sensor
blips don't. Measures how debounce drops the false-positive rate on real
sequential data. Run:
    python autopi/tune_debounce.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
import joblib, numpy as np

STABLE = {"coolant_temp","coolant_avg5","coolant_d","voltage","voltage_d",
          "afr","stft_b1","ltft_b1","timing"}
Z_STABLE, Z_NOISY = 4.0, 6.0

def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    b = joblib.load("models/anomaly_model.joblib")
    feats, sc, model, base, thr = (b["features"], b["scaler"], b["model"],
                                   b["baselines"], b["threshold"])
    df = build_features(csv)
    # IMPORTANT: use a CONTIGUOUS slice (real time order), not a random sample,
    # so debounce (consecutive readings) is meaningful.
    seg = df.iloc[50000:80000].reset_index(drop=True)   # 30k contiguous rows
    X = sc.transform(seg[feats].values)
    forest = model.score_samples(X) < thr
    means = np.array([base[f]["mean"] for f in feats])
    stds  = np.array([base[f]["std"] if base[f]["std"]>1e-9 else 1e9 for f in feats])
    Z = np.abs((seg[feats].values - means) / stds)
    bars = np.array([Z_STABLE if f in STABLE else Z_NOISY for f in feats])
    zguard = (Z >= bars).any(axis=1)
    raw = forest | zguard      # per-reading anomaly (no debounce)

    print(f"contiguous rows: {len(seg)}")
    print(f"raw anomaly rate (no debounce): {100*raw.mean():.1f}%\n")
    print(f"{'debounce N':>10} {'surfaced%':>10} {'avg alerts/1000 rows':>22}")
    for N in (1, 2, 3, 4, 5, 6, 8):
        # an alert 'surfaces' only after N consecutive raw-anomaly readings
        surfaced = np.zeros(len(raw), dtype=bool)
        run = 0
        for i, a in enumerate(raw):
            run = run + 1 if a else 0
            if run >= N:
                surfaced[i] = True
        # count distinct alert events (rising edges)
        events = int(np.sum(surfaced & ~np.roll(surfaced, 1)))
        print(f"{N:10d} {100*surfaced.mean():10.2f} {1000*events/len(seg):22.2f}")

    print("\nInterpretation: pick the smallest N where surfaced% is ~1% or less.")
    print("That N * 0.75s = seconds a fault must persist before you're alerted.")

if __name__ == "__main__":
    main()