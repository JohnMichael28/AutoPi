"""Validate the trained model WITHOUT a car, using REALISTIC combination faults.

Key lesson from the first pass: an Isolation Forest scores the whole 17-dim
point. Poking ONE raw feature while the other 16 stay at their median does NOT
look anomalous - real faults move several correlated features together. So each
fault below is built by taking a real normal row and applying a COHERENT set of
changes (an overheat also means coolant climbing AND usually low speed, etc).

Run:  python autopi/validate.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features
from anomaly import AnomalyDetector


def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    d = AnomalyDetector("models/anomaly_model.joblib")
    print("model loaded:", d.ok)
    if not d.ok:
        print("!! could not load models/anomaly_model.joblib"); return

    df = build_features(csv)
    feats = d.features

    # 1. FALSE-POSITIVE RATE on real normal data (want ~1-3%).
    sample = df.sample(min(3000, len(df)), random_state=7)
    flagged = sum(1 for _, r in sample.iterrows()
                  if d.score({f: float(r[f]) for f in feats})["anomaly"])
    print(f"\nNORMAL data: {flagged}/{len(sample)} flagged "
          f"({100*flagged/len(sample):.1f}%)  <- want ~1-3%")

    # 2. REALISTIC COMBINATION FAULTS (each should read ANOMALY).
    med = {f: float(df[f].median()) for f in feats}

    def test(name, overrides):
        snap = dict(med); snap.update(overrides)
        res = d.score(snap)
        tag = "ANOMALY" if res["anomaly"] else "normal "
        culp = ", ".join(f"{c}({z:+})" for c, z in res.get("culprits", [])[:4])
        print(f"  {tag} | {name:24s} score={res['score']:+.3f}  {culp}")
        return res["anomaly"]

    print("\nREALISTIC FAULTS (should be ANOMALY):")
    results = []
    # Overheat: coolant high + short-avg high + climbing + crawling/stopped
    results.append(test("overheat (climbing)", {
        "coolant_temp": 108, "coolant_avg5": 106, "coolant_d": 8,
        "speed": 0, "rpm": 900, "rpm_avg5": 900, "engine_load": 30}))
    # Charging system failure: voltage low + dropping, engine running
    results.append(test("charging fault", {
        "voltage": 11.8, "voltage_d": -1.2, "rpm": 1500, "rpm_avg5": 1500}))
    # Lean condition: both trims pushed high positive together
    results.append(test("lean fuel trims", {
        "stft_b1": 18, "ltft_b1": 15, "afr": 14.7}))
    # Rich / misfire-ish: trims dropped hard negative, afr low
    results.append(test("rich fuel trims", {
        "stft_b1": -18, "ltft_b1": -14, "afr": 11.5}))
    # Overrev under no load (money-shift / neutral rev): rpm way up, speed 0
    results.append(test("overrev at standstill", {
        "rpm": 5600, "rpm_avg5": 5400, "rpm_d": 2500,
        "speed": 0, "engine_load": 95, "throttle": 75}))
    # Timing pulled hard (knock retard): timing dumped negative under load
    # knock retard: timing crashes negative under load. timing is a STABLE
    # feature, so a big negative z here should trip the guard on its own.
    results.append(test("knock retard", {
        "timing": -15, "engine_load": 85, "rpm": 3200, "rpm_avg5": 3100,
        "throttle": 60}))

    # 3. CONTROLS (should read normal): real medians, gentle variations.
    print("\nCONTROLS (should be normal):")
    controls = []
    controls.append(not test("median cruise", {}))
    controls.append(not test("warm idle", {
        "rpm": 800, "rpm_avg5": 800, "speed": 0, "engine_load": 28}))
    controls.append(not test("moderate accel", {
        "rpm": 2600, "rpm_avg5": 2400, "speed": 40, "engine_load": 62,
        "throttle": 35, "rpm_d": 600}))

    hits = sum(results); ctrl_ok = sum(controls)
    print(f"\nSUMMARY: {hits}/{len(results)} faults caught | "
          f"{ctrl_ok}/{len(controls)} controls stayed normal | "
          f"normal FP rate {100*flagged/len(sample):.1f}%")
    if hits >= 5 and ctrl_ok == len(controls) and flagged/len(sample) < 0.05:
        print("VERDICT: model looks good - safe to deploy.")
    else:
        print("VERDICT: needs tuning - do NOT deploy yet. Paste this output.")


if __name__ == "__main__":
    main()