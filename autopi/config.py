import json


class Config:
    """Loads and provides access to tunable settings from config.json.
    Encapsulates the config file so the rest of the app never reads it directly."""

    def __init__(self, path="config.json"):
        self.__path = path              # private: where the file lives
        self.__data = {}                # private: the loaded settings
        self.__load()

    def __load(self):
        # Private method - reads the JSON file into memory once
        with open(self.__path) as f:
            self.__data = json.load(f)

    def get(self, key, default=None):
        # Safe access with a fallback default
        return self.__data.get(key, default)

    # --- Properties: clean, named access to each setting ---
    @property
    def obd_port(self):
        return self.__data.get("obd_port", "/dev/ttyUSB0")
    
    @property
    def coolant_warn_c(self):
        return self.__data.get("coolant_warn_c", 105)

    @property
    def idle_warn_seconds(self):
        return self.__data.get("idle_warn_seconds", 1800)

    @property
    def voltage_warn(self):
        return self.__data.get("voltage_warn", 12.2)

    @property
    def log_every(self):
        return self.__data.get("log_every", 5)

    @property
    def dyno_calibration(self):
        return self.__data.get("dyno_calibration", 1.0)

    @property
    def ollama_ip(self):
        return self.__data.get("ollama_ip", "192.168.68.108")