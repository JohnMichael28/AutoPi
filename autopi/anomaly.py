import sqlite3
import numpy as np
from sklearn.ensemble import IsolationForest
import joblib


class AnomalyDetector:
    """Encapsulates the ML anomaly detection. Wraps training, saving,
    and live checking. The rolling buffer is an instance attribute
    (not a global), so each detector manages its own state."""

    # The raw sensor columns - MUST match Reading.to_feature_list() order
    FEATURES = ["coolant_temp", "intake_temp", "boost_psi", "rpm",
                "speed", "engine_load", "throttle", "voltage"]

    def __init__(self, db_file="autopi_log.db", model_file="anomaly_model.pkl",
                 window=6):
        self.__db_file = db_file
        self.__model_file = model_file
        self.window = window
        self.__model = None          # loaded lazily
        self.__buffer = []           # rolling buffer (instance attribute, not global)

    # ---------- data loading ----------
    def load_readings(self):
        conn = sqlite3.connect(self.__db_file)
        cursor = conn.cursor()
        columns = ", ".join(self.FEATURES)
        cursor.execute("SELECT " + columns + " FROM readings ORDER BY id ASC;")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def clean_rows(self, rows):
        clean = []
        for row in rows:
            if None not in row:
                clean.append(row)
        return np.array(clean)

    def make_window_features(self, data):
        # Rolling mean/std/max over each window - captures trends, not snapshots
        feature_rows = []
        for i in range(self.window, len(data)):
            w = data[i - self.window:i]
            means = w.mean(axis=0)
            stds = w.std(axis=0)
            maxes = w.max(axis=0)
            feature_rows.append(np.concatenate([means, stds, maxes]))
        return np.array(feature_rows)

    # ---------- training ----------
    def train(self):
        rows = self.load_readings()
        data = self.clean_rows(rows)
        print("Loaded", len(data), "clean readings.")
        if len(data) < self.window + 10:
            print("Not enough data yet to train. Need at least", self.window + 10)
            return None
        features = self.make_window_features(data)
        print("Built", len(features), "rolling-window feature rows.")
        model = IsolationForest(contamination=0.05, random_state=42)
        model.fit(features)
        joblib.dump(model, self.__model_file)
        self.__model = model
        print("Model trained and saved to", self.__model_file)
        return model

    # ---------- live checking ----------
    def __ensure_model(self):
        # Private: load the model from disk if not already in memory
        if self.__model is None:
            self.__model = joblib.load(self.__model_file)
        return self.__model

    def add_reading(self, feature_list):
        # Add one reading's features to the rolling buffer (keeps last `window`)
        if None in feature_list:
            return None                 # skip incomplete
        self.__buffer.append(feature_list)
        if len(self.__buffer) > self.window:
            self.__buffer.pop(0)        # drop oldest, fixed-size buffer
        if len(self.__buffer) == self.window:
            return self.check()
        return None                     # buffer not full yet

    def check(self):
        # Judge the current buffer as normal or anomalous
        try:
            model = self.__ensure_model()
            window = np.array(self.__buffer)
            means = window.mean(axis=0)
            stds = window.std(axis=0)
            maxes = window.max(axis=0)
            combined = np.concatenate([means, stds, maxes])
            result = model.predict([combined])
            return "ANOMALY" if result[0] == -1 else "normal"
        except Exception as e:
            return "ML error: " + str(e)

    def count_anomalies(self):
        # For the report: run all logged readings through the model, count -1s
        try:
            model = self.__ensure_model()
        except Exception as e:
            return None, "ML model not available: " + str(e)
        data = self.clean_rows(self.load_readings())
        if len(data) < self.window + 1:
            return 0, "Not enough data for anomaly analysis"
        features = self.make_window_features(data)
        predictions = model.predict(features)
        return int((predictions == -1).sum()), None