class FaceState:
    """Decides the Face's mood from the car's actual state.
    Priority: critical danger > warnings > drive mode > all-good.
    Pure logic (Model) - testable without a car."""

    @staticmethod
    def _num(value):
        # Treat "--" / None / any non-number as "no reading" (returns None).
        # Keeps the decider safe when a sensor is missing or the car is off.
        if isinstance(value, (int, float)):
            return value
        return None

    def decide(self, coolant_temp=None, has_codes=False, voltage=None,
               mode="highway", critical=False, needs_gas=False,
               info_flag=False, fuel_flag=None):
        coolant_temp = self._num(coolant_temp)
        voltage = self._num(voltage)

        if critical:
            return "critical"
        if coolant_temp is not None and coolant_temp >= 115:
            return "critical"
        if has_codes:
            return "red"
        if fuel_flag == "bad":
            return "red"          # fuel trims way off = real problem
        if coolant_temp is not None and coolant_temp >= 105:
            return "yellow"
        if voltage is not None and voltage <= 12.2:
            return "yellow"
        if fuel_flag == "warn":
            return "yellow"       # trims drifting = worth watching
        if needs_gas or info_flag:
            return "blue"
        if mode == "adventure":
            return "orange"
        if mode == "camp" or mode == "idle":
            return "purple"
        if mode == "track":
            return "turquoise"
        return "green"