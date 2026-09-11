"""Find WHERE the false positives come from: for a sample of real normal rows
that get flagged, tally which layer fired (forest vs z-guard) and which features
tripped the z-guard. Tells us exactly which bar to raise. Run:
    python autopi/fp_audit.py autopi/data/driving_log.csv
"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
from anomaly import AnomalyDetector, STABLE_FEATURES, Z_STABLE, Z_NOISY

def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    d = AnomalyDetector("models/anomaly_model.joblib")
    df = build_features(csv)
    feats = d.features
    sample = df.sample(min(5000, len(df)), random_state=11)

    forest_only = zguard_only = both = 0
    trip_counts = collections.Counter()
    for _, r in sample.iterrows():
        snap = {f: float(r[f]) for f in feats}
        res = d.score(snap)
        if not res["anomaly"]:
            continue
        # recompute which layer(s)
        row = [float(r[f]) for f in feats]
        X = d.scaler.transform([row]); s = float(d.model.score_samples(X)[0])
        forest = s < d.threshold
        hits = []
        for f in feats:
            std = d.baselines[f]["std"]
            if std > 1e-9:
                z = (float(r[f]) - d.baselines[f]["mean"]) / std
                bar = Z_STABLE if f in STABLE_FEATURES else Z_NOISY
                if abs(z) >= bar:
                    hits.append(f); trip_counts[f] += 1
        if forest and hits: both += 1
        elif forest: forest_only += 1
        else: zguard_only += 1

    total = forest_only + zguard_only + both
    print(f"sampled {len(sample)} normal rows, {total} flagged "
          f"({100*total/len(sample):.1f}%)")
    print(f"  forest only : {forest_only}")
    print(f"  zguard only : {zguard_only}")
    print(f"  both        : {both}")
    print("\nz-guard trip counts by feature (which bar to raise):")
    for f, c in trip_counts.most_common():
        bar = Z_STABLE if f in STABLE_FEATURES else Z_NOISY
        print(f"  {f:14s} tripped {c:4d}x  (bar {bar}, "
              f"{'STABLE' if f in STABLE_FEATURES else 'noisy'})")

if __name__ == "__main__":
    main()