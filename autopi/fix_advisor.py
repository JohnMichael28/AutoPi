"""ActionPlaybook: turns every warning / ML anomaly into "what to do" steps.

Rule-based and offline, so the WARNINGS tab always says what to do even
with no WiFi and no model. The guarded AI can add detail on tap, but these
steps never depend on it. The steps themselves live in playbook.json (data),
this class only matches and formats them.

Honest limit: AutoPi reads the car over OBD-II. It does not write engine
settings, so it cannot "fix" a fault itself - it tells you exactly what to
check and do, and you approve/do it.
"""
import json
import os


FEATURE_NAMES = {
    "coolant_temp": "coolant temp", "coolant_avg5": "coolant (avg)",
    "coolant_d": "coolant change", "stft_b1": "short fuel trim",
    "ltft_b1": "long fuel trim", "afr": "air/fuel ratio",
    "timing": "ignition timing", "rpm": "RPM", "rpm_avg5": "RPM (avg)",
    "rpm_d": "RPM change", "engine_load": "engine load",
    "load_avg5": "engine load (avg)", "boost": "MAF airflow",
    "intake_temp": "intake air temp", "throttle": "throttle",
    "speed": "speed", "voltage": "voltage", "voltage_d": "voltage change",
}


class ActionPlaybook:
    def __init__(self, path=None):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "playbook.json")
        self._warnings = []
        self._anomaly = {}
        self._look = {}
        self.ok = False
        try:
            with open(path) as f:
                data = json.load(f)
            self._warnings = data.get("warnings", [])
            self._anomaly = data.get("anomaly", {})
            self._look = data.get("look", {})
            self.ok = True
        except Exception:
            self.ok = False

    @staticmethod
    def plain_name(feature):
        return FEATURE_NAMES.get(feature, feature.replace("_", " "))

    def steps_for_warning(self, text):
        text = str(text)
        low = text.lower()
        for entry in self._warnings:
            if all(m.lower() in low for m in entry.get("match", [])):
                return list(entry.get("steps", []))
        return ["Watch this reading; if it repeats, run DIAGNOSTICS > HEALTH CHECK."]

    def warning_key(self, text):
        """Stable id for a warning (its matched rule), so "COOLANT HIGH
        (106C)" and "COOLANT HIGH (107C)" count as the same ongoing issue."""
        low = str(text).lower()
        for entry in self._warnings:
            match = entry.get("match", [])
            if match and all(m.lower() in low for m in match):
                return " ".join(match).upper()
        return str(text).split("(")[0].strip().upper()

    def page_for_warning(self, text):
        upper = str(text).upper()
        for prefix, page in self._look.get("warnings", {}).items():
            if upper.startswith(prefix):
                return page
        return None

    def page_for_anomaly(self, result):
        looks = self._look.get("anomaly", {})
        culprits = (result or {}).get("culprits", []) or []
        for feature, _z in culprits:
            best = None
            for key in looks:
                if not key.startswith("_") and feature.startswith(key) \
                        and (best is None or len(key) > len(best)):
                    best = key
            if best is not None:
                return looks[best]
        return looks.get("_default")

    def _family(self, feature):
        # Longest matching prefix wins: "coolant_avg5" -> "coolant".
        best = None
        for key in self._anomaly:
            if key.startswith("_"):
                continue
            if feature.startswith(key) and (best is None or len(key) > len(best)):
                best = key
        return best

    def steps_for_anomaly(self, result, max_sensors=2):
        culprits = (result or {}).get("culprits", []) or []
        steps = []
        seen = set()
        for feature, _z in culprits:
            fam = self._family(feature)
            if fam is None or fam in seen:
                continue
            seen.add(fam)
            steps.extend(self._anomaly.get(fam, []))
            if len(seen) >= max_sensors:
                break
        if not steps:
            steps = list(self._anomaly.get("_default", []))
        return steps

    def describe_culprits(self, result, limit=3):
        """['coolant temp +4.2 sigma', ...] in plain words."""
        out = []
        for feature, z in ((result or {}).get("culprits", []) or [])[:limit]:
            sign = "+" if z >= 0 else ""
            out.append(self.plain_name(feature) + " " + sign + str(z)
                       + " sigma from normal")
        return out
