"""Load the raw driving_log CSV into SQLite, run features.sql (the CS 3312
Transformation stage), and return a clean pandas DataFrame ready for training.

Keeping the transform in SQL (not pandas) is the point: it's the auditable
Cleaning->Transformation stage of the course pipeline, and the exact same .sql
could later run against a live DB instead of a CSV with no code change."""
import sqlite3
import pandas as pd
import os


def build_features(csv_path, sql_path=None, keep_db=False):
    if sql_path is None:
        sql_path = os.path.join(os.path.dirname(__file__), "features.sql")
    raw = pd.read_csv(csv_path, on_bad_lines="skip")
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