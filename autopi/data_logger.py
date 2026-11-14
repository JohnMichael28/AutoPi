"""Phase 1 of the ML anomaly-detection pipeline: silently log every snapshot
to CSV during the learning phase. This builds the 'normal driving' dataset
that an Isolation Forest is later trained on (sourced approach: train only on
normal data, learn the boundary of normal, flag deviations - EngineAD /
predictive-maintenance literature).

Design choices:
- CSV (not SQLite): human-readable, drops straight into pandas for training,
  reviewable by anyone looking at the project. Append-only = crash-safe.
- Runs on the snapshot thread's cadence; rows are batched in memory and
  written about once a minute to save SD-card wear (see below).
- Only logs rows where the ENGINE IS RUNNING (rpm > 0) - we want the model to
  learn running-engine normal, not key-on-engine-off or disconnected garbage.
- Logs raw numeric PIDs only; '--' (missing) rows are skipped so the training
  data stays clean (no imputation headaches later)."""
import os
import csv
import shutil
import threading
import time


class DataLogger:
    # The features we log = the model's input vector. Order is fixed and
    # must match what the training script expects. Chosen from the sourced
    # 'core diagnostic PIDs' (fuel trims, O2, coolant, load, rpm, boost, etc.).
    FIELDS = ["timestamp", "rpm", "speed", "coolant_temp", "engine_load",
              "boost", "afr", "voltage", "timing", "throttle", "intake_temp",
              "stft_b1", "ltft_b1"]

    # v2 (Nov 2026): throttle moved to the FAST poll tier, so it's fresh every
    # row instead of a cached value up to ~7 s old, and each cycle reads one
    # more PID. New rows go to a new file so old and new data never get mixed
    # by accident; train on v2 alone, or on both once v2 has enough drives.
    #
    # SD-card friendly (Nov 2026): rows are kept in memory and written in one
    # batch (default every 60 s / 50 rows) instead of one SD write every
    # 1.2 s. The batch is also written the moment the engine stops and when
    # the app exits, so a normal key-off loses nothing; a hard power cut
    # mid-drive loses at most one batch. If the card is nearly full, logging
    # pauses instead of filling it.
    MAX_BUFFER = 5000            # never hold more than this in memory

    def __init__(self, path="data/driving_log_v2.csv", flush_s=60,
                 flush_rows=50, min_free_mb=500, clock=time.monotonic,
                 disk_usage=shutil.disk_usage):
        self.__path = path
        self.__flush_s = flush_s
        self.__flush_rows = flush_rows
        self.__min_free_mb = min_free_mb
        self.__clock = clock
        self.__disk_usage = disk_usage
        self.__buffer = []
        self.__last_flush = clock()
        self.__lock = threading.Lock()
        self.paused = False          # True = card nearly full, not logging
        self.writes = 0              # batches written (for tests / status)
        self.__ensure_file()

    def __ensure_file(self):
        # Create the data dir + CSV header if the file isn't there. Checked
        # before every batch too, because the storage keeper moves last
        # month's file into data/archive/ and the next batch starts a new one.
        directory = os.path.dirname(self.__path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self.__path):
            with open(self.__path, "w", newline="") as f:
                csv.writer(f).writerow(self.FIELDS)

    def __card_full(self):
        try:
            free = self.__disk_usage(os.path.dirname(os.path.abspath(self.__path)))
            return free.free / (1024.0 * 1024.0) < self.__min_free_mb
        except Exception:
            return False

    def log(self, snapshot):
        # Buffer one row if the engine is running. Missing fields ('--') are
        # written as empty cells rather than causing the whole row to be
        # dropped - some cars don't support every PID (e.g. intake_temp on this
        # Subaru), and discarding every row over one missing sensor means we
        # log nothing. Training handles empty cells (drop/impute) downstream.
        rpm = snapshot.get("rpm", "--")
        if not isinstance(rpm, (int, float)) or rpm <= 0:
            # Engine off or no data: don't log, but save what we have now -
            # the key is probably about to turn off.
            self.flush()
            return
        row = [time.strftime("%Y-%m-%d %H:%M:%S")]
        for key in self.FIELDS[1:]:     # skip 'timestamp', already added
            value = snapshot.get(key, "--")
            # Write the number if present, else an empty cell (not a skip).
            row.append(value if isinstance(value, (int, float)) else "")
        with self.__lock:
            self.__buffer.append(row)
            if len(self.__buffer) > self.MAX_BUFFER:
                del self.__buffer[:len(self.__buffer) - self.MAX_BUFFER]
            due = (len(self.__buffer) >= self.__flush_rows
                   or self.__clock() - self.__last_flush >= self.__flush_s)
        if due:
            self.flush()

    def flush(self):
        """Write every buffered row in one go. Safe to call any time, from
        any thread (engine stop, app exit). Logging must NEVER crash the app."""
        with self.__lock:
            rows = self.__buffer
            self.__buffer = []
            self.__last_flush = self.__clock()
        if not rows:
            return
        if self.__card_full():
            self.paused = True        # protect the OS: drop rows, don't fill
            return
        self.paused = False
        try:
            self.__ensure_file()
            with open(self.__path, "a", newline="") as f:
                csv.writer(f).writerows(rows)
            self.writes += 1
        except Exception:
            pass

    def pending(self):
        with self.__lock:
            return len(self.__buffer)
