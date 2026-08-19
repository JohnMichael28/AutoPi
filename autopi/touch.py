"""Direct touchscreen reader via evdev. Bypasses SDL's flaky KMSDRM
touch support by reading /dev/input/event* and mapping raw controller
coordinates to screen pixels. Non-blocking: poll() drains pending events
each frame. O(k) per frame where k = events waiting (usually 0-2).

Auto-finds the touch device by capability rather than a hardcoded event
number, because Linux assigns /dev/input/eventN by USB enumeration order,
which shifts when devices are plugged in a different order."""
import evdev
from evdev import ecodes


class TouchReader:
    def __init__(self, device_path, screen_w, screen_h):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.dev = self._find_touch_device(device_path)
        # Read the controller's real ABS range so we scale correctly
        caps = self.dev.capabilities()
        abs_info = dict(caps.get(ecodes.EV_ABS, []))
        x_info = abs_info[ecodes.ABS_X]
        y_info = abs_info[ecodes.ABS_Y]
        self._x_min, self._x_max = x_info.min, x_info.max
        self._y_min, self._y_max = y_info.min, y_info.max
        self.dev.grab()             # take exclusive control
        self._x = 0
        self._y = 0
        self._down = False

    @staticmethod
    def _find_touch_device(fallback_path):
        """Return the first device that reports touch capability (EV_ABS with
        ABS_X + BTN_TOUCH). Robust to shifting event numbers. Falls back to the
        configured path if nothing matches. O(d) over available devices."""
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                has_abs = ecodes.EV_ABS in caps
                has_touch = (ecodes.EV_KEY in caps
                             and ecodes.BTN_TOUCH in caps[ecodes.EV_KEY])
                if has_abs and has_touch:
                    print("Touch device found:", path, dev.name)
                    return dev
                dev.close()
            except Exception:
                continue
        print("No touch device auto-found, using fallback:", fallback_path)
        return evdev.InputDevice(fallback_path)

    def _scale_x(self, raw):
        span = self._x_max - self._x_min or 1
        return int((raw - self._x_min) / span * self.screen_w)

    def _scale_y(self, raw):
        span = self._y_max - self._y_min or 1
        return int((raw - self._y_min) / span * self.screen_h)

    def poll(self):
        """Drain pending events. Returns list of ('down'|'up', (x, y)).
        Position is read from the same event batch as the touch, so a tap
        always reports where the finger actually landed - not the previous
        tap's location."""
        out = []
        pending_down = False
        pending_up = False
        while True:
            event = self.dev.read_one()
            if event is None:
                break
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    self._x = self._scale_x(event.value)
                elif event.code == ecodes.ABS_Y:
                    self._y = self._scale_y(event.value)
            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:
                    pending_down = True
                elif event.value == 0:
                    pending_up = True
            elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                if pending_down:
                    self._down = True
                    out.append(("down", (self._x, self._y)))
                    pending_down = False
                if pending_up:
                    self._down = False
                    out.append(("up", (self._x, self._y)))
                    pending_up = False
        return out

    def close(self):
        try:
            self.dev.ungrab()
        except Exception:
            pass