import obd


class WarningChecker:
    """Encapsulates threshold-based warnings (hot engine, low battery,
    long idle). Thresholds come from a Config object (dependency injection),
    so tuning them means editing config.json, not this class."""

    def __init__(self, config):
        # Pull thresholds from the Config object once
        self.__coolant_warn_c = config.coolant_warn_c
        self.__idle_warn_seconds = config.idle_warn_seconds
        self.__voltage_warn = config.voltage_warn

    def check(self, vehicle):
        # Returns a list of warning strings based on the car's current state.
        warnings = []

        coolant = vehicle.read_number(obd.commands.COOLANT_TEMP)
        if coolant is not None and coolant >= self.__coolant_warn_c:
            warnings.append("!! ENGINE HOT - let it rest / turn off soon")

        voltage = vehicle.read_number(obd.commands.CONTROL_MODULE_VOLTAGE)
        if voltage is not None and voltage <= self.__voltage_warn:
            warnings.append("!! LOW BATTERY - risk of draining")

        runtime = vehicle.read_number(obd.commands.RUN_TIME)
        if runtime is not None and runtime >= self.__idle_warn_seconds:
            warnings.append("!! LONG IDLE - consider shutting off for a bit")

        return warnings