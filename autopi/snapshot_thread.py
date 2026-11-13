"""Runs data_provider.snapshot() on a background thread so slow OBD reads
never block the render loop. Also feeds each snapshot to the ML DataLogger
(if provided) to build the training dataset during the learning phase."""
import threading
import time


class SnapshotThread:
    def __init__(self, data_provider, interval=0.75, logger=None,
                 log_interval=1.2):
        self._data = data_provider
        self._interval = interval
        # The ML model was trained on rows ~1.2 s apart (the old poll rate:
        # median 51 rows/min in driving_log.csv). Snapshots are faster now,
        # so the logger keeps the SAME row spacing or the rolling features
        # (avg of 5 rows, change over 10 rows) would mean something else.
        self._log_interval = log_interval
        self._last_log = 0.0
        self._logger = logger           # optional DataLogger for ML collection
        self._latest = data_provider.snapshot()   # prime once before UI starts
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            snap = self._data.snapshot()           # blocks HERE, off the UI loop
            with self._lock:
                self._latest = snap
            now = time.monotonic()
            if self._logger is not None and now - self._last_log >= self._log_interval:
                self._last_log = now
                self._logger.log(snap)             # feed the ML data logger
            time.sleep(self._interval)

    def latest(self):
        """Return the most recent snapshot. Non-blocking, instant."""
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False