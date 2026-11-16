"""Getting a report OFF the Pi: to ANY phone (QR code) or a USB stick.

Three ways, so it works for anyone, anywhere, with no setup on the phone:
  1. AUTOPI WIFI  - the Pi turns its own WiFi into a private hotspot
                    (random password each time). QR #1 joins it (every
                    iPhone/Android camera understands WiFi QR codes), QR #2
                    opens the report. No internet, no shared network needed.
  2. SAME WIFI    - if the Pi and the phone are already on one network.
  3. TEXT QR      - the summary itself is inside the QR code: the camera
                    shows the text. Works with no WiFi at all.

ReportServer  a tiny read-only web server that serves ONLY the reports/
              folder, on AutoPi's own network address. Scan the QR code on
              the screen with your phone camera -> the report opens in the
              browser -> Share / Print / Save as PDF -> text or email it to
              the mechanic. Guardrails: serves nothing outside reports/, no
              directory listing, read-only (GET/HEAD only), and it shuts
              itself off after `lifetime_s` (default 15 min).
              The phone must be on the same network as the Pi (home WiFi or
              the phone's hotspot that AutoPi joins in the car).
copy_to_usb() copies the files to every USB stick mounted under /media.
qr_matrix()   the QR code as a list of rows of booleans (needs the `qrcode`
              package: pip install qrcode). None if it isn't installed.
"""
import functools
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _ReportsOnlyHandler(SimpleHTTPRequestHandler):
    def list_directory(self, path):          # no browsing the folder
        self.send_error(403, "No directory listing")
        return None

    def do_POST(self):
        self.send_error(405)

    do_PUT = do_POST
    do_DELETE = do_POST

    def log_message(self, fmt, *args):       # keep the console quiet
        pass


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is sent
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class ReportServer:
    def __init__(self, folder="reports", port=8088, lifetime_s=900):
        self.folder = os.path.abspath(folder)
        self.port = port
        self.lifetime_s = lifetime_s
        self._server = None
        self._stop_at = 0.0

    def running(self):
        return self._server is not None

    def start(self):
        """Start (or extend) serving. Returns the base URL."""
        os.makedirs(self.folder, exist_ok=True)
        self._stop_at = time.monotonic() + self.lifetime_s
        if self._server is None:
            handler = functools.partial(_ReportsOnlyHandler, directory=self.folder)
            self._server = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
            threading.Thread(target=self._watchdog, daemon=True).start()
        return "http://" + local_ip() + ":" + str(self.port) + "/"

    def url_for(self, path):
        return self.start() + os.path.basename(path)

    def _watchdog(self):
        while self._server is not None:
            if time.monotonic() >= self._stop_at:
                self.stop()
                return
            time.sleep(5)

    def stop(self):
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass


def usb_mounts(base="/media"):
    """Writable USB mount points (Raspberry Pi OS mounts sticks at
    /media/<user>/<label>)."""
    found = []
    if not os.path.isdir(base):
        return found
    for user in os.listdir(base):
        upath = os.path.join(base, user)
        if not os.path.isdir(upath):
            continue
        for label in os.listdir(upath):
            mpath = os.path.join(upath, label)
            if os.path.ismount(mpath) and os.access(mpath, os.W_OK):
                found.append(mpath)
    return found


def copy_to_usb(paths, base="/media"):
    """Copy files to every mounted USB stick. Returns list of destinations."""
    copied = []
    for mount in usb_mounts(base):
        dest = os.path.join(mount, "AutoPi_reports")
        try:
            os.makedirs(dest, exist_ok=True)
            for p in paths:
                shutil.copy2(p, dest)
            copied.append(dest)
        except Exception:
            pass
    return copied


def qr_matrix(text):
    try:
        import qrcode
    except Exception:
        return None
    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def iface_ip(iface):
    """IPv4 address of one interface (e.g. wlan0 while it is the hotspot)."""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", iface],
                             capture_output=True, text=True, timeout=3).stdout
        for part in out.split():
            if "/" in part and part.count(".") == 3:
                return part.split("/")[0]
    except Exception:
        pass
    return None


def wifi_qr_text(ssid, password):
    """Standard WiFi QR payload (scan with any phone camera to join)."""
    def esc(v):
        for ch in ("\\", ";", ",", ":", '"'):
            v = v.replace(ch, "\\" + ch)
        return v
    return "WIFI:T:WPA;S:" + esc(ssid) + ";P:" + esc(password) + ";;"


class PiHotspot:
    """Turns the Pi's WiFi into a private access point with NetworkManager
    (nmcli), then puts the previous WiFi connection back when done.

    Guardrails: WPA2 with a fresh random password every time, only the
    reports folder is served on it, and it is switched off by stop() (the UI
    calls it on DONE and when the share timer runs out). Needs permission to
    run nmcli: as the logged-in desktop user it usually just works; on a
    console-only setup add the one-line sudoers rule from the README."""

    def __init__(self, iface="wlan0", con_name="AutoPi-Share", run=None):
        self.iface = iface
        self.con_name = con_name
        self._run = run or self._default_run
        self._previous = None
        self.active = False
        self.ssid = None
        self.password = None

    @staticmethod
    def _default_run(args):
        p = subprocess.run(args, capture_output=True, text=True, timeout=25)
        return p.returncode, (p.stdout + p.stderr).strip()

    def _nmcli(self, args):
        code, out = self._run(["nmcli"] + args)
        if code != 0 and ("not authorized" in out.lower() or "permission" in out.lower()
                          or "insufficient privileges" in out.lower()):
            code, out = self._run(["sudo", "-n", "nmcli"] + args)
        return code, out

    def _active_wifi(self):
        code, out = self._nmcli(["-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active"])
        if code != 0:
            return None
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2] == self.iface and "wireless" in parts[1] \
                    and parts[0] != self.con_name:
                return parts[0]
        return None

    def start(self, prefix="AutoPi"):
        """-> (ok, message)."""
        self._previous = self._active_wifi()
        self.ssid = prefix + "-" + "%04d" % (secrets.randbelow(10000))
        self.password = secrets.token_hex(5)          # 10 chars, WPA2 needs 8+
        code, out = self._nmcli(["dev", "wifi", "hotspot", "ifname", self.iface,
                                 "con-name", self.con_name, "ssid", self.ssid,
                                 "password", self.password])
        if code != 0:
            self.restore()
            return False, out[:120] or "nmcli failed"
        self.active = True
        return True, "hotspot " + self.ssid + " on"

    def ip(self):
        return iface_ip(self.iface) or "10.42.0.1"   # NetworkManager's default

    def restore(self):
        if self._previous:
            self._nmcli(["con", "up", "id", self._previous])

    def stop(self):
        if self.active:
            self._nmcli(["con", "down", self.con_name])
            self._nmcli(["con", "delete", self.con_name])
            self.active = False
        self.restore()
        self._previous = None
