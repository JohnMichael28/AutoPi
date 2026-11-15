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

STATUS_BITS = [            # UDS DTC status byte (ISO 14229-1), all 8 bits
    (0x01, "FAILING NOW"),
    (0x02, "FAILED THIS KEY CYCLE"),
    (0x04, "PENDING"),
    (0x08, "CONFIRMED"),
    (0x20, "FAILED SINCE CODES CLEARED"),
    (0x80, "WARNING LIGHT ON"),
    (0x10, "not tested since clear"),
    (0x40, "not tested yet this key cycle"),
]
# Bits that mean the test actually FAILED at some point. A code whose status
# has only 0x10/0x40 set is just "this check exists but hasn't run yet" - NOT
# a fault (a 19 02 FF request returns those too: the first real scan showed
# 135 of them from the 2.4 turbo engine module, all status 0x40/0x50).
FAULT_BITS = 0x01 | 0x02 | 0x04 | 0x08 | 0x20 | 0x80

NRC = {0x10: "general reject", 0x11: "service not supported",
       0x12: "sub-function not supported", 0x13: "bad message length",
       0x22: "conditions not correct (try key ON, engine off)",
       0x31: "request out of range", 0x33: "security access denied",
       0x78: "busy - response pending", 0x7E: "not supported in this session",
       0x7F: "service not supported in this session"}

# Not modules: 0x7DF is the OBD "everyone answer" broadcast ID (the first full
# sweep listed a fake "2.4 TURBO (7DF)" - the engine answering the broadcast),
# and 0x7E8-0x7EF are reply IDs.
NOT_MODULES = set([0x7DF] + list(range(0x7E8, 0x7F0)))

# Modules whose codes AutoPi already reads the standard OBD-II way (Mode 03
# stored / 07 pending / 0A permanent). Many engine computers don't answer the
# UDS code request at all - that is normal, not a failure, so it's labelled so.
OBD_CHECKED = {0x7E0}
OBD_CHECKED_NOTE = "normal - engine codes are read the standard OBD-II way (Health Check 1-3)"

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
    return ", ".join(words) if words else "no status bits set"


def is_fault(status):
    return bool(status & FAULT_BITS)


def severity(status):
    """fix = failing now / confirmed / warning light; watch = pending,
    failed this key cycle or failed since clear (intermittent/history)."""
    if status & (0x01 | 0x08 | 0x80):
        return "fix"
    if status & FAULT_BITS:
        return "watch"
    return "info"


def clean_text(raw_bytes):
    """Module names come padded with 0x00/0xFF/odd bytes: keep printable ASCII."""
    text = "".join(chr(b) for b in raw_bytes if 0x20 <= b <= 0x7E)
    return " ".join(text.split())


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

    def __init__(self, send, cfg=None, code_book=None, raw_fn=None):
        merged = dict(self.DEFAULTS)
        if cfg:
            merged.update(cfg)
        self._c = merged
        self._send = send
        self._codes = code_book or {}
        self._raw = raw_fn                  # -> adapter's raw lines of the last request
        self.modules = self._load()

    # ---- cache of discovered modules ---------------------------------
    def _load(self):
        try:
            with open(self._c["cache"]) as f:
                mods = json.load(f)
            if isinstance(mods, list) and mods:
                mods = [m for m in mods if int(m.get("req", "0"), 16) not in NOT_MODULES]
                for m in mods:
                    if m.get("name"):
                        m["name"] = clean_text(m["name"].encode("latin-1", "ignore"))
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
    def _send_once(self, req, payload, timeout_hex):
        try:
            return self._send(req, payload, timeout_hex) or [], ""
        except TypeError:
            try:
                return self._send(req, payload) or [], ""
            except Exception as err:
                return [], str(err)[:40]
        except Exception as err:
            return [], str(err)[:40]

    def _raw_note(self):
        if self._raw is None:
            return ""
        try:
            lines = [ln.strip() for ln in (self._raw() or []) if ln.strip()]
        except Exception:
            return ""
        return " | ".join(lines)[:70]

    def _ask(self, req, payload, timeout_hex="32", busy_retries=3):
        """-> (positive_bytes or None, error_text or ''). Waits through
        "busy - response pending" (7F xx 78) replies by asking again."""
        sid = int(payload[:2], 16)
        want = int(req, 16) + 8
        last_err = "no reply"
        for _attempt in range(busy_retries + 1):
            replies, err = self._send_once(req, payload, timeout_hex)
            if err:
                return None, "no reply (" + err + ")"
            mine = [d for rid, d in replies if rid == want and d] or \
                   [d for _rid, d in replies if d]
            for d in mine:
                if d[0] == sid + 0x40:
                    return bytes(d), ""
            refusals = [d for d in mine if d[0] == 0x7F and len(d) > 2]
            final = [d for d in refusals if d[2] != 0x78]
            if final:
                code = final[-1][2]
                return None, "refused: " + NRC.get(code, "NRC 0x%02X" % code)
            if refusals:                     # only "busy, wait" - ask again
                last_err = "busy - response pending (no final answer)"
                time.sleep(0.5)
                continue
            if mine:
                return None, "unexpected reply " + bytes(mine[0][:4]).hex()
            note = self._raw_note()
            return None, "no reply" + (" (adapter: " + note + ")" if note else "")
        return None, last_err

    # ---- discovery ------------------------------------------------------
    def discover(self, full=False, progress=None):
        if full:
            lo = int(self._c["sweep_from"], 16)
            hi = int(self._c["sweep_to"], 16)
            ids = ["%03X" % i for i in range(lo, hi + 1) if i not in NOT_MODULES]
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
                mod["name"] = ident["system name"].upper()[:28]
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
                text = clean_text(data[3:])
                out[label] = text if text else bytes(data[3:]).hex()
        return out

    # UDS first, then the older KWP2000 service some Japanese modules still use.
    DTC_REQUESTS = [("1902FF", "uds"), ("190209", "uds"), ("1800FF00", "kwp")]

    def read_dtcs(self, mod):
        """-> {"ok", "error", "codes": [...], "tries": [(request, outcome)]}"""
        tries = []
        for payload, kind in self.DTC_REQUESTS:
            data, err = self._ask(mod["req"], payload, timeout_hex="64")
            spaced = " ".join(payload[i:i + 2] for i in range(0, len(payload), 2))
            if data is None:
                tries.append((spaced, err))
                if err.startswith("refused") and "not supported" not in err \
                        and "out of range" not in err:
                    break        # module is there but said no - don't spam it
                continue
            tries.append((spaced, "answered (" + str(len(data)) + " bytes)"))
            entries = self._parse_uds(data) if kind == "uds" else self._parse_kwp(data)
            faults = [c for c in entries if c["severity"] != "info"]
            return {"ok": True, "error": "", "codes": faults, "tries": tries,
                    "not_tested": len(entries) - len(faults)}
        error = tries[-1][1] if tries else "no reply"
        obd_checked = int(mod["req"], 16) in OBD_CHECKED
        if obd_checked:
            error = error.replace("no reply", "no UDS reply", 1) + " - " + OBD_CHECKED_NOTE
        return {"ok": False, "error": error, "codes": [], "tries": tries,
                "obd_checked": obd_checked}

    def _entry(self, code, st):
        return {"code": code, "status": st, "status_text": decode_status(st),
                "severity": severity(st),
                "meaning": self._codes.get(code.split("-")[0], "")}

    def _parse_uds(self, data):
        codes = []
        body = data[3:]                       # 59 02 <availability mask> ...
        for i in range(0, len(body) - 3, 4):
            b0, b1, b2, st = body[i], body[i + 1], body[i + 2], body[i + 3]
            if b0 == 0 and b1 == 0 and b2 == 0:
                continue
            codes.append(self._entry(decode_dtc(b0, b1, b2), st))
        return codes

    def _parse_kwp(self, data):
        # 58 <count> [hi lo status]...
        codes = []
        body = data[2:]
        for i in range(0, len(body) - 2, 3):
            hi, lo, st = body[i], body[i + 1], body[i + 2]
            if hi == 0 and lo == 0:
                continue
            code = (SYSTEMS[(hi >> 6) & 0x03] + str((hi >> 4) & 0x03)
                    + "%X" % (hi & 0x0F) + "%02X" % lo)
            entry = self._entry(code, st)
            entry["severity"] = "fix"
            entry["status_text"] = "stored (KWP status 0x%02X)" % st
            codes.append(entry)
        return codes

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
        items.append(("--- STORED CODES (UDS 19 / KWP 18) ---", "head"))
        for req, outcome in result.get("tries", []):
            items.append(("RAW  asked " + req + " -> " + outcome, "raw"))
        items.extend(self._code_items(result))
        return items

    def _code_items(self, result):
        items = []
        if not result["ok"]:
            items.append(("RAW  " + result["error"], "raw"))
            if result.get("obd_checked"):
                items.append(("[OK] Not a problem: this computer's codes come from the "
                              "standard OBD-II check (stored, pending, permanent).", "ai"))
            else:
                items.append(("This module did not give its codes to AutoPi.", "ai"))
            return items
        quiet = result.get("not_tested", 0)
        if quiet:
            items.append(("RAW  " + str(quiet) + " checks not run yet this key cycle "
                          "(status 0x40/0x10 - normal, not faults)", "raw"))
        if not result["codes"]:
            items.append(("RAW  no failed codes", "raw"))
            items.append(("[OK] Nothing has failed in this module.", "ai"))
            return items
        for c in result["codes"]:
            items.append(("RAW  " + c["code"] + "  status 0x%02X = " % c["status"]
                          + c["status_text"], "raw"))
        fixes = [c for c in result["codes"] if c["severity"] == "fix"]
        if fixes:
            items.append(("[FIX] " + str(len(fixes)) + " code(s) failing now or confirmed.", "ai"))
        else:
            items.append(("[WATCH] Failed once (this key cycle or since clear) but not "
                          "confirmed - intermittent. Re-scan after a drive.", "ai"))
        for c in result["codes"]:
            if c["meaning"]:
                items.append(("  " + c["code"].split("-")[0] + ": " + c["meaning"], "ai"))
        return items

    def scan_items(self, results):
        answered = [r for r in results if r[1]["ok"]]
        with_codes = [r for r in answered if r[1]["codes"]]
        items = [("--- ALL-MODULE SCAN  " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---", "head"),
                 ("RAW  " + str(len(results)) + " modules asked, " + str(len(answered))
                  + " answered, " + str(len(with_codes)) + " with failed codes", "raw")]
        if with_codes:
            confirmed = [m for m, r in with_codes
                         if any(c["severity"] == "fix" for c in r["codes"])]
            tag = "[FIX] " if confirmed else "[WATCH] "
            items.append((tag + "Failed codes in: " + ", ".join(
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
                parts.append(mod["req"] + (": checked via OBD-II" if res.get("obd_checked")
                                           else ": no answer"))
            else:
                parts.append(mod["req"] + ": " + (", ".join(c["code"] for c in res["codes"]) or "no codes"))
        return "; ".join(parts)
