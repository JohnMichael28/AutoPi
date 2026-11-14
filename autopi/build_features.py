"""Load the raw driving_log CSV into SQLite, run features.sql (the CS 3312
Transformation stage), and return a clean pandas DataFrame ready for training.

Keeping the transform in SQL (not pandas) is the point: it's the auditable
Cleaning->Transformation stage of the course pipeline, and the exact same .sql
could later run against a live DB instead of a CSV with no code change."""
import sqlite3
import pandas as pd
import os


def expand_paths(paths):
    """Turn files and folders into one list of CSV files. A folder means
    every driving_log*.csv inside it (e.g. data/archive/ after the monthly
    roll), so training can use this month plus the archived months."""
    if isinstance(paths, str):
        paths = [paths]
    out = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.startswith("driving_log") and name.endswith(".csv"):
                    out.append(os.path.join(p, name))
        elif os.path.exists(p):
            out.append(p)
    return out


def load_raw(paths):
    """Read one or many driving logs into one table, oldest row first."""
    frames = [pd.read_csv(p, on_bad_lines="skip") for p in expand_paths(paths)]
    if not frames:
        raise FileNotFoundError("no driving_log CSV files found in " + str(paths))
    raw = pd.concat(frames, ignore_index=True)
    if "timestamp" in raw.columns:
        raw = raw.sort_values("timestamp", kind="stable").reset_index(drop=True)
    return raw


def build_features(csv_path, sql_path=None, keep_db=False):
    # csv_path: one CSV, a folder, or a list of them (see expand_paths).
    if sql_path is None:
        sql_path = os.path.join(os.path.dirname(__file__), "features.sql")
    raw = load_raw(csv_path)
    # Empty CSV cells -> NULL in SQLite (pandas reads them as NaN, to_sql maps
    # NaN->NULL). This is what makes the forward-fill in SQL work.
    con = sqlite3.connect(":memory:" if not keep_db else "features.db")
    raw.to_sql("raw_log", con, if_exists="replace", index=False)
    with open(sql_path) as f:
        sql = f.read()
    feats = pd.read_sql_query(sql, con)
    con.close()
    return feats


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "driving_log_sim.csv"
    df = build_features(path)
    print("raw rows:", len(pd.read_csv(path)))
    print("feature rows:", len(df))
    print("feature columns:", list(df.columns))
    print(df.head(3).to_string())