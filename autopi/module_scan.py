"""ModuleScanner: talk to EVERY module on the car's network, not just the engine.

Generic OBD-II (modes 01-0A) only reaches the engine/emissions computer. That
is why AutoPi missed the shop's DCM (Starlink telematics) fault: that code
lives in the DCM's own memory. Modern modules all speak UDS (ISO 14229) on
CAN, so AutoPi can ask each one directly through the OBDLink:

  discover()        which modules answer? (UDS 0x3E TesterPresent to each
                    request ID; the reply comes from request ID + 8)
  identify(mod)     name / part number / software (UDS 0x22 F197 F187 F195)
  read_dtcs(mod)    stored codes (UDS 0x19 02 FF) -> P/C/B/U codes + status
  clear_dtcs(mod)   clear THAT module's stored codes (UDS 0x14 FF FF FF)

Read-only except clear_dtcs, which the UI only allows behind the same
spam-proof confirm gate as engine codes. Nothing here unlocks security
access, codes modules or calibrates anything - that stays dealer-only.

Honest limits: Subaru does not publish which ID belongs to which module, so
modules are shown by ID plus whatever name the module reports about itself;
some modules may not answer a non-dealer tool at all.

`send` is a function(header_hex, payload_hex) -> [(reply_id_int, bytes)],
provided by Vehicle.uds_request (tests pass a fake).
"""
import json
import os
import time


SYSTEMS = {0: "P", 1: "C", 2: "B", 3: "U"}

STATUS_BITS = [            # UDS DTC status byte (ISO 14229-1)
    (0x01, "FAILING NOW"),
    (0x04, "PENDING"),
    (0x08, "CONFIRMED"),
    (0x80, "WARNING LIGHT ON"),
    (0x20, "FAILED SINCE CLEAR"),
]

NRC = {0x10: "general reject", 0x11: "service not supported",
       0x12: "sub-function not supported", 0x13: "bad message length",
       0x22: "conditions not correct (try key ON, engine off)",
       0x31: "request out of range", 0x33: "security access denied",
       0x78: "busy - response pending", 0x7E: "not supported in this session",
       0x7F: "service not supported in this session"}

# Request IDs every OBD-II CAN car uses for these two modules.
STANDARD_NAMES = {0x7E0: "ENGINE (ECM)", 0x7E1: "TRANSMISSION (TCM)"}


def decode_dtc(b0, b1, b2):
    """3 UDS bytes -> 'B2A16-00' (code + failure-type byte)."""
    letter = SYSTEMS[(b0 >> 6) & 0x03]
    first = str((b0 >> 4) & 0x03)
    code = letter + first + "%X" % (b0 & 0x0F) + "%02X" % b1
    return code + "-" + "%02X" % b2


def decode_status(status):
    words = [name for bit, name in STATUS_BITS if status & bit]
    return ", ".join(words) if words else "stored (inactive)"


class ModuleScanner:
    DEFAULTS = {
        "cache": "data/modules.json",
        # Quick discovery: the IDs most OEMs (incl. Subaru/Toyota-family CAN)
        # use for diagnostics. FULL SWEEP tries every ID 0x700-0x7F7.
        "candidates": ["7E0", "7E1", "7E2", "7E3", "7E4", "7E5", "7E6", "7E7",
                       "7B0", "7B1", "7B2", "7B6", "780", "781", "7A0", "7A1",
                       "7A2", "7C0", "7C4", "7C6", "7C8", "7D0", "7D1", "7D2",
                       "750", "752", "760", "770", "720", "730", "740", "744",
                       "746", "790", "7F0"],
        "sweep_from": "700",
        "sweep_to": "7F7",
    }

    def __init__(self, send, cfg=None, code_book=None):
        merged = dict(self.DEFAULTS)
        if cfg:
            merged.update(cfg)
        self._c = merged
        self._send = send
        self._codes = code_book or {}
        self.modules = self._load()

    # ---- cache of discovered modules ---------------------------------
    def _load(self):
        try:
            with open(self._c["cache"]) as f:
                mods = json.load(f)
            if isinstance(mods, list) and mods:
                return mods
        except Exception:
            pass
        return [{"req": "7E0", "name": STANDARD_NAMES[0x7E0], "seen": None},
                {"req": "7E1", "name": STANDARD_NAMES[0x7E1], "seen": None}]

    def _save(self):
        try:
            folder = os.path.dirname(self._c["cache"])
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self._c["cache"], "w") as f:
                json.dump(self.modules, f, indent=1)
        except Exception:
            pass

    @staticmethod
    def label(mod):
        return mod.get("name") or ("MODULE " + mod["req"])

    def menu_label(self, mod):
        if not mod.get("name"):
            return "MODULE " + mod["req"]
        name = self.label(mod)
        if name.endswith("(" + mod["req"] + ")"):
            return name
        return name + " (" + mod["req"] + ")"

    # ---- low level ----------------------------------------------------
    def _ask(self, req, payload):
        """-> (positive_bytes or None, error_text or '')"""
        try:
            replies = self._send(req, payload) or []
        except Exception as err:
            return None, "no reply (" + str(err)[:40] + ")"
        want = int(req, 16) + 8
        pick = None
        for rid, data in replies:
            if rid == want:
                pick = data
                break
        if pick is None and replies:
            pick = replies[0][1]
        if not pick:
            return None, "no reply"
        sid = int(payload[:2], 16)
        if pick[0] == 0x7F:
            code = pick[2] if len(pick) > 2 else 0
            return None, "refused: " + NRC.get(code, "NRC 0x%02X" % code)
        if pick[0] != sid + 0x40:
            return None, "unexpected reply " + bytes(pick[:4]).hex()
        return bytes(pick), ""

    # ---- discovery ------------------------------------------------------
    def discover(self, full=False, progress=None):
        if full:
            lo = int(self._c["sweep_from"], 16)
            hi = int(self._c["sweep_to"], 16)
            ids = ["%03X" % i for i in range(lo, hi + 1)]
        else:
            ids = list(self._c["candidates"])
        found = []
        for n, req in enumerate(ids):
            if progress is not None:
                progress(n + 1, len(ids), req)
            data, err = self._ask(req, "3E00")
            if data is None and "refused" not in err:
                continue          # silence = nothing at this ID
            mod = {"req": req, "name": STANDARD_NAMES.get(int(req, 16)),
                   "seen": time.strftime("%Y-%m-%d %H:%M")}
            ident = self.identify(mod)
            if ident.get("system name") and not mod["name"]:
                mod["name"] = ident["system name"].upper()[:24]
            found.append(mod)
        if found:
            # keep modules found by earlier scans (e.g. a full sweep); drop the
            # never-confirmed starter entries
            known = dict((m["req"], m) for m in self.modules if m.get("seen"))
            for m in found:
                known[m["req"]] = m
            self.modules = sorted(known.values(), key=lambda m: m["req"])
            self._save()
        return found

    # ---- per module -----------------------------------------------------
    def identify(self, mod):
        out = {}
        for did, label in (("F197", "system name"), ("F187", "part number"),
                           ("F18C", "serial number"), ("F195", "software version"),
                           ("F190", "VIN")):
            data, err = self._ask(mod["req"], "22" + did)
            if data is not None and len(data) > 3:
                text = bytes(data[3:]).decode("ascii", errors="ignore").strip(" \x00")
                out[label] = text if text else bytes(data[3:]).hex()
        return out

    def read_dtcs(self, mod):
        """-> {"ok", "error", "codes": [{"code", "status", "status_text", "meaning"}]}"""
        data, err = self._ask(mod["req"], "1902FF")
        if data is None:
            return {"ok": False, "error": err, "codes": []}
        codes = []
        body = data[3:]                       # 59 02 <availability mask> ...
        for i in range(0, len(body) - 3, 4):
            b0, b1, b2, st = body[i], body[i + 1], body[i + 2], body[i + 3]
            if b0 == 0 and b1 == 0 and b2 == 0:
                continue
            code = decode_dtc(b0, b1, b2)
            base = code.split("-")[0]
            codes.append({"code": code, "status": st,
                          "status_text": decode_status(st),
                          "meaning": self._codes.get(base, "")})
        return {"ok": True, "error": "", "codes": codes}

    def clear_dtcs(self, mod):
        data, err = self._ask(mod["req"], "14FFFFFF")
        return data is not None, err

    def scan_all(self, progress=None):
        """Read codes from every known module. -> list of (mod, result)."""
        results = []
        for n, mod in enumerate(self.modules):
            if progress is not None:
                progress(n + 1, len(self.modules), self.label(mod))
            results.append((mod, self.read_dtcs(mod)))
        return results

    # ---- presentation (green raw / yellow meaning) ----------------------
    def module_items(self, mod, ident, result):
        items = [("--- " + self.menu_label(mod) + " ---", "head"),
                 ("RAW  request ID " + mod["req"] + "  reply ID "
                  + "%03X" % (int(mod["req"], 16) + 8), "raw")]
        for k in ("system name", "part number", "software version", "serial number", "VIN"):
            if ident.get(k):
                items.append(("RAW  " + k + ": " + ident[k], "raw"))
        items.append(("--- STORED CODES (UDS 19 02) ---", "head"))
        items.extend(self._code_items(result))
        return items

    def _code_items(self, result):
        items = []
        if not result["ok"]:
            items.append(("RAW  " + result["error"], "raw"))
            items.append(("This module did not give its codes to AutoPi.", "ai"))
            return items
        if not result["codes"]:
            items.append(("RAW  none", "raw"))
            items.append(("[OK] No codes stored in this module.", "ai"))
            return items
        for c in result["codes"]:
            items.append(("RAW  " + c["code"] + "  status 0x%02X = " % c["status"]
                          + c["status_text"], "raw"))
        items.append(("[FIX] " + str(len(result["codes"])) + " code(s) in this module.", "ai"))
        for c in result["codes"]:
            if c["meaning"]:
                items.append(("  " + c["code"].split("-")[0] + ": " + c["meaning"], "ai"))
        return items

    def scan_items(self, results):
        answered = [r for r in results if r[1]["ok"]]
        with_codes = [r for r in answered if r[1]["codes"]]
        items = [("--- ALL-MODULE SCAN  " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---", "head"),
                 ("RAW  " + str(len(results)) + " modules asked, " + str(len(answered))
                  + " answered, " + str(len(with_codes)) + " with codes", "raw")]
        if with_codes:
            items.append(("[FIX] Codes found in: " + ", ".join(
                self.label(m) for m, _r in with_codes), "ai"))
        elif answered:
            items.append(("[OK] No codes in any module that answered.", "ai"))
        else:
            items.append(("[WATCH] No module answered - key ON? adapter connected?", "ai"))
        items.append(("", "raw"))
        for mod, res in results:
            items.append(("--- " + self.menu_label(mod) + " ---", "head"))
            items.extend(self._code_items(res))
            items.append(("", "raw"))
        return items

    @staticmethod
    def facts(results):
        parts = []
        for mod, res in results:
            if not res["ok"]:
                parts.append(mod["req"] + ": no answer")
            else:
                parts.append(mod["req"] + ": " + (", ".join(c["code"] for c in res["codes"]) or "no codes"))
        return "; ".join(parts)
