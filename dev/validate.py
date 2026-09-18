"""Validate the CONTEXTUAL model: measure the false-positive rate per regime on
real normal data (this is the number we're trying to lower vs the global model),
and confirm realistic faults still fire. Run:
    python autopi/validate.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
from anomaly import AnomalyDetector
from regimes import regime_of, REGIMES


def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    d = AnomalyDetector("models/anomaly_model.joblib")
    print("model loaded:", d.ok, "| regimes:", list(getattr(d, "_regimes", {}).keys()))
    if not d.ok:
        return
    df = build_features(csv)
    df["regime"] = df.apply(regime_of, axis=1)
    feats = d.features

    # FALSE-POSITIVE RATE overall and per regime (fresh detector each sample so
    # persistence doesn't blend rows; we want the raw per-reading flag rate).
    print("\nFalse-positive rate on real NORMAL data (want low, esp. cold_start):")
    overall_flag = overall_n = 0
    for name in REGIMES:
        sub = df[df["regime"] == name]
        if len(sub) == 0:
            continue
        sample = sub.sample(min(2000, len(sub)), random_state=7)
        flagged = 0
        for _, r in sample.iterrows():
            det = AnomalyDetector("models/anomaly_model.joblib")  # reset state
            res = det.score({f: float(r[f]) for f in feats})
            if res.get("anomalous"):
                flagged += 1
        overall_flag += flagged; overall_n += len(sample)
        print("  %-11s %5d sampled  %5.1f%% flagged" %
              (name, len(sample), 100*flagged/len(sample)))
    print("  %-11s %5d sampled  %5.1f%% flagged  <- was ~8-10%% on global model"
          % ("OVERALL", overall_n, 100*overall_flag/max(overall_n, 1)))

    # REALISTIC FAULTS still caught (built per regime so they're judged right).
    print("\nRealistic faults (should be ANOMALY):")
    med = {f: float(df[f].median()) for f in feats}
    def test(name, ov):
        det = AnomalyDetector("models/anomaly_model.joblib")
        snap = dict(med); snap.update(ov)
        res = det.score(snap)
        tag = "ANOMALY" if res.get("anomalous") else "normal "
        cul = ", ".join("%s(%+.1f)" % (c, z) for c, z in res.get("culprits", [])[:3])
        print("  %s | %-22s regime=%-10s %s" %
              (tag, name, res.get("regime", "?"), cul))
    test("overheat (warm)", {"coolant_temp": 112, "coolant_avg5": 110, "coolant_d": 9})
    test("charging fault", {"voltage": 11.6, "voltage_d": -1.4})
    test("lean trims", {"stft_b1": 20, "ltft_b1": 16})
    test("rich trims", {"stft_b1": -20, "ltft_b1": -15, "afr": 11.5})
    test("overrev", {"rpm": 6000, "rpm_avg5": 5800, "rpm_d": 2500, "engine_load": 95})

if __name__ == "__main__":
    main()