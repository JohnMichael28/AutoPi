"""Getting a report OFF the Pi: to your phone (QR code) or a USB stick.

ReportServer  a tiny read-only web server that serves ONLY the reports/
              folder, on the Pi's own network address. Scan the QR code on
              the screen with your phone camera -> the report opens in the
              browser -> Share / Print / Save as PDF -> text or email it to
              the mechanic. Guardrails: serves nothing outside reports/, no
              directory listing, read-only (GET/HEAD only), and it shuts
              itself off after `lifetime_s` (default 15 min).
              The phone must be on the same network as the Pi (home WiFi or
              the phone's hotspot that the Pi joins in the car).
copy_to_usb() copies the files to every USB stick mounted under /media.
qr_matrix()   the QR code as a list of rows of booleans (needs the `qrcode`
              package: pip install qrcode). None if it isn't installed.
"""
import functools
import os
import shutil
import socket
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
