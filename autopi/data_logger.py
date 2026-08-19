"""Phase 1 of the ML anomaly-detection pipeline: silently log every snapshot
to CSV during the learning phase. This builds the 'normal driving' dataset
that an Isolation Forest is later trained on (sourced approach: train only on
normal data, learn the boundary of normal, flag deviations - EngineAD /
predictive-maintenance literature).

Design choices:
- CSV (not SQLite): human-readable, drops straight into pandas for training,
  reviewable by anyone looking at the project. Append-only = crash-safe.
- Runs on the snapshot thread's cadence but writes are cheap (one row).
- Only logs rows where the ENGINE IS RUNNING (rpm > 0) - we want the model to
  learn running-engine normal, not key-on-engine-off or disconnected garbage.
- Logs raw numeric PIDs only; '--' (missing) rows are skipped so the training
  data stays clean (no imputation headaches later)."""
import os
import csv
import time


class DataLogger:
    # The features we log = the model's input vector. Order is fixed and
    # must match what the training script expects. Chosen from the sourced
    # 'core diagnostic PIDs' (fuel trims, O2, coolant, load, rpm, boost, etc.).
    FIELDS = ["timestamp", "rpm", "speed", "coolant_temp", "engine_load",
              "boost", "afr", "voltage", "timing", "throttle", "intake_temp",
              "stft_b1", "ltft_b1"]

    def __init__(self, path="data/driving_log.csv"):
        self.__path = path
        self.__ensure_file()

    def __ensure_file(self):
        # Create the data dir + CSV header once. Append-only afterward.
        directory = os.path.dirname(self.__path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self.__path):
            with open(self.__path, "w", newline="") as f:
                csv.writer(f).writerow(self.FIELDS)

    def log(self, snapshot):
        # Append one row IF the engine is running and the row is complete.
        # Skips disconnected / engine-off / partial rows so the training set
        # stays clean (sourced: preprocessing/clean data is stage 1).
        rpm = snapshot.get("rpm", "--")
        if not isinstance(rpm, (int, float)) or rpm <= 0:
            return                      # engine off or no data - don't log
        row = [time.strftime("%Y-%m-%d %H:%M:%S")]
        for key in self.FIELDS[1:]:     # skip 'timestamp', already added
            value = snapshot.get(key, "--")
            # Only log fully-numeric rows; a single '--' means skip the row,
            # so we never train on missing values.
            if not isinstance(value, (int, float)):
                return
            row.append(value)
        try:
            with open(self.__path, "a", newline="") as f:
                csv.writer(f).writerow(row)
        except Exception:
            pass                        # logging must NEVER crash the app