import obd


class Capabilities:
    """Detects what a given car actually supports (PIDs vary car-to-car,
    and manufacturer features like boost aren't universal). Queries the
    car's supported_commands ONCE on connect, caches it, and answers
    'can this car do X?' so the UI adapts to any vehicle.

    Sourced: python-OBD exposes connection.supported_commands (the set the
    car reported) and has_command() - the standard capability-check pattern."""

    def __init__(self, vehicle):
        self._vehicle = vehicle
        self._supported = set()      # names of supported PIDs
        self._checked = False

    def detect(self):
        # Ask the car what it supports (once). Falls back gracefully if the
        # connection can't report (older adapters).
        conn = self._vehicle.connection   # the underlying obd.OBD
        if conn is None:
            self._checked = True
            return
        try:
            for cmd in conn.supported_commands:
                self._supported.add(cmd.name)
        except Exception:
            pass
        self._checked = True

    def supports(self, pid_name):
        # True if the car exposes this PID
        return pid_name in self._supported

    def has_boost(self):
        # Boost needs manifold pressure (not all cars expose it)
        return self.supports("INTAKE_PRESSURE")

    def has_oil_temp(self):
        return self.supports("OIL_TEMP")

    def count(self):
        return len(self._supported)

    def all_supported(self):
        return sorted(self._supported)

    def pick_stat(self, preferred, fallback_chain):
        # Return the first supported stat from a preference list.
        # Lets a mode bar say "boost if available, else manifold, else load"
        if self.supports(preferred):
            return preferred
        for alt in fallback_chain:
            if self.supports(alt):
                return alt
        return None