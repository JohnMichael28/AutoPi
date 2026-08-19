import sqlite3
import time


class Logger:
    """Encapsulates the SQLite database. All reading/event storage and
    retrieval goes through this class - the db file and connections are
    managed internally (encapsulation). Uses WAL mode for SD-card safety."""

    def __init__(self, db_file="autopi_log.db"):
        self.__db_file = db_file        # private
        self.setup()

    def __connect(self):
        # Private: open a connection in WAL mode (crash-safe, SD-friendly)
        conn = sqlite3.connect(self.__db_file)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def setup(self):
        # Create tables if they don't exist yet
        conn = self.__connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY,
                timestamp TEXT, vin TEXT,
                coolant_temp REAL, intake_temp REAL, boost_psi REAL,
                rpm REAL, speed REAL, engine_load REAL,
                throttle REAL, voltage REAL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON readings (timestamp);")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                timestamp TEXT, vin TEXT, code TEXT,
                description TEXT, explanation TEXT, outcome TEXT
            );
        """)
        conn.commit()
        conn.close()

    def log_readings(self, readings):
        # readings: a list of Reading objects. Written in ONE transaction (batched).
        conn = self.__connect()
        cursor = conn.cursor()
        cursor.execute("BEGIN;")
        for r in readings:
            d = r.to_dict()
            cursor.execute("""
                INSERT INTO readings
                (timestamp, vin, coolant_temp, intake_temp, boost_psi,
                 rpm, speed, engine_load, throttle, voltage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                d["timestamp"], d["vin"], d["coolant_temp"], d["intake_temp"],
                d["boost_psi"], d["rpm"], d["speed"], d["engine_load"],
                d["throttle"], d["voltage"],
            ))
        conn.commit()
        conn.close()

    def safe_log_readings(self, readings):
        # Crash-safe wrapper: a logging failure never crashes the app
        try:
            self.log_readings(readings)
        except Exception as e:
            print("logging broke: ", e)

    def log_event(self, event):
        # event: an Event object. Outcome starts empty, filled in later.
        conn = self.__connect()
        conn.execute(
            "INSERT INTO events (timestamp, vin, code, description, explanation, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (event.timestamp, event.vin, event.code, event.description,
             event.explanation, event.outcome)
        )
        conn.commit()
        conn.close()

    def get_events(self):
        # All events, for the report
        conn = self.__connect()
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, code, description, explanation, outcome FROM events;")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_reading_summary(self):
        # Let the DATABASE compute min/max/count (memory-efficient - no bulk load)
        conn = self.__connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*),
                   MIN(coolant_temp), MAX(coolant_temp),
                   MIN(voltage), MAX(voltage),
                   MAX(rpm), MAX(boost_psi)
            FROM readings;
        """)
        row = cursor.fetchone()
        conn.close()
        return row

    def get_unresolved(self):
        # Codes with no outcome yet (for confirm-fix). Filter at the DB.
        conn = self.__connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, code, description FROM events WHERE outcome = '';")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def save_outcome(self, event_id, outcome):
        # Fill in the fix outcome for one event, by id (fast - primary key)
        conn = self.__connect()
        conn.execute("UPDATE events SET outcome = ? WHERE id = ?;", (outcome, event_id))
        conn.commit()
        conn.close()