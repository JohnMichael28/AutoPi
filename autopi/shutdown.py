import time


class ShutdownWatcher:
    """Detects ignition-off and triggers a clean shutdown before car power
    fully dies (protects the SD card - no battery/UPS needed).

    Two signals: OBD connection lost, OR voltage sags (engine off).
    Requires the signal to persist for a few checks (debounce) so a brief
    glitch doesn't trigger a false shutdown. Pure logic - testable."""

    def __init__(self, voltage_floor=12.4, confirm_count=3):
        # voltage_floor: below this = likely ignition off (engine not charging)
        # confirm_count: how many consecutive bad readings before we act
        self.voltage_floor = voltage_floor
        self.confirm_count = confirm_count
        self._strikes = 0

    def check(self, voltage=None, obd_connected=True):
        # Returns True if a shutdown should happen NOW.
        ignition_off = False

        # Signal 1: OBD dropped entirely (car powered down the bus)
        if not obd_connected:
            ignition_off = True
        # Signal 2: voltage sagged (engine off, not charging)
        elif voltage is not None and voltage < self.voltage_floor:
            ignition_off = True

        if ignition_off:
            self._strikes += 1
        else:
            self._strikes = 0   # reset on any good reading (debounce)

        return self._strikes >= self.confirm_count

    def reset(self):
        self._strikes = 0