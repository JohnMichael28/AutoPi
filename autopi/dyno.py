class DynoCalculator:
    """Encapsulates virtual-dyno physics. Pure calculation (Model) - it
    takes speed/time samples and estimates horsepower. Separated from any
    display so the physics can be unit-tested on its own."""

    def __init__(self, config, weight_kg=1770, drag_coeff=0.35,
                 frontal_area=2.6, air_density=1.2, rolling_coeff=0.015):
        self.weight_kg = weight_kg
        self.drag_coeff = drag_coeff
        self.frontal_area = frontal_area
        self.air_density = air_density
        self.rolling_coeff = rolling_coeff
        self.__calibration = config.dyno_calibration   # from config
        self.__peak_hp = 0.0

    def estimate_hp(self, speed_ms, accel):
        # Given current speed (m/s) and acceleration (m/s^2), estimate HP.
        # Accounts for acceleration force + aero drag + rolling resistance.
        # Returns None if not actually accelerating forward.
        if accel <= 0 or speed_ms <= 0:
            return None

        f_accel = self.weight_kg * accel
        f_drag = (0.5 * self.air_density * self.drag_coeff
                  * self.frontal_area * speed_ms ** 2)
        f_roll = self.rolling_coeff * self.weight_kg * 9.81
        total_force = f_accel + f_drag + f_roll

        power_watts = total_force * speed_ms
        hp = (power_watts / 745.7) * self.__calibration

        if hp > self.__peak_hp:
            self.__peak_hp = hp
        return hp

    @property
    def peak_hp(self):
        return self.__peak_hp

    @property
    def calibration(self):
        return self.__calibration

    def reset(self):
        # Clear peak for a fresh run
        self.__peak_hp = 0.0

    @staticmethod
    def kmh_to_ms(kmh):
        # Convert km/h to m/s (helper for the physics)
        if kmh is None:
            return None
        return kmh * 1000.0 / 3600.0

    @staticmethod
    def ms_to_mph(ms):
        # Convert m/s to mph (for display)
        return ms * 2.237