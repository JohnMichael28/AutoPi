"""Runs data_provider.snapshot() on a background thread so slow OBD reads
never block the render loop. Also feeds each snapshot to the ML DataLogger
(if provided) to build the training dataset during the learning phase."""
import threading
import time


class SnapshotThread:
    def __init__(self, data_provider, interval=0.75, logger=None):
        self._data = data_provider
        self._interval = interval
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
            if self._logger is not None:
                self._logger.log(snap)             # feed the ML data logger
            time.sleep(self._interval)

    def latest(self):
        """Return the most recent snapshot. Non-blocking, instant."""
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False