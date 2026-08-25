"""Persists warnings with timestamps so the driver can review exactly what
happened and when, at a safe stop - a warning that only shows live is useless
if you're driving and can't read it in the moment.

Each warning is recorded ONCE per continuous episode (not every cycle it's
active), so a coolant warning that lasts two minutes is one logged entry with a
start time, not hundreds of duplicate rows. Written to a CSV that survives
restarts, so a whole drive's history is there when you park.
"""
import os
import csv
import time


class WarningLog:
    def __init__(self, path="data/warning_log.csv"):
        self.__path = path
        self.__active = {}      # warning text -> first-seen timestamp (dedupe)
        self.__session = []     # this run's entries, newest first, for display
        self.__ensure_file()

    def __ensure_file(self):
        directory = os.path.dirname(self.__path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self.__path):
            with open(self.__path, "w", newline="") as f:
                csv.writer(f).writerow(["timestamp", "warning"])

    def record(self, warnings):
        """Given the CURRENT list of active warnings, log any that are newly
        active (weren't active last cycle). Clears entries that have gone away
        so they can re-log if they happen again later. Call every cycle."""
        current = set(warnings)
        # New warnings (active now, weren't before) get logged with a timestamp.
        for w in warnings:
            if w not in self.__active:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                self.__active[w] = stamp
                self.__session.insert(0, (stamp, w))   # newest first
                try:
                    with open(self.__path, "a", newline="") as f:
                        csv.writer(f).writerow([stamp, w])
                except Exception:
                    pass        # logging must never crash the app
        # Drop ones that are no longer active, so a later recurrence re-logs.
        for w in list(self.__active.keys()):
            if w not in current:
                del self.__active[w]

    def session_entries(self):
        """This run's warnings, newest first, as (timestamp, warning) tuples -
        for the on-screen review list."""
        return list(self.__session)

    def clear_session(self):
        """Clear the on-screen list (does NOT erase the CSV history)."""
        self.__session = []
        self.__active = {}