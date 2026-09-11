-- ============================================================================
-- AutoPi feature-engineering pipeline  (CS 3312 Transformation stage, in SQL)
-- ============================================================================
-- Input : raw_log  (the driving_log.csv loaded verbatim, sparse cells = NULL)
-- Output: features (one clean row per timestamp, every column a real number,
--         PLUS rolling-window context features the raw log can't express)
--
-- WHY SQL: per the course pipeline (Ingestion -> Cleaning -> Transformation ->
-- ... -> AI). Doing this in SQL (not pandas) removes the export step and keeps
-- the transformation auditable. Each CTE below is one named stage.
-- ============================================================================

-- Stage 1 - CLEANING: forward-fill the slow/tiered PIDs.
-- The tiered poller only refreshes boost/afr/trims/voltage/timing every ~10th
-- row, so most cells are NULL. A NULL doesn't mean "0" - it means "unchanged
-- since last read". So we carry the last known value forward (LOCF - last
-- observation carried forward), the standard fill for tiered sensor logs.
-- SQLite has no native LOCF, so we use a correlated subquery: for each NULL,
-- grab the most recent non-NULL value at or before this row.
WITH filled AS (
  SELECT
    rowid AS rid,
    timestamp,
    rpm,
    speed,
    coolant_temp,
    engine_load,
    throttle,
    -- forward-fill each slow PID
    COALESCE(boost,   (SELECT b.boost   FROM raw_log b WHERE b.boost   IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS boost,
    COALESCE(afr,     (SELECT b.afr     FROM raw_log b WHERE b.afr     IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS afr,
    COALESCE(voltage, (SELECT b.voltage FROM raw_log b WHERE b.voltage IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS voltage,
    COALESCE(timing,  (SELECT b.timing  FROM raw_log b WHERE b.timing  IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS timing,
    COALESCE(stft_b1, (SELECT b.stft_b1 FROM raw_log b WHERE b.stft_b1 IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS stft_b1,
    COALESCE(ltft_b1, (SELECT b.ltft_b1 FROM raw_log b WHERE b.ltft_b1 IS NOT NULL AND b.rowid <= r.rowid ORDER BY b.rowid DESC LIMIT 1)) AS ltft_b1
  FROM raw_log r
),

-- Stage 2 - TRANSFORMATION: rolling-window context features.
-- A single reading has no history. "coolant 96" is fine; "coolant 96 and
-- climbing 4 deg in 20s" is not. Window functions add that memory:
--   *_avg5   : short (5-row ~4s) rolling mean  - smooths noise
--   *_d      : delta vs 10 rows ago            - rate of change / trend
-- This is the feature set that lets the model catch drift, not just spikes.
windowed AS (
  SELECT
    f.*,
    AVG(coolant_temp) OVER w5 AS coolant_avg5,
    AVG(engine_load)  OVER w5 AS load_avg5,
    AVG(rpm)          OVER w5 AS rpm_avg5,
    coolant_temp - LAG(coolant_temp, 10) OVER wo AS coolant_d,
    voltage      - LAG(voltage, 10)      OVER wo AS voltage_d,
    rpm          - LAG(rpm, 10)          OVER wo AS rpm_d
  FROM filled f
  WINDOW
    w5 AS (ORDER BY f.rid ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
    wo AS (ORDER BY f.rid)
)

-- Stage 3 - FINAL SELECT: only rows where the fill succeeded (engine warmed up
-- and every slow PID has been seen at least once), and drop intake_temp
-- entirely (this Subaru never reports it - a column that is always NULL carries
-- zero signal and would just break the model).
SELECT
  timestamp,
  rpm, speed, coolant_temp, engine_load, throttle,
  boost, afr, voltage, timing, stft_b1, ltft_b1,
  coolant_avg5, load_avg5, rpm_avg5,
  COALESCE(coolant_d, 0) AS coolant_d,
  COALESCE(voltage_d, 0) AS voltage_d,
  COALESCE(rpm_d, 0)     AS rpm_d
FROM windowed
WHERE boost   IS NOT NULL
  AND afr     IS NOT NULL
  AND voltage IS NOT NULL
  AND timing  IS NOT NULL
  AND stft_b1 IS NOT NULL
  AND ltft_b1 IS NOT NULL
  -- drop physically-impossible / glitch rows so the model learns real normal
  AND boost   BETWEEN 0 AND 60
  AND stft_b1 BETWEEN -25 AND 25
  AND ltft_b1 BETWEEN -25 AND 25
  AND coolant_d BETWEEN -15 AND 15
  AND rpm_d   BETWEEN -3000 AND 3000;