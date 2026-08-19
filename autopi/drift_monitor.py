class DriftMonitor:
    """Surfaces the ML anomaly detector as meaningful, honest warnings.
    One anomalous reading is noise; a SUSTAINED pattern of anomalies means
    the car's behavior has genuinely drifted from normal - worth flagging.
    Tracks the recent anomaly rate and warns only when it's persistent
    (debounced, like a real monitoring system - avoids false alarms)."""

    def __init__(self, window=20, warn_ratio=0.35):
        # window: how many recent readings to consider
        # warn_ratio: fraction that must be anomalous to warn (0.35 = 35%)
        self._window = window
        self._warn_ratio = warn_ratio
        self._recent = []          # 1 = anomaly, 0 = normal
        self._warned = False       # so we don't repeat the same warning

    def add_result(self, is_anomaly):
        # Feed each reading's anomaly result (from AnomalyDetector.check)
        self._recent.append(1 if is_anomaly else 0)
        if len(self._recent) > self._window:
            self._recent.pop(0)

    def _anomaly_rate(self):
        if not self._recent:
            return 0.0
        return sum(self._recent) / len(self._recent)

    def check(self):
        # Returns a warning dict if drift is sustained, else None.
        # Needs a full-ish window first (don't warn on startup).
        if len(self._recent) < self._window // 2:
            return None

        rate = self._anomaly_rate()

        if rate >= self._warn_ratio:
            if not self._warned:
                self._warned = True   # warn once per drift episode
                return {"level": "warning",
                        "message": "Your engine's behavior has drifted from "
                                   "its normal pattern lately. Nothing is "
                                   "definitely wrong, but it'd be worth having "
                                   "it looked at."}
        else:
            self._warned = False   # behavior back to normal - reset

        return None

    def status(self):
        # For display: current drift level as a simple label
        rate = self._anomaly_rate()
        if rate >= self._warn_ratio:
            return "DRIFT DETECTED"
        elif rate >= self._warn_ratio / 2:
            return "watching"
        return "normal"