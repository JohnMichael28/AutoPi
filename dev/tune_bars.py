"""Sweep candidate z-bar settings and print, for each, the false-positive rate
on real normal data AND which of the 6 realistic faults it still catches. Lets
us pick the bar from real numbers instead of guessing. Run:
    python autopi/tune_bars.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
import joblib, numpy as np

STABLE_FEATURES = {"coolant_temp","coolant_avg5","coolant_d","voltage",
                   "voltage_d","afr","stft_b1","ltft_b1","timing"}

def zrow(row, feats, base):
    return {f: ((row[i]-base[f]["mean"])/base[f]["std"] if base[f]["std"]>1e-9 else 0.0)
            for i, f in enumerate(feats)}

def flagged(z, feats, z_stable, z_noisy, forest_bad):
    if forest_bad: return True
    for f in feats:
        bar = z_stable if f in STABLE_FEATURES else z_noisy
        if abs(z[f]) >= bar: return True
    return False

def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    b = joblib.load("models/anomaly_model.joblib")
    feats, sc, model, base, thr = (b["features"], b["scaler"], b["model"],
                                   b["baselines"], b["threshold"])
    df = build_features(csv)

    # sample normals + precompute forest verdict
    sample = df.sample(min(5000, len(df)), random_state=11)
    Xn = sc.transform(sample[feats].values)
    forest_n = model.score_samples(Xn) < thr
    zrows_n = [zrow(r, feats, base) for r in sample[feats].values]

    # the 6 faults as median + overrides
    med = {f: float(df[f].median()) for f in feats}
    faults = {
     "overheat": {"coolant_temp":108,"coolant_avg5":106,"coolant_d":8,"speed":0,"rpm":900,"rpm_avg5":900,"engine_load":30},
     "charging": {"voltage":11.8,"voltage_d":-1.2,"rpm":1500,"rpm_avg5":1500},
     "lean":     {"stft_b1":18,"ltft_b1":15,"afr":14.7},
     "rich":     {"stft_b1":-18,"ltft_b1":-14,"afr":11.5},
     "overrev":  {"rpm":5600,"rpm_avg5":5400,"rpm_d":2500,"speed":0,"engine_load":95,"throttle":75},
     "knock":    {"timing":-15,"engine_load":85,"rpm":3200,"rpm_avg5":3100,"throttle":60},
    }
    def fault_flagged(ov, zs, zn):
        snap = dict(med); snap.update(ov)
        row = [float(snap[f]) for f in feats]
        fb = float(model.score_samples(sc.transform([row]))[0]) < thr
        z = zrow(row, feats, base)
        return flagged(z, feats, zs, zn, fb)

    print(f"{'Z_stable':>8} {'Z_noisy':>7} {'FP%':>6}  faults_caught")
    for zs in (3.5, 4.0, 4.5, 5.0, 5.5, 6.0):
        for zn in (6.0, 7.0):
            fp = sum(flagged(zrows_n[i], feats, zs, zn, forest_n[i])
                     for i in range(len(sample)))
            caught = [name for name, ov in faults.items() if fault_flagged(ov, zs, zn)]
            print(f"{zs:8.1f} {zn:7.1f} {100*fp/len(sample):6.1f}  "
                  f"{len(caught)}/6  {caught}")

if __name__ == "__main__":
    main()