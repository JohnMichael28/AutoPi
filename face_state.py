class FaceState:
    """Decides the Face's mood from the car's actual state.
    Priority order matters: critical danger overrides everything,
    then warnings, then the current driving mode, then all-good.
    Pure logic (Model) - testable without a car."""

    def decide(self, coolant_temp=None, has_codes=False, voltage=None,
               mode="highway", critical=False, needs_gas=False,
               info_flag=False):
        # --- Highest priority: genuine danger ---
        if critical:
            return "critical"
        # Overheating is critical
        if coolant_temp is not None and coolant_temp >= 115:
            return "critical"

        # --- Warnings (something wrong) ---
        if has_codes:
            return "red"
        if coolant_temp is not None and coolant_temp >= 105:
            return "yellow"
        if voltage is not None and voltage <= 12.2:
            return "yellow"

        # --- Informational (blue) ---
        if needs_gas or info_flag:
            return "blue"

        # --- Mode-based coloring (when all is well) ---
        if mode == "adventure":
            return "orange"
        if mode == "camp" or mode == "idle":
            return "purple"
        if mode == "track":
            return "turquoise"

        # --- Default: all good ---
        return "green"