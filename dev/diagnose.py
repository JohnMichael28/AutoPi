"""Diagnose why single-feature faults don't register. Prints the real spread of
each engineered feature in your training data. Run:
    python autopi/diagnose.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features

def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    df = build_features(csv)
    feats = ["rpm","speed","coolant_temp","engine_load","throttle","boost","afr",
             "voltage","timing","stft_b1","ltft_b1","coolant_avg5","load_avg5",
             "rpm_avg5","coolant_d","voltage_d","rpm_d"]
    print(f"rows: {len(df)}\n")
    print(f"{'feature':14s} {'min':>10s} {'max':>10s} {'mean':>10s} {'std':>10s} {'nunique':>8s}")
    for f in feats:
        s = df[f]
        print(f"{f:14s} {s.min():10.2f} {s.max():10.2f} {s.mean():10.2f} {s.std():10.2f} {s.nunique():8d}")
    # A feature with near-zero std, or one whose 'normal' range already spans
    # your 'fault' value, is the culprit - it makes that fault look normal.

if __name__ == "__main__":
    main()