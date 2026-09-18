"""Operating-regime definitions - the ONE place both training and inference
decide which driving state a reading belongs to. Keeping this shared means the
trainer and the on-Pi detector can never disagree about regimes (the same
single-source-of-truth principle as features.sql).

CONTEXTUAL ANOMALY DETECTION: instead of one global "normal", we compare each
reading against the normal FOR ITS CURRENT OPERATING STATE. A 65C coolant is
normal cold-starting but abnormal warm; high load is normal climbing but not at
idle. Formally this is conditional detection - P(reading | regime) rather than
P(reading).

Regimes were chosen from the real data, NOT guessed. Exploration of 312k logged
rows showed a candidate 4-regime split (cold_start/idle/cruise/high_load) had
idle and cruise with near-identical sensor means (coolant 91/90.5, load 46/39,
volt 13.6/13.8) - so splitting them added no signal and starved each of data.
Merging idle+cruise into one "normal" regime gives 3 regimes that each genuinely
differ, which is the coarse, principled split the literature recommends over
fine-grained over-segmentation (which raises false positives, not lowers them).

  cold_start : coolant_temp < 70 C   (proven distinct: 65C vs 91C elsewhere)
  high_load  : engine_load  >= 70 %  (proven distinct: load 85, rpm 2083)
  normal     : everything else       (warm, ordinary driving - ~96% of data)
"""

REGIMES = ["cold_start", "high_load", "normal"]

COLD_START_COOLANT_C = 70.0
HIGH_LOAD_PCT = 70.0


def regime_of(row):
    """Return the regime name for one feature row (dict or pandas Series).
    Order matters - first match wins. cold_start takes priority because a cold
    engine under load is still fundamentally 'warming up' behavior."""
    coolant = row["coolant_temp"]
    load = row["engine_load"]
    if coolant is not None and coolant < COLD_START_COOLANT_C:
        return "cold_start"
    if load is not None and load >= HIGH_LOAD_PCT:
        return "high_load"
    return "normal"