"""Explore the natural operating regimes in the real driving log BEFORE fixing
boundaries. The dissenting literature's failure mode is over-segmentation (too
many tiny regimes -> more false positives, not fewer), so we deliberately want
a SMALL number of coarse, physically-meaningful regimes. This script shows how
the data distributes across candidate regime rules so we pick boundaries from
evidence, not guesses.

Run: python autopi/pipeline/regime_explore.py autopi/data/driving_log.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_features import build_features

def regime_of(row):
    # Coarse, physical, rule-based regimes. Order matters (first match wins).
    # These are the CANDIDATE definitions - the script reports how much data
    # lands in each so we can adjust before committing.
    coolant = row["coolant_temp"]
    rpm = row["rpm"]
    speed = row["speed"]
    load = row["engine_load"]
    if coolant < 70:
        return "cold_start"      # engine warming up - everything runs different
    if speed < 3 and rpm < 1200:
        return "idle"            # stopped, engine idling
    if load >= 70:
        return "high_load"       # climbing, hard accel, towing
    return "cruise"              # normal driving (the catch-all)

def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "autopi/data/driving_log.csv"
    df = build_features(csv)
    df["regime"] = df.apply(regime_of, axis=1)
    total = len(df)
    print("Total feature rows:", total)
    print("\n%-12s %8s %7s   sample sensor means (coolant/rpm/load/volt)" % ("regime","rows","%"))
    print("-"*70)
    for reg in ["cold_start","idle","cruise","high_load"]:
        sub = df[df["regime"]==reg]
        if len(sub)==0:
            print("%-12s %8d %6.1f%%   (none)" % (reg, 0, 0)); continue
        print("%-12s %8d %6.1f%%   coolant=%.0f rpm=%.0f load=%.0f volt=%.1f" % (
            reg, len(sub), 100*len(sub)/total,
            sub["coolant_temp"].mean(), sub["rpm"].mean(),
            sub["engine_load"].mean(), sub["voltage"].mean()))
    print("\n--- Why this matters ---")
    print("If any regime has <2% of rows, it may be too rare to model well")
    print("(would need its own baseline from too little data). If 'cruise'")
    print("holds >90%, the split isn't doing much. Ideal: each regime a")
    print("meaningful slice with DIFFERENT sensor means (proving they really")
    print("are distinct operating states that deserve separate baselines).")
    # Show how different the regimes actually are on the key sensor:
    print("\nCoolant mean per regime (should differ - that's the whole point):")
    for reg in ["cold_start","idle","cruise","high_load"]:
        sub = df[df["regime"]==reg]
        if len(sub): print("  %-12s %.1f C" % (reg, sub["coolant_temp"].mean()))

if __name__ == "__main__":
    main()