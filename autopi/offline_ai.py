import json
import os


class OfflineExplainer:
    """Tier 3 of the AI ladder: works with NO connection. A built-in
    dictionary of common OBD codes gives instant basic explanations, and
    an offline queue holds codes for richer AI explanation when the device
    reconnects. This is the 'graceful degradation' that makes the device
    genuinely standalone (sourced: tiered edge-AI architecture pattern)."""

    # Built-in explanations for the most common generic OBD-II codes.
    # (Generic P0/P2 codes are standardized across all cars.)
    CODE_DICT = {
        "P0300": "Random or multiple cylinder misfire. Often spark plugs, "
                 "coils, or fuel delivery. Drive gently until fixed.",
        "P0301": "Cylinder 1 misfire. Usually a spark plug or ignition coil "
                 "in that cylinder. Protect the catalytic converter - go easy.",
        "P0302": "Cylinder 2 misfire. Usually a spark plug or ignition coil.",
        "P0303": "Cylinder 3 misfire. Usually a spark plug or ignition coil.",
        "P0304": "Cylinder 4 misfire. Usually a spark plug or ignition coil.",
        "P0171": "System too lean (bank 1). Possible vacuum leak, weak fuel "
                 "pump, or dirty MAF sensor.",
        "P0172": "System too rich (bank 1). Possible leaking injector, bad "
                 "MAF reading, or fuel pressure issue.",
        "P0420": "Catalyst efficiency below threshold (bank 1). Often a "
                 "failing catalytic converter or an O2 sensor.",
        "P0455": "Large evaporative emissions leak. Frequently a loose or "
                 "faulty gas cap - check that first.",
        "P0456": "Small evaporative emissions leak. Often a gas cap seal or "
                 "a small EVAP hose leak.",
        "P0128": "Coolant thermostat below regulating temperature. Usually a "
                 "stuck-open thermostat.",
        "P0442": "Small EVAP system leak. Check the gas cap and EVAP hoses.",
        "P0113": "Intake air temp sensor high input. Possible sensor or "
                 "wiring fault.",
        "P0507": "Idle RPM higher than expected. Possible vacuum leak or "
                 "dirty throttle body.",
    }

    def __init__(self, queue_file="ai_queue.json"):
        self._queue_file = queue_file
        self._queue = self._load_queue()

    def _load_queue(self):
        if os.path.exists(self._queue_file):
            try:
                with open(self._queue_file, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_queue(self):
        try:
            with open(self._queue_file, "w") as f:
                json.dump(self._queue, f)
        except Exception:
            pass

    def explain(self, code):
        # Instant offline explanation from the dictionary, or a safe default.
        code = code.upper().strip()
        if code in self.CODE_DICT:
            return self.CODE_DICT[code]
        return ("Code " + code + " detected. No offline description available. "
                "Queued for a full AI explanation when reconnected.")

    def has_offline(self, code):
        return code.upper().strip() in self.CODE_DICT

    def queue_for_ai(self, code):
        # Save a code to explain richly later (when the good AI is reachable)
        code = code.upper().strip()
        if code not in self._queue:
            self._queue.append(code)
            self._save_queue()

    def get_queue(self):
        return list(self._queue)

    def clear_queue(self):
        self._queue = []
        self._save_queue()