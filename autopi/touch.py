"""Direct touchscreen reader via evdev. Bypasses SDL's flaky KMSDRM touch
support by reading /dev/input/event* and mapping raw controller coordinates to
screen pixels. Non-blocking: poll() drains pending events each frame.

TAP TIMING (the important part): this panel (wch.cn USB2IIC_CTP_CONTROL) reports
a FROZEN placeholder coordinate for the first ~19 samples of a touch, then
streams the real finger position. So the BTN_TOUCH 'down' event arrives with
STALE coordinates - the last touch's position, or an init value - which made
every tap register in the wrong place ('jumps to whatever it wants'). Measured
with a raw stream probe: a drag showed coords frozen at (107,179) for 19 samples
before tracking the finger.

FIX: a tap is emitted on touch-UP using the SETTLED coordinates (by the time the
finger lifts, the panel has reported the true position). We still expose 'down'
for anything that needs press feedback, but the actionable event the UI treats
as a tap is the 'up', which always carries accurate coordinates. Axis handling
(swap/invert/scale) is config-driven but this panel needs none of it - the raw
mapping tracks the finger correctly once the coordinates settle."""
import evdev
from evdev import ecodes


class TouchReader:
    def __init__(self, device_path, screen_w, screen_h, calib=None):
        self.screen_w = screen_w
        self.screen_h = screen_h
        calib = calib or {}
        self._swap_xy = bool(calib.get("swap_xy", False))
        self._invert_x = bool(calib.get("invert_x", False))
        self._invert_y = bool(calib.get("invert_y", False))
        self._x_scale = float(calib.get("x_scale", 1.0))
        self._y_scale = float(calib.get("y_scale", 1.0))
        self._x_offset = float(calib.get("x_offset", 0.0))
        self._y_offset = float(calib.get("y_offset", 0.0))
        self.dev = self._find_touch_device(device_path)
        caps = self.dev.capabilities()
        abs_info = dict(caps.get(ecodes.EV_ABS, []))
        x_info = abs_info[ecodes.ABS_X]
        y_info = abs_info[ecodes.ABS_Y]
        self._x_min, self._x_max = x_info.min, x_info.max
        self._y_min, self._y_max = y_info.min, y_info.max
        self.dev.grab()
        self._raw_x = 0
        self._raw_y = 0
        self._x = 0
        self._y = 0
        self._down = False
        self._samples_since_down = 0    # how many ABS updates since touch began

    @staticmethod
    def _find_touch_device(fallback_path):
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

    def _compute(self):
        rx = (self._raw_x - self._x_min) / ((self._x_max - self._x_min) or 1)
        ry = (self._raw_y - self._y_min) / ((self._y_max - self._y_min) or 1)
        if self._swap_xy:
            rx, ry = ry, rx
        if self._invert_x:
            rx = 1.0 - rx
        if self._invert_y:
            ry = 1.0 - ry
        px = rx * self.screen_w * self._x_scale + self._x_offset
        py = ry * self.screen_h * self._y_scale + self._y_offset
        self._x = int(max(0, min(px, self.screen_w - 1)))
        self._y = int(max(0, min(py, self.screen_h - 1)))

    def poll(self):
        """Drain pending events. Returns list of ('down'|'up', (x, y)).

        The UI should ACT on 'up' events - they carry the settled, accurate
        coordinates. 'down' is emitted too (for press feedback) but its
        coordinates can be stale on this panel, so don't select menu items on
        'down'."""
        out = []
        pending_down = False
        pending_up = False
        while True:
            event = self.dev.read_one()
            if event is None:
                break
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    self._raw_x = event.value
                    self._samples_since_down += 1
                elif event.code == ecodes.ABS_Y:
                    self._raw_y = event.value
                    self._samples_since_down += 1
            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:
                    pending_down = True
                    self._samples_since_down = 0
                elif event.value == 0:
                    pending_up = True
            elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                self._compute()
                if pending_down:
                    self._down = True
                    out.append(("down", (self._x, self._y)))
                    pending_down = False
                if pending_up:
                    self._down = False
                    # 'up' uses the settled coordinates - accurate landing spot.
                    out.append(("up", (self._x, self._y)))
                    pending_up = False
        return out

    def close(self):
        try:
            self.dev.ungrab()
        except Exception:
            pass