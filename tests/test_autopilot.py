"""Tests for warm-up guide, always-auto screens, voltage removal, playbook,
health check and ML monitor status. Run:  python3 -m unittest discover tests
No car, no pygame, no model needed."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autopi.warmup import WarmupMonitor, FAST_IDLE, DRIVE_GENTLY, ALL_CLEAR, WARM, OFF
from autopi.face_state import FaceState
from autopi.warning_advisor import WarningAdvisor
from autopi.fix_advisor import ActionPlaybook
from autopi.health_check import HealthCheck
from autopi.drive_mode import DriveModeSelector


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class WarmupTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.w = WarmupMonitor(clock=self.clock)

    def step(self, secs, rpm, coolant, speed=0, throttle=5):
        self.clock.t += secs
        return self.w.update(rpm=rpm, coolant=coolant, speed=speed, throttle=throttle)

    def test_cold_start_waits_then_safe_then_clear(self):
        st = self.step(0, 1450, 8)
        self.assertEqual(st["phase"], FAST_IDLE)
        self.assertTrue(st["cold"])
        self.assertIn("WAIT", st["headline"])
        # idle drops at 12 s but min wait is 20 s
        for _ in range(8):
            st = self.step(1, 850, 10)
        self.assertEqual(st["phase"], FAST_IDLE)
        for _ in range(13):                 # now 21 s in, idle settled
            st = self.step(1, 850, 12)
        self.assertEqual(st["phase"], DRIVE_GENTLY)
        self.assertIn("SAFE TO DRIVE OFF", st["headline"])
        st = self.step(1, 4200, 30, speed=40, throttle=80)
        self.assertIn("EASE OFF", st["headline"])
        st = self.step(1, 2000, 80, speed=40)        # 80 C is not fully warm yet
        self.assertEqual(st["phase"], DRIVE_GENTLY)
        st = self.step(1, 2000, 86, speed=40)
        self.assertEqual(st["phase"], ALL_CLEAR)
        self.assertFalse(st["cold"])
        st = self.step(9, 2000, 90, speed=40)
        self.assertEqual(st["phase"], WARM)
        self.assertEqual(st["headline"], "")

    def test_idle_stays_high_gives_go_after_max_wait(self):
        self.step(0, 1500, -5)
        st = None
        for _ in range(91):
            st = self.step(1, 1300, -3)
        self.assertEqual(st["phase"], DRIVE_GENTLY)

    def test_hot_restart_waits_only_for_the_needle(self):
        # Hot engine, idle drops right away: a few seconds of WAIT, then go.
        st = self.step(0, 1250, 92)
        self.assertEqual(st["phase"], FAST_IDLE)
        self.assertIn("IDLE STILL HIGH", st["headline"])
        for _ in range(7):
            st = self.step(1, 820, 92)
        self.assertEqual(st["phase"], ALL_CLEAR)
        self.assertEqual(st["headline"], "IDLE SETTLED - READY TO GO")
        st = self.step(9, 820, 92)
        self.assertEqual(st["phase"], WARM)

    def test_sat_a_while_idle_still_high_does_not_say_go(self):
        # Nov 15: car "warm" but sat a while - ECU held the idle up.
        # It must keep saying WAIT while the needle is up, however warm.
        st = self.step(0, 1300, 75)
        for _ in range(40):
            st = self.step(1, 1150, 76)
            self.assertEqual(st["phase"], FAST_IDLE)
            self.assertTrue(st["cold"])
            self.assertNotIn("SAFE", st["headline"])
            self.assertNotIn("READY", st["headline"])

    def test_needle_still_falling_is_not_settled(self):
        # Below 1000 but still dropping 15 rpm/s: keep waiting until it levels off.
        st = self.step(0, 1200, 90)
        rpm = 990
        for _ in range(10):
            st = self.step(1, rpm, 90)
            rpm -= 15
        self.assertEqual(st["phase"], FAST_IDLE)
        for _ in range(7):
            st = self.step(1, 840, 90)
        self.assertEqual(st["phase"], ALL_CLEAR)

    def test_warm_but_not_hot_goes_gently_after_idle_drops(self):
        st = self.step(0, 1300, 72)
        for _ in range(7):
            st = self.step(1, 850, 72)
        self.assertEqual(st["phase"], FAST_IDLE)        # below 85 C: 20 s oil wait too
        for _ in range(14):
            st = self.step(1, 850, 72)
        self.assertEqual(st["phase"], DRIVE_GENTLY)
        self.assertIn("SAFE TO DRIVE OFF", st["headline"])

    def test_dropped_read_keeps_state_engine_off_resets(self):
        self.step(0, 1400, 10)
        st = self.w.update(rpm=None, coolant=None)
        self.assertEqual(st["phase"], FAST_IDLE)
        st = self.step(1, 0, 10)
        self.assertEqual(st["phase"], OFF)

    def test_driving_off_early_moves_to_gentle(self):
        self.step(0, 1400, 10)
        st = self.step(5, 1500, 11, speed=10)
        self.assertEqual(st["phase"], DRIVE_GENTLY)


class FaceAndVoltageTests(unittest.TestCase):
    def test_cold_face_is_blue_mood(self):
        self.assertEqual(FaceState().decide(coolant_temp=20, cold=True), "cold")

    def test_low_voltage_never_changes_face(self):
        self.assertEqual(FaceState().decide(coolant_temp=90, voltage=11.4), "green")

    def test_low_voltage_never_warns(self):
        adv = WarningAdvisor()
        snap = {"rpm": 800, "speed": 0, "engine_load": 20, "voltage": 11.2,
                "coolant_temp": 90}
        for _ in range(10):
            warnings = adv.check(snap)
        self.assertFalse(any("VOLT" in w for w in warnings))

    def test_cold_mood_exists(self):
        try:
            from autopi.face import MOODS, FACES
        except Exception:
            self.skipTest("pygame not installed")
        self.assertIn("cold", MOODS)
        self.assertIn("cold", FACES)
        r, g, b = MOODS["cold"]["rgb"]
        self.assertTrue(b > r and b > g)       # actually blue


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.book = ActionPlaybook()

    def test_loaded(self):
        self.assertTrue(self.book.ok)

    def test_every_advisor_warning_has_steps(self):
        for text in ("KNOCK - timing pulled sharply under load, ease off",
                     "COOLANT CRITICAL (114C) - stop safely and let it cool",
                     "COOLANT HIGH (107C) - ease off and let it cool",
                     "FUEL TRIM DRIFT (LTFT 17.2%, lean) - check for a leak/fuel issue",
                     "FUEL TRIM DRIFT (LTFT -16%, rich) - check for a leak/fuel issue",
                     "FUEL TRIM SWING (STFT 13%) - running off-target",
                     "STRAINING - high load at low speed, pause to let it cool if you can",
                     "TURBO HOT - idle a minute before shutoff to cool the turbo"):
            steps = self.book.steps_for_warning(text)
            self.assertTrue(len(steps) >= 2, text)
            self.assertNotIn("HEALTH CHECK", steps[0], text)
        lean = self.book.steps_for_warning("FUEL TRIM DRIFT (LTFT 17%, lean)")
        rich = self.book.steps_for_warning("FUEL TRIM DRIFT (LTFT -17%, rich)")
        self.assertNotEqual(lean, rich)

    def test_anomaly_steps_follow_culprit(self):
        res = {"severity": "watch", "culprits": [("coolant_avg5", 4.4), ("rpm", 2.6)]}
        steps = self.book.steps_for_anomaly(res)
        self.assertIn("coolant", steps[0].lower())
        names = self.book.describe_culprits(res)
        self.assertEqual(names[0], "coolant (avg) +4.4 sigma (higher than normal)")


class FakeReader:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class FakeVehicle:
    def __init__(self, codes):
        self.codes = codes

    def get_dtcs(self):
        return self.codes


class HealthCheckTests(unittest.TestCase):
    def make(self, codes, snap, readiness=None, ml_loaded=True, ml=None):
        readers = {
            "PENDING CODES": FakeReader(None),
            "PERMANENT CODES": FakeReader(None),
            "READINESS MONITORS": FakeReader(readiness or {
                "MISFIRE_MONITORING": {"available": True, "complete": True},
                "CATALYST_MONITORING": {"available": True, "complete": True}}),
            "MONITOR TESTS (06)": FakeReader(None),
        }
        return HealthCheck(
            FakeVehicle(codes), readers, lambda: snap, ActionPlaybook(),
            anomaly_status_fn=lambda: {"loaded": ml_loaded, "reason": "model file not found: x"},
            anomaly_result_fn=lambda: (ml or {"severity": "ok"}),
            warmup_fn=lambda: {"cold": False, "phase": "warm"},
            module_scan_fn=lambda: [({"req": "7E0"}, {"ok": True, "error": "", "codes": []})])

    def test_clean_car_passes(self):
        hc = self.make([], {"rpm": 750, "coolant_temp": 92, "stft_b1": 1.5,
                            "ltft_b1": -2.0, "o2_b1s2": 0.65, "run_time": 20})
        items = hc.render(hc.run())
        self.assertEqual(items[1], ("PASS - nothing needs attention.", "ai"))

    def test_layout_is_consistent(self):
        hc = self.make([("P0171", "System Too Lean Bank 1")],
                       {"rpm": 750, "coolant_temp": 92, "stft_b1": 4,
                        "ltft_b1": 18.0, "run_time": 20},
                       readiness={"CATALYST_MONITORING": {"available": True, "complete": False}},
                       ml_loaded=False)
        items = hc.render(hc.run())
        heads = [t for t, k in items if k == "head"]
        # result header + 9 numbered sections, always in the same order
        self.assertEqual(len(heads), 10)
        self.assertTrue(heads[1].startswith("--- 1. STORED CODES"))
        self.assertTrue(heads[8].startswith("--- 8. ML ANOMALY MONITOR"))
        self.assertTrue(heads[9].startswith("--- 9. ALL MODULES"))
        # every RAW line is green (raw), every meaning/step is yellow (ai)
        for text, kind in items:
            if text.startswith("RAW "):
                self.assertEqual(kind, "raw")
            if text.startswith("[FIX]") or text.startswith("[WATCH]") or text.startswith("[OK]"):
                self.assertEqual(kind, "ai")
        joined = "\n".join(t for t, k in items)
        self.assertIn("RAW  P0171  System Too Lean Bank 1", joined)
        self.assertIn("2 TO FIX", joined)
        self.assertIn("RAW  catalyst        NOT complete", joined)
        self.assertIn("RAW  OFF: model file not found", joined)

    def test_engine_off_skips_live(self):
        hc = self.make([], {"rpm": "--"})
        joined = "\n".join(t for t, k in hc.render(hc.run()))
        self.assertIn("Engine off - live checks skipped", joined)


class IssueGuideTests(unittest.TestCase):
    def setUp(self):
        from autopi.issue_guide import IssueGuide
        self.clock = Clock()
        self.guide = IssueGuide(ActionPlaybook(), {"persist_s": 8, "cooldown_s": 300},
                                clock=self.clock)

    def run_for(self, secs, warnings, ml=None):
        event = None
        for _ in range(int(secs)):
            self.clock.t += 1
            e = self.guide.update(warnings, ml or {"severity": "ok"})
            event = event or e
        return event

    def test_blip_is_ignored(self):
        self.assertIsNone(self.run_for(5, ["COOLANT HIGH (106C) - ease off"]))

    def test_persistent_warning_points_to_graph_once(self):
        ev = self.run_for(10, ["FUEL TRIM DRIFT (LTFT 17%, lean) - check"])
        self.assertIsNotNone(ev)
        self.assertEqual(ev["page"], "FUEL")
        self.assertIn("FUEL graph", ev["say"])
        # changing value text is still the SAME issue: no repeat within cooldown
        self.assertIsNone(self.run_for(60, ["FUEL TRIM DRIFT (LTFT 18%, lean) - check"]))

    def test_sustained_ml_anomaly_points_to_graph(self):
        ml = {"severity": "elevated", "culprits": [("coolant_avg5", 5.1)]}
        ev = self.run_for(10, [], ml)
        self.assertEqual(ev["page"], "TEMPS")

    def test_every_look_page_exists(self):
        from autopi.graph_pages import DEFAULT_PAGES
        names = set(p["name"] for p in DEFAULT_PAGES)
        book = ActionPlaybook()
        for page in book._look["warnings"].values():
            self.assertIn(page, names)
        for key, page in book._look["anomaly"].items():
            if not key.startswith("_about"):
                self.assertIn(page, names)


class FakeResp:
    def __init__(self, v):
        self.value = v

    def is_null(self):
        return self.value is None


class FakeConn:
    def __init__(self, latency=0.01):
        self.latency = latency
        self.queries = []

    def supports(self, cmd):
        return True

    def is_connected(self):
        return True

    def query(self, cmd):
        import time as _t
        _t.sleep(self.latency)
        self.queries.append(cmd.name)
        return FakeResp({"RPM": 2500, "THROTTLE_POS": 30, "SPEED": 80}.get(cmd.name, 50))

    def close(self):
        pass


class FastLaneTests(unittest.TestCase):
    def make(self):
        try:
            from autopi.vehicle import Vehicle
        except Exception as err:
            self.skipTest("vehicle import failed: " + str(err))
        v = Vehicle(port="x")
        conn = FakeConn()
        v._Vehicle__connection = conn
        return v, conn

    def test_rpm_streams_fast_and_snapshot_skips_it(self):
        import time as _t
        v, conn = self.make()
        v.start_fast_lane(gap_s=0.002)
        _t.sleep(0.5)
        fast = v.fast_values()
        self.assertEqual(fast["RPM"], 2500)
        self.assertGreater(v.rpm_rate_hz(), 10)       # 10 ms adapter -> 20+/s
        conn.queries.clear()
        r = v.read_current()
        v.stop_fast_lane()
        self.assertEqual(r.rpm, 2500)
        self.assertEqual(r.throttle, 30)

    def test_priority_pauses_fast_lane(self):
        import time as _t
        v, conn = self.make()
        v.start_fast_lane(gap_s=0.002)
        _t.sleep(0.1)
        with v.priority():
            _t.sleep(0.08)            # let an in-flight read finish
            conn.queries.clear()
            _t.sleep(0.2)
            self.assertEqual(conn.queries, [])
        _t.sleep(0.1)
        v.stop_fast_lane()
        self.assertTrue(len(conn.queries) > 0)


class ScreenOverrideTests(unittest.TestCase):
    """VehicleData's override rule, checked against the real selector."""

    def test_menu_pick_is_temporary(self):
        clock = Clock()
        sel = DriveModeSelector(clock=clock)
        override = None
        base = None

        def tick(speed, rpm):
            nonlocal override
            clock.t += 1
            pick = sel.update(speed=speed, rpm=rpm, throttle=20, load=30)
            if override is not None and pick != base:
                override = None
            return override if override is not None else pick

        for _ in range(3):
            mode = tick(50, 2000)
        self.assertEqual(mode, "highway")
        override, base = "track", sel.mode          # driver picks TRACK
        self.assertEqual(tick(50, 2000), "track")   # holds while cruising
        mode = None
        for _ in range(5):
            mode = tick(0, 800)                     # stops: auto takes over
        self.assertEqual(mode, "camp")


class AIClientTests(unittest.TestCase):
    def make(self):
        from autopi.ai_client import AIClient

        class Cfg:
            ollama_ip = "10.255.255.1"
        c = AIClient(Cfg())
        c.retry_wait_s = 0
        c.retry_slow_s = 0
        return c

    def test_cache_and_single_flight(self):
        import threading
        import time as _t
        c = self.make()
        calls = []

        def tiers(prompt):
            calls.append(prompt)
            _t.sleep(0.2)
            return "fine"
        c._AIClient__ask_tiers = tiers
        threads = [threading.Thread(target=c.ask, args=("q",)) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(c.ask("q"), "fine")
        self.assertEqual(len(calls), 1)        # 5 asks, 1 model call

    def test_retry_until_answer_with_status(self):
        c = self.make()
        answers = ["AI unavailable: timeout", "AI unavailable: timeout", "ok now"]
        c._AIClient__ask_tiers = lambda p: answers.pop(0)
        seen = []
        out = c.ask_with_retry("q2", on_status=seen.append, still_wanted=lambda: True)
        self.assertEqual(out, "ok now")
        self.assertTrue(any("retrying" in m for m in seen))

    def test_retry_stops_when_screen_left(self):
        c = self.make()
        c._AIClient__ask_tiers = lambda p: "AI unavailable: down"
        state = {"n": 0}

        def wanted():
            state["n"] += 1
            return state["n"] < 4
        out = c.ask_with_retry("q3", still_wanted=wanted)
        self.assertTrue(out.startswith("AI unavailable"))

    def test_tidy_removes_prompt_parroting(self):
        from autopi.ai_client import AIClient
        raw = ("<<DATA B2C22-00 CONFIRMED: EyeSight comms fault. Have the dealer check it. "
               "SECURITY: I cannot claim I did anything to the car.")
        out = AIClient.tidy(raw)
        self.assertNotIn("<<", out)
        self.assertNotIn("SECURITY", out)
        self.assertIn("Have the dealer check it.", out)

    def test_local_only_never_calls_another_host(self):
        import autopi.ai_client as mod
        c = self.make()
        urls = []

        def fake_post(url, **kw):
            urls.append(url)
            raise mod.requests.exceptions.ConnectionError("refused")
        old = mod.requests.post
        mod.requests.post = fake_post
        try:
            ans = c.ask("hello")
        finally:
            mod.requests.post = old
        self.assertTrue(ans.startswith("AI unavailable"))
        self.assertIn("not running", ans)
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("http://127.0.0.1:8080/"))


class SnapshotLogCadenceTests(unittest.TestCase):
    def test_logger_keeps_trained_row_spacing(self):
        import time as _t
        from autopi.snapshot_thread import SnapshotThread

        class Data:
            def snapshot(self):
                return {"rpm": 800}

        class Log:
            def __init__(self):
                self.n = 0

            def log(self, snap):
                self.n += 1
        log = Log()
        st = SnapshotThread(Data(), interval=0.01, logger=log, log_interval=0.2)
        _t.sleep(0.5)
        st.stop()
        self.assertTrue(2 <= log.n <= 4, log.n)


class AutoDynoTests(unittest.TestCase):
    """Simulates the real data path: snapshots every 0.15 s, speed from the
    fast lane ~3x/s in whole km/h (so most snapshots repeat the speed)."""

    def drive(self, dyno, clock, secs, accel_ms2, throttle, load=60,
              start_mph=20.0, rpm_from=3000, rpm_to=5800):
        speed_ms = start_mph * 0.44704
        speed_sample = None
        sample_t = None
        t_end = clock.t + secs
        t0 = clock.t
        next_speed_read = clock.t
        while clock.t < t_end:
            speed_ms += accel_ms2 * 0.15
            if clock.t >= next_speed_read:
                kmh = int(round(speed_ms * 3.6))           # OBD: whole km/h
                speed_sample = kmh * 0.621371
                sample_t = clock.t
                next_speed_read = clock.t + 0.33
            frac = (clock.t - t0) / secs
            rpm = rpm_from + (rpm_to - rpm_from) * frac
            dyno.feed(rpm, speed_sample, throttle, load, speed_t=sample_t)
            clock.t += 0.15
        return speed_ms / 0.44704

    def make(self):
        from autopi.drive_mode import AutoDyno
        clock = Clock()
        clock.t = 1000.0
        return AutoDyno(clock=clock), clock

    def test_hard_pull_is_recorded_with_repeated_speed_samples(self):
        dyno, clock = self.make()
        self.drive(dyno, clock, 6.0, 3.0, throttle=85)
        self.assertTrue(dyno.is_pulling() or dyno.last() is not None)
        self.drive(dyno, clock, 3.0, 0.0, throttle=10)       # lift off
        last = dyno.last()
        self.assertIsNotNone(last, "pull was not recorded")
        self.assertGreater(len(last["points"]), 5)
        self.assertGreater(last["peak_hp"], 100)
        self.assertLess(last["peak_hp"], 450)
        self.assertGreater(last["duration_s"], 4)

    def test_load_counts_as_pedal_when_throttle_reads_low(self):
        dyno, clock = self.make()
        self.drive(dyno, clock, 5.0, 2.8, throttle=45, load=95)
        self.drive(dyno, clock, 3.0, 0.0, throttle=10, load=20)
        self.assertIsNotNone(dyno.last())

    def test_normal_acceleration_is_not_a_pull(self):
        dyno, clock = self.make()
        self.drive(dyno, clock, 6.0, 1.2, throttle=25, load=45)
        self.drive(dyno, clock, 3.0, 0.0, throttle=10, load=20)
        self.assertIsNone(dyno.last())

    def test_brief_dip_does_not_split_the_pull(self):
        dyno, clock = self.make()
        self.drive(dyno, clock, 3.0, 3.0, throttle=85)
        self.drive(dyno, clock, 0.45, 0.1, throttle=85, start_mph=40)   # CVT step
        self.drive(dyno, clock, 3.0, 3.0, throttle=85, start_mph=41)
        self.drive(dyno, clock, 3.0, 0.0, throttle=10, start_mph=60)
        self.assertEqual(len(dyno.history()), 1)


class ReportTests(unittest.TestCase):
    def make(self, codes):
        from autopi.report_builder import DiagnosticReport
        snap = {"rpm": 750, "coolant_temp": 92, "stft_b1": 1.5, "ltft_b1": 18.0,
                "o2_b1s2": 0.6, "run_time": 20}
        readers = {"PENDING CODES": FakeReader(None), "PERMANENT CODES": FakeReader(None),
                   "READINESS MONITORS": FakeReader({"MISFIRE_MONITORING":
                                                     {"available": True, "complete": True}}),
                   "MONITOR TESTS (06)": FakeReader(None),
                   "FREEZE FRAME": FakeReader({"trigger_code": "P0171", "rpm": 2400})}
        hc = HealthCheck(FakeVehicle(codes), readers, lambda: snap, ActionPlaybook(),
                         anomaly_status_fn=lambda: {"loaded": True, "reason": "loaded"},
                         anomaly_result_fn=lambda: {"severity": "ok"},
                         warmup_fn=lambda: {"cold": False},
                         module_scan_fn=lambda: [({"req": "7D0"}, {"ok": True, "error": "", "codes": [
                             {"code": "B2A16-00", "status": 9, "status_text": "FAILING NOW, CONFIRMED",
                              "severity": "fix",
                              "meaning": "Telematics (DCM): immobilizer key-check failed"}]})])
        return DiagnosticReport(None, hc, lambda: snap, readers=readers,
                                dyno_info_fn=lambda: {"time": "2026-11-13 00:21:52",
                                                      "peak_hp": 231, "peak_rpm": 5600})

    def test_report_has_everything_a_mechanic_needs(self):
        rep = self.make([("P0171", "System Too Lean <Bank 1>")])
        data = rep.collect()
        page = rep.to_html(data, "Lean condition on bank 1.")
        for must in ("P0171", "Freeze frame", "Live snapshot", "READINESS",
                     "Last dyno pull", "231 hp", "Lean condition", "OVERALL"):
            self.assertIn(must, page)
        self.assertIn("&lt;Bank 1&gt;", page)        # escaped, not raw HTML
        self.assertNotIn("<Bank 1>", page)
        text = rep.to_text(data, None)
        self.assertIn("RAW  P0171", text)
        items = rep.screen_items(data, "sum")
        self.assertTrue(all(k in ("head", "raw", "ai", "dim") for _t, k in items))

    def test_save_writes_html_and_text(self):
        import tempfile
        rep = self.make([])
        folder = tempfile.mkdtemp()
        h, t = rep.save(rep.collect(), None, folder=folder)
        self.assertTrue(os.path.exists(h) and os.path.exists(t))


class ReportServerTests(unittest.TestCase):
    def test_serves_reports_only(self):
        import tempfile
        import urllib.request
        import urllib.error
        from autopi.report_share import ReportServer
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "latest.html"), "w") as f:
            f.write("<p>report</p>")
        srv = ReportServer(folder, port=18088, lifetime_s=60)
        srv.start()
        try:
            body = urllib.request.urlopen("http://127.0.0.1:18088/latest.html").read()
            self.assertIn(b"report", body)
            for bad in ("http://127.0.0.1:18088/", "http://127.0.0.1:18088/../config.json",
                        "http://127.0.0.1:18088/%2e%2e/%2e%2e/etc/passwd"):
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(bad)
        finally:
            srv.stop()


class HotspotTests(unittest.TestCase):
    def test_wifi_qr_escapes(self):
        from autopi.report_share import wifi_qr_text
        self.assertEqual(wifi_qr_text("AutoPi-0042", "ab;cd"),
                         "WIFI:T:WPA;S:AutoPi-0042;P:ab\\;cd;;")

    def test_start_uses_sudo_when_needed_and_stop_restores_wifi(self):
        from autopi.report_share import PiHotspot
        calls = []

        def run(args):
            calls.append(args)
            if args[:2] == ["nmcli", "-t"]:
                return 0, "HomeWiFi:802-11-wireless:wlan0\nWired:802-3-ethernet:eth0"
            if args[0] == "nmcli" and "hotspot" in args:
                return 10, "Error: Not authorized to control networking."
            return 0, "ok"
        hs = PiHotspot(run=run)
        ok, msg = hs.start()
        self.assertTrue(ok)
        self.assertTrue(hs.ssid.startswith("AutoPi-"))
        self.assertGreaterEqual(len(hs.password), 8)
        self.assertTrue(any(c[:3] == ["sudo", "-n", "nmcli"] and "hotspot" in c for c in calls))
        hs.stop()
        self.assertIn(["nmcli", "con", "up", "id", "HomeWiFi"], calls)
        self.assertFalse(hs.active)

    def test_failed_start_restores_and_reports(self):
        from autopi.report_share import PiHotspot

        def run(args):
            if "hotspot" in args:
                return 4, "Error: No Wi-Fi device found."
            return 0, ""
        hs = PiHotspot(run=run)
        ok, msg = hs.start()
        self.assertFalse(ok)
        self.assertIn("No Wi-Fi device", msg)

    def test_text_qr_fits(self):
        rep = ReportTests().make([("P0171", "System Too Lean Bank 1")])
        text = rep.to_qr_text(rep.collect(), "Lean on bank 1.")
        self.assertLessEqual(len(text), 900)
        self.assertIn("OVERALL", text)
        try:
            from autopi.report_share import qr_matrix
        except Exception:
            return
        self.assertIsNotNone(qr_matrix(text))


class BootLoaderTests(unittest.TestCase):
    def test_runs_every_step_and_reports_problems(self):
        import time as _t
        try:
            import pygame
            pygame.init()
        except Exception:
            self.skipTest("pygame not installed")
        from autopi.boot_loader import BootLoader
        steps = [("A", lambda: ("ok", "")), ("B", lambda: ("warn", "no wifi")),
                 ("C", lambda: 1 / 0)]
        b = BootLoader(800, 480, steps)
        for _ in range(50):
            if b.finished():
                break
            _t.sleep(0.02)
        states = [r[1] for r in b.results()]
        self.assertEqual(states, ["ok", "warn", "fail"])


class ModuleScanTests(unittest.TestCase):
    """Fake CAN network: engine (no codes), ABS (one C code), a telematics
    DCM (B2A16 failing now) and an airbag module that refuses to read."""

    def fake_send(self, req, payload):
        nets = {"7E0", "7B0", "7D0", "780"}
        if req not in nets:
            return []
        rid = int(req, 16) + 8
        if payload == "3E00":
            return [(rid, bytes([0x7E, 0x00]))]
        if payload == "22F197":
            name = {"7D0": b"TELEMATICS DCM"}.get(req)
            return [(rid, bytes([0x62, 0xF1, 0x97]) + name)] if name else [(rid, bytes([0x7F, 0x22, 0x31]))]
        if payload.startswith("22"):
            return [(rid, bytes([0x7F, 0x22, 0x31]))]
        if payload == "1902FF":
            if req == "780":
                return [(rid, bytes([0x7F, 0x19, 0x22]))]
            body = {"7E0": b"", "7B0": bytes([0x40, 0x31, 0x00, 0x08]),
                    "7D0": bytes([0xAA, 0x16, 0x00, 0x09])}[req]
            return [(rid, bytes([0x59, 0x02, 0xFF]) + body)]
        if payload == "14FFFFFF":
            return [(rid, bytes([0x54]))]
        return []

    def make(self):
        import tempfile
        from autopi.module_scan import ModuleScanner
        book = {"B2A16": "Telematics (DCM): immobilizer key-check failed."}
        cache = os.path.join(tempfile.mkdtemp(), "modules.json")
        return ModuleScanner(self.fake_send, {"cache": cache}, book), cache

    def test_decode(self):
        from autopi.module_scan import decode_dtc, decode_status
        self.assertEqual(decode_dtc(0xAA, 0x16, 0x00), "B2A16-00")
        self.assertEqual(decode_dtc(0x40, 0x31, 0x00), "C0031-00")
        self.assertEqual(decode_dtc(0xC1, 0x00, 0x00), "U0100-00")
        self.assertEqual(decode_status(0x09), "FAILING NOW, CONFIRMED")

    def test_discover_names_and_saves(self):
        from autopi.module_scan import ModuleScanner
        sc, cache = self.make()
        found = sc.discover(full=False)
        self.assertEqual(sorted(m["req"] for m in found), ["780", "7B0", "7D0", "7E0"])
        dcm = [m for m in sc.modules if m["req"] == "7D0"][0]
        self.assertEqual(dcm["name"], "TELEMATICS DCM")
        again = ModuleScanner(self.fake_send, {"cache": cache})
        self.assertIn("7D0", [m["req"] for m in again.modules])     # remembered

    def test_scan_finds_the_dcm_fault_the_engine_scan_missed(self):
        sc, _ = self.make()
        sc.discover()
        results = dict((m["req"], r) for m, r in sc.scan_all())
        self.assertEqual(results["7E0"]["codes"], [])
        self.assertEqual(results["7D0"]["codes"][0]["code"], "B2A16-00")
        self.assertIn("immobilizer", results["7D0"]["codes"][0]["meaning"])
        self.assertFalse(results["780"]["ok"])
        self.assertIn("conditions not correct", results["780"]["error"])
        items = sc.scan_items(sc.scan_all())
        text = "\n".join(t for t, k in items)
        self.assertIn("4 modules asked, 3 answered, 2 with failed codes", text)
        self.assertIn("RAW  B2A16-00  status 0x09 = FAILING NOW, CONFIRMED", text)
        for t, k in items:
            if t.startswith("RAW "):
                self.assertEqual(k, "raw")

    def test_not_tested_entries_are_not_faults(self):
        """Real car: the 2.4 turbo module returned 135 entries with status
        0x40/0x50 ('not tested yet') - those must NOT show as faults."""
        from autopi.module_scan import ModuleScanner
        import tempfile
        body = bytes([0x00, 0x06, 0x2D, 0x40, 0x00, 0x12, 0x61, 0x50,
                      0x94, 0xA1, 0x00, 0x02])           # last: B14A1 failed this cycle

        def send(req, payload, timeout_hex="32"):
            if payload == "1902FF":
                return [(0x7AA, bytes([0x59, 0x02, 0xFF]) + body)]
            return []
        sc = ModuleScanner(send, {"cache": os.path.join(tempfile.mkdtemp(), "m.json")})
        res = sc.read_dtcs({"req": "7A2"})
        self.assertEqual(res["not_tested"], 2)
        self.assertEqual([c["code"] for c in res["codes"]], ["B14A1-00"])
        self.assertEqual(res["codes"][0]["severity"], "watch")
        self.assertIn("FAILED THIS KEY CYCLE", res["codes"][0]["status_text"])

    def test_broadcast_id_is_not_a_module(self):
        from autopi.module_scan import ModuleScanner
        import tempfile
        asked = []

        def send(req, payload, timeout_hex="32"):
            asked.append(req)
            return []
        sc = ModuleScanner(send, {"cache": os.path.join(tempfile.mkdtemp(), "m.json"),
                                  "sweep_from": "7D0", "sweep_to": "7EF"})
        sc.discover(full=True)
        self.assertNotIn("7DF", asked)
        self.assertNotIn("7E8", asked)
        self.assertIn("7E0", asked)

    def test_module_names_are_cleaned(self):
        from autopi.module_scan import clean_text
        self.assertEqual(clean_text(b"AIRBAG SYSTEM\x00\xff\xff\x01\x7f"), "AIRBAG SYSTEM")

    def test_isotp_parser_keeps_busy_then_answer(self):
        from autopi.vehicle import parse_isotp_lines
        lines = ["7E8 03 7F 19 78", "7E8 07 59 02 FF AA 16 00 09", "NO DATA"]
        self.assertEqual(parse_isotp_lines(lines),
                         [(0x7E8, bytes([0x7F, 0x19, 0x78])),
                          (0x7E8, bytes([0x59, 0x02, 0xFF, 0xAA, 0x16, 0x00, 0x09]))])
        multi = ["7D8100B5902FFAA1600", "7D82109C1000008"]      # no spaces too
        self.assertEqual(parse_isotp_lines(multi)[0][1],
                         bytes([0x59, 0x02, 0xFF, 0xAA, 0x16, 0x00, 0x09, 0xC1, 0x00, 0x00, 0x08]))

    def test_busy_then_answer_is_read(self):
        from autopi.module_scan import ModuleScanner
        import tempfile

        def send(req, payload, timeout_hex="32"):
            if payload == "1902FF":
                return [(0x7E8, bytes([0x7F, 0x19, 0x78])),
                        (0x7E8, bytes([0x59, 0x02, 0xFF, 0xC1, 0x00, 0x00, 0x08]))]
            return []
        sc = ModuleScanner(send, {"cache": os.path.join(tempfile.mkdtemp(), "m.json")})
        res = sc.read_dtcs({"req": "7E0"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["codes"][0]["code"], "U0100-00")

    def test_kwp_fallback_when_uds_not_supported(self):
        from autopi.module_scan import ModuleScanner
        import tempfile

        def send(req, payload, timeout_hex="32"):
            if payload.startswith("19"):
                return [(0x7E8, bytes([0x7F, 0x19, 0x11]))]
            if payload == "1800FF00":
                return [(0x7E8, bytes([0x58, 0x01, 0x01, 0x71, 0x48]))]   # P0171
            return []
        sc = ModuleScanner(send, {"cache": os.path.join(tempfile.mkdtemp(), "m.json")})
        res = sc.read_dtcs({"req": "7E0"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["codes"][0]["code"], "P0171")
        self.assertEqual(len(res["tries"]), 3)

    def test_uds_request_puts_engine_header_back(self):
        from autopi.vehicle import Vehicle
        sent = []

        class Iface:
            def _ELM327__send(self, cmd, delay=None):
                sent.append(cmd)
                if cmd == b"1902FF":
                    return ["7D8 07 59 02 FF AA 16 00 09"]
                return ["OK"]

        class Conn:
            interface = Iface()

            def is_connected(self):
                return True
        v = Vehicle(port="x")
        v._Vehicle__connection = Conn()
        out = v.uds_request("7D0", "1902FF", "64")
        self.assertEqual(out[0][0], 0x7D8)
        self.assertEqual(sent, [b"AT SH 7D0", b"AT ST 64", b"1902FF", b"AT ST 32", b"AT SH 7E0"])

    def test_clear_one_module(self):
        sc, _ = self.make()
        ok, err = sc.clear_dtcs({"req": "7D0"})
        self.assertTrue(ok)
        ok, err = sc.clear_dtcs({"req": "7A0"})
        self.assertFalse(ok)


class AnomalyStatusTests(unittest.TestCase):
    def test_missing_model_reports_reason(self):
        from autopi.anomaly_feed import AnomalyFeed
        feed = AnomalyFeed("models/definitely_missing.joblib")
        st = feed.status()
        self.assertFalse(st["loaded"])
        self.assertTrue(st["reason"])
        self.assertEqual(feed.push({"rpm": 800})["severity"], "ok")


# ---- SD-card care: storage keeper + batched data logger -------------------
import tempfile
import shutil as _shutil
import time as _time
from collections import namedtuple

_Usage = namedtuple("_Usage", "total used free")
DAY = 86400


def _touch(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class StorageKeeperTests(unittest.TestCase):
    def setUp(self):
        from autopi.storage_keeper import StorageKeeper
        self.Keeper = StorageKeeper
        self.root = tempfile.mkdtemp()
        # "now" = 15 Nov 2026, local time
        self.now = _time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))
        self.free = 10000          # MB the fake card has free

    def tearDown(self):
        _shutil.rmtree(self.root, ignore_errors=True)

    def keeper(self, **cfg):
        usage = lambda path: _Usage(0, 0, self.free * 1024 * 1024)
        return self.Keeper(cfg, root=self.root, clock=lambda: self.now,
                           disk_usage=usage)

    def p(self, rel):
        return os.path.join(self.root, rel)

    def test_last_months_log_is_archived_and_this_months_is_not(self):
        oct_t = _time.mktime((2026, 10, 20, 9, 0, 0, 0, 0, -1))
        _touch(self.p("data/driving_log_v2.csv"), "h\n1\n", oct_t)
        _touch(self.p("data/warning_log.csv"), "h\n", self.now - 60)
        s = self.keeper().run()
        self.assertTrue(os.path.exists(self.p("data/archive/driving_log_v2_2026-10.csv")))
        self.assertFalse(os.path.exists(self.p("data/driving_log_v2.csv")))
        self.assertTrue(os.path.exists(self.p("data/warning_log.csv")))
        self.assertEqual(len(s["rolled"]), 1)

    def test_legacy_log_goes_to_archive(self):
        _touch(self.p("data/driving_log.csv"), "old", self.now - 30 * DAY)
        self.keeper().run()
        self.assertFalse(os.path.exists(self.p("data/driving_log.csv")))
        self.assertEqual(len(os.listdir(self.p("data/archive"))), 1)

    def test_archives_older_than_keep_days_are_deleted(self):
        _touch(self.p("data/archive/driving_log_v2_2026-06.csv"), "a", self.now - 150 * DAY)
        _touch(self.p("data/archive/driving_log_v2_2026-10.csv"), "b", self.now - 20 * DAY)
        s = self.keeper(keep_days=90).run()
        self.assertFalse(os.path.exists(self.p("data/archive/driving_log_v2_2026-06.csv")))
        self.assertTrue(os.path.exists(self.p("data/archive/driving_log_v2_2026-10.csv")))
        self.assertEqual(len(s["deleted"]), 1)

    def test_size_cap_removes_oldest_first(self):
        big = "x" * (600 * 1024)
        _touch(self.p("data/archive/a.csv"), big, self.now - 30 * DAY)
        _touch(self.p("data/archive/b.csv"), big, self.now - 20 * DAY)
        _touch(self.p("data/archive/c.csv"), big, self.now - 10 * DAY)
        self.keeper(max_archive_mb=1).run()
        left = sorted(os.listdir(self.p("data/archive")))
        self.assertEqual(left, ["c.csv"])

    def test_only_newest_reports_kept_and_latest_html_never_deleted(self):
        for i in range(5):
            t = self.now - (10 - i) * DAY
            _touch(self.p("reports/autopi_report_%d.html" % i), "h", t)
            _touch(self.p("reports/autopi_report_%d.txt" % i), "t", t)
        _touch(self.p("reports/latest.html"), "l", self.now - 100 * DAY)
        self.keeper(keep_reports=2).run()
        left = sorted(os.listdir(self.p("reports")))
        self.assertEqual(left, ["autopi_report_3.html", "autopi_report_3.txt",
                                "autopi_report_4.html", "autopi_report_4.txt",
                                "latest.html"])

    def test_big_text_log_rotates_to_dot_one(self):
        _touch(self.p("logs/boot.log"), "x" * 3000)
        _touch(self.p("logs/boot.log.1"), "older")
        self.keeper(max_text_log_kb=1).run()
        self.assertFalse(os.path.exists(self.p("logs/boot.log")))
        with open(self.p("logs/boot.log.1")) as f:
            self.assertEqual(len(f.read()), 3000)

    def test_low_space_deletes_archives_then_reports_low(self):
        _touch(self.p("data/archive/a.csv"), "a", self.now - 5 * DAY)
        self.free = 100
        k = self.keeper(min_free_mb=500)
        s = k.run()
        self.assertFalse(os.path.exists(self.p("data/archive/a.csv")))
        self.assertTrue(s["low_space"])
        self.assertIn("nearly full", k.describe(s))

    def test_never_touches_model_config_or_module_cache(self):
        old = self.now - 400 * DAY
        _touch(self.p("config.json"), "{}", old)
        _touch(self.p("models/anomaly_model.joblib"), "m", old)
        _touch(self.p("data/modules.json"), "[]", old)
        # even if someone points the archive at a protected folder
        k = self.keeper(archive_dir="models", keep_days=1, max_archive_mb=0)
        s = k.run()
        self.assertTrue(os.path.exists(self.p("models/anomaly_model.joblib")))
        self.assertTrue(os.path.exists(self.p("config.json")))
        self.assertTrue(os.path.exists(self.p("data/modules.json")))
        self.assertTrue(any("refused" in n for n in s["notes"]))

    def test_describe_is_short_and_plain(self):
        k = self.keeper()
        line = k.describe(k.run())
        self.assertIn("MB free", line)
        self.assertLess(len(line), 60)


class BatchedDataLoggerTests(unittest.TestCase):
    def setUp(self):
        from autopi.data_logger import DataLogger
        self.DataLogger = DataLogger
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "data", "driving_log_v2.csv")
        self.clock = Clock()
        self.free = 10000

    def tearDown(self):
        _shutil.rmtree(self.root, ignore_errors=True)

    def make(self, **kw):
        usage = lambda path: _Usage(0, 0, self.free * 1024 * 1024)
        return self.DataLogger(self.path, clock=self.clock, disk_usage=usage, **kw)

    def rows(self):
        with open(self.path) as f:
            return f.read().strip().splitlines()

    def test_rows_wait_in_memory_until_a_batch_is_due(self):
        log = self.make(flush_s=60, flush_rows=50)
        for _ in range(10):
            log.log({"rpm": 800, "speed": 0})
        self.assertEqual(len(self.rows()), 1)          # header only
        self.assertEqual(log.pending(), 10)
        self.clock.t = 61
        log.log({"rpm": 800})
        self.assertEqual(len(self.rows()), 12)         # header + 11 in one write
        self.assertEqual(log.writes, 1)

    def test_row_count_triggers_a_batch(self):
        log = self.make(flush_s=999, flush_rows=5)
        for _ in range(5):
            log.log({"rpm": 900})
        self.assertEqual(len(self.rows()), 6)

    def test_engine_off_saves_the_batch_right_away(self):
        log = self.make(flush_s=999, flush_rows=999)
        for _ in range(3):
            log.log({"rpm": 900})
        log.log({"rpm": 0})                            # key turning off
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(log.pending(), 0)

    def test_new_file_with_header_after_monthly_archive(self):
        log = self.make(flush_s=999, flush_rows=1)
        log.log({"rpm": 900})
        os.replace(self.path, self.path + ".old")      # what the keeper does
        log.log({"rpm": 950})
        rows = self.rows()
        self.assertTrue(rows[0].startswith("timestamp,"))
        self.assertEqual(len(rows), 2)

    def test_card_nearly_full_pauses_logging(self):
        log = self.make(flush_s=999, flush_rows=1, min_free_mb=500)
        self.free = 100
        log.log({"rpm": 900})
        self.assertTrue(log.paused)
        self.assertEqual(len(self.rows()), 1)
        self.free = 5000
        log.log({"rpm": 900})
        self.assertFalse(log.paused)
        self.assertEqual(len(self.rows()), 2)


class MultiFileTrainingTests(unittest.TestCase):
    def test_folder_and_files_merge_oldest_first(self):
        try:
            import pandas  # noqa: F401  (training needs pandas; Pi app does not)
        except ImportError:
            self.skipTest("pandas not installed")
        from autopi.build_features import expand_paths, load_raw
        root = tempfile.mkdtemp()
        try:
            _touch(os.path.join(root, "archive", "driving_log_v2_2026-10.csv"),
                   "timestamp,rpm\n2026-10-01 10:00:00,800\n")
            _touch(os.path.join(root, "archive", "warning_log_2026-10.csv"),
                   "timestamp,warning\n2026-10-01 10:00:00,x\n")
            live = os.path.join(root, "driving_log_v2.csv")
            _touch(live, "timestamp,rpm\n2026-11-01 10:00:00,900\n")
            paths = expand_paths([live, os.path.join(root, "archive")])
            self.assertEqual(len(paths), 2)            # warning log not included
            raw = load_raw([live, os.path.join(root, "archive")])
            self.assertEqual(list(raw["rpm"]), [800, 900])
        finally:
            _shutil.rmtree(root, ignore_errors=True)


class PermanentCodesTests(unittest.TestCase):
    """Mode 0A is asked for directly (python-OBD 0.7.2 has no command for it)."""

    class FakeCar:
        def __init__(self, replies=None, error=None):
            self.replies = replies or []
            self.error = error
            self.sent = []

        def uds_request(self, header, payload, timeout_hex="32"):
            self.sent.append((header, payload))
            if self.error:
                raise IOError(self.error)
            return self.replies

    def read(self, car):
        from autopi.diagnostics import PermanentCodesReader
        return PermanentCodesReader(car, None).read()

    def test_asks_the_car_with_mode_0a_on_broadcast(self):
        car = self.FakeCar([(0x7E8, bytes([0x4A, 0x00]))])
        self.assertIsNone(self.read(car))                 # answered: none stored
        self.assertEqual(car.sent, [("7DF", "0A")])

    def test_decodes_codes_and_module(self):
        car = self.FakeCar([(0x7E8, bytes([0x4A, 0x02, 0x20, 0x96, 0x04, 0x20]))])
        codes = self.read(car)
        self.assertEqual([c for c, _ in codes], ["P2096", "P0420"])
        self.assertIn("ENGINE", codes[0][1])

    def test_waits_through_busy_reply(self):
        car = self.FakeCar([(0x7E8, bytes([0x7F, 0x0A, 0x78])),
                            (0x7E8, bytes([0x4A, 0x01, 0x20, 0x96]))])
        self.assertEqual(self.read(car)[0][0], "P2096")

    def test_reports_why_when_it_cannot_read(self):
        self.assertIn("did not answer", self.read(self.FakeCar([])))
        self.assertIn("not supported",
                      self.read(self.FakeCar([(0x7E8, bytes([0x7F, 0x0A, 0x11]))])))
        self.assertIn("no connection", self.read(self.FakeCar(error="no connection")))

    def test_old_reply_without_count_byte(self):
        from autopi.diagnostics import decode_obd_dtc_reply
        self.assertEqual(decode_obd_dtc_reply(bytes([0x4A, 0x20, 0x96, 0x00, 0x00])),
                         ["P2096"])
        self.assertEqual(decode_obd_dtc_reply(bytes([0x4A, 0x01, 0xC1, 0x00])), ["U0100"])


class DirectionAwareAdviceTests(unittest.TestCase):
    """Advice must match WHICH WAY a reading is off (Nov 15 report: a cool,
    still-warming engine got overheating advice, and a rich trim got
    vacuum-leak advice, which causes LEAN)."""

    def setUp(self):
        self.book = ActionPlaybook()

    def test_cool_coolant_gets_warmup_thermostat_advice_not_overheat(self):
        steps = " ".join(self.book.steps_for_anomaly(
            {"culprits": [("coolant_temp", -7.0)]})).lower()
        self.assertIn("thermostat", steps)
        self.assertNotIn("fan runs", steps)

    def test_hot_coolant_gets_overheat_advice(self):
        steps = " ".join(self.book.steps_for_anomaly(
            {"culprits": [("coolant_temp", 5.0)]})).lower()
        self.assertIn("fan", steps)
        self.assertNotIn("thermostat", steps)

    def test_rich_trim_never_blames_vacuum_leak_or_air_filter(self):
        ml = " ".join(self.book.steps_for_anomaly(
            {"culprits": [("ltft_b1", -2.6)]})).lower()
        warn = " ".join(self.book.steps_for_warning(
            "FUEL TRIM DRIFT (LTFT -16%, rich)")).lower()
        for text in (ml, warn):
            self.assertIn("rich", text)
            self.assertNotIn("vacuum", text)
            self.assertNotIn("air filter", text)
            self.assertIn("purge", text)

    def test_lean_trim_points_at_vacuum_leak(self):
        ml = " ".join(self.book.steps_for_anomaly(
            {"culprits": [("ltft_b1", 3.0)]})).lower()
        self.assertIn("lean", ml)
        self.assertIn("vacuum", ml)

    def test_describe_says_lower_or_higher(self):
        names = self.book.describe_culprits({"culprits": [("coolant_temp", -7.0)]})
        self.assertIn("lower than normal", names[0])


class WarmingGuardTests(unittest.TestCase):
    """Coolant is not judged until the engine is fully warm."""

    class FakeDetector:
        ok = True
        _severity = "ok"

        def __init__(self):
            self.skips = []

        def score(self, feat, skip=None):
            self.skips.append(list(skip or []))
            return {"available": True, "severity": "ok", "culprits": []}

        def reset(self):
            pass

    def feed(self):
        from autopi.anomaly_feed import AnomalyFeed
        f = AnomalyFeed("models/definitely_missing.joblib",
                        warm_coolant_c=85, warm_run_min=10)
        f._det = self.FakeDetector()
        return f

    def test_80c_three_minutes_in_is_warming(self):
        f = self.feed()
        res = f.push({"rpm": 800, "coolant_temp": 80, "engine_load": 39, "run_time": 3})
        self.assertTrue(res["warming"])
        self.assertIn("coolant_temp", f._det.skips[-1])

    def test_short_run_time_alone_counts_as_warming(self):
        f = self.feed()
        res = f.push({"rpm": 800, "coolant_temp": 90, "engine_load": 30, "run_time": 4})
        self.assertTrue(res["warming"])

    def test_fully_warm_engine_is_judged(self):
        f = self.feed()
        res = f.push({"rpm": 800, "coolant_temp": 91, "engine_load": 30, "run_time": 25})
        self.assertFalse(res["warming"])
        self.assertEqual(f._det.skips[-1], [])

    def test_detector_skip_pins_feature_to_mean(self):
        from autopi.anomaly import AnomalyDetector

        class Ident:
            def transform(self, rows):
                return rows

        class Calm:
            def score_samples(self, X):
                return [0.0]                     # forest never alarms

        d = AnomalyDetector.__new__(AnomalyDetector)
        d.ok = True; d.ignore = set(); d._streak = 0; d._clean = 0
        d._severity = "ok"; d._fallback = "normal"
        d.features = ["coolant_temp", "rpm"]
        d._regimes = {"normal": {"scaler": Ident(), "model": Calm(), "threshold": -1.0,
                                 "baselines": {"coolant_temp": {"mean": 90.0, "std": 1.0},
                                               "rpm": {"mean": 800.0, "std": 50.0}}}}
        cold = {"coolant_temp": 80.0, "engine_load": 30.0, "rpm": 800.0}
        self.assertTrue(d.score(cold)["anomalous"])                     # judged: -10 sigma
        d.reset()
        res = d.score(cold, skip=["coolant_temp"])
        self.assertFalse(res["anomalous"])                               # not judged
        self.assertEqual(res["culprits"], [])


class ReportScrollTests(unittest.TestCase):
    """The last line of the REPORT must be reachable above the SEND TO PHONE bar."""

    def test_last_line_visible_above_bottom_bar(self):
        try:
            import pygame
            pygame.init()
        except Exception:
            self.skipTest("pygame not installed")
        from autopi.diag_screen import DiagScreen
        ds = DiagScreen(800, 480)
        ds.set_colored("REPORT", [("line " + str(i), "raw") for i in range(60)])
        ds.bottom_reserve = 44
        ds.scroll_by(999)
        surface = pygame.Surface((800, 480))
        ds.draw(surface)
        last_y = 66 + (ds._visible() - 1) * 24
        self.assertLess(last_y + 24, 480 - 44)                  # above the bar
        shown = ds._lines[ds._scroll:ds._scroll + ds._visible()]
        self.assertEqual(shown[-1][0], ds._lines[-1][0])        # really the last line
        ds.bottom_reserve = 0
        self.assertGreater(ds._visible(), (480 - 96 - 44) // 24)


class ReportWordingTests(unittest.TestCase):
    """Nov 15 report: fuel status printed as a Python tuple, and the engine's
    normal 'no UDS reply' looked like a failure."""

    def test_closed_loop_in_plain_words(self):
        from autopi.report_builder import describe_fuel_status
        short, note = describe_fuel_status(
            ("Closed loop, using oxygen sensor feedback to determine fuel mix", ""))
        self.assertEqual(short, "Closed loop (normal)")
        self.assertIn("oxygen sensor", note)
        self.assertNotIn("(", short.replace("(normal)", ""))      # no tuple

    def test_other_fuel_states(self):
        from autopi.report_builder import describe_fuel_status
        self.assertIn("cold", describe_fuel_status(
            ("Open loop due to insufficient engine temperature", ""))[0])
        self.assertIn("FAULT", describe_fuel_status(
            ("Open loop due to system failure", ""))[0])
        self.assertIn("sensor fault", describe_fuel_status(
            ("Closed loop, using at least one oxygen sensor but there is a fault "
             "in the feedback system", ""))[0])
        self.assertEqual(describe_fuel_status(("Something new", "")), ("Something new", ""))
        self.assertEqual(describe_fuel_status(None), (None, ""))

    def test_engine_no_uds_reply_is_labelled_normal(self):
        import tempfile
        from autopi.module_scan import ModuleScanner
        sc = ModuleScanner(lambda req, payload, timeout_hex="32": [],
                           {"cache": os.path.join(tempfile.mkdtemp(), "m.json")})
        eng = sc.read_dtcs({"req": "7E0"})
        self.assertFalse(eng["ok"])
        self.assertTrue(eng["obd_checked"])
        self.assertIn("OBD-II", eng["error"])
        items = " ".join(t for t, _k in sc._code_items(eng))
        self.assertIn("Not a problem", items)
        other = sc.read_dtcs({"req": "7B0"})
        self.assertFalse(other.get("obd_checked"))
        self.assertNotIn("OBD-II", other["error"])


class SettlingFaceTests(unittest.TestCase):
    """Cold start = blue face. Warm-ish engine waiting for the idle = its own
    color (white), never used by any other mood."""

    def setUp(self):
        self.clock = Clock()
        self.w = WarmupMonitor(clock=self.clock)

    def test_true_cold_start_is_blue(self):
        st = self.w.update(rpm=1400, coolant=10)
        self.assertEqual(st["mood"], "cold")
        self.assertEqual(FaceState().decide(coolant_temp=10, cold=True,
                                            settling=st["mood"] == "settling"), "cold")

    def test_warm_engine_idle_up_is_settling_color(self):
        st = self.w.update(rpm=1200, coolant=75)
        self.assertEqual(st["mood"], "settling")
        self.assertIn("IDLE STILL HIGH", st["headline"])
        self.assertEqual(FaceState().decide(coolant_temp=75, cold=True, settling=True),
                         "settling")

    def test_no_mood_once_ready(self):
        self.w.update(rpm=1200, coolant=92)
        for _ in range(7):
            self.clock.t += 1
            st = self.w.update(rpm=820, coolant=92)
        self.assertEqual(st["mood"], "")

    def test_settling_color_is_new(self):
        try:
            from autopi.face import MOODS, FACES
        except Exception:
            self.skipTest("pygame not installed")
        self.assertIn("settling", FACES)
        others = [m["rgb"] for k, m in MOODS.items() if k != "settling"]
        self.assertNotIn(MOODS["settling"]["rgb"], others)
        r, g, b = MOODS["settling"]["rgb"]
        self.assertTrue(min(r, g, b) > 200)            # white, unlike every other mood

    def test_warnings_still_beat_settling(self):
        self.assertEqual(FaceState().decide(coolant_temp=75, has_codes=True,
                                            cold=True, settling=True), "red")


class TalkScreenTests(unittest.TestCase):
    def test_driver_words_are_blue_without_you_prefix(self):
        try:
            import pygame
            pygame.init()
        except Exception:
            self.skipTest("pygame not installed")
        from autopi.diag_screen import DiagScreen
        ds = DiagScreen(800, 480)
        ds.set_colored("TALK TO AUTOPI", [("how is my car doing", "you"),
                                          ("Coolant is normal.", "ai")])
        text, color = ds._lines[0]
        self.assertEqual(text, "how is my car doing")
        r, g, b = color
        self.assertTrue(b > g > r)                      # blue = the driver's words
        r, g, b = ds._lines[1][1]
        self.assertTrue(b > r > g)                      # purple = AutoPi's answer

    def test_no_yellow_text_but_faces_untouched(self):
        """Nov 15 color scheme: plain TEXT is never yellow (purple = AutoPi,
        blue = the driver). Faces, face labels, gauges and graph lines keep
        their original colors - those must NOT change."""
        from autopi.diag_screen import DiagScreen
        from autopi.face import MOODS
        yellow = (255, 215, 40)
        self.assertNotIn(yellow, DiagScreen.KIND_COLORS.values())
        self.assertEqual(MOODS["yellow"]["rgb"], yellow)                 # warning face
        self.assertEqual(MOODS["yellow"]["text"], (185, 100, 255))
        self.assertEqual(MOODS["purple"]["rgb"], (185, 100, 255))        # camp face
        here = os.path.dirname(os.path.abspath(__file__))
        report = open(os.path.join(here, "..", "autopi", "report_builder.py")).read()
        self.assertNotIn("#ffd728", report)


class TalkScrollTests(unittest.TestCase):
    """TALK TO AUTOPI scrolls like every other screen: 3 lines per tap, and a
    new answer doesn't yank you away if you scrolled up to re-read."""

    def test_scroll_helpers(self):
        try:
            import pygame
            pygame.init()
        except Exception:
            self.skipTest("pygame not installed")
        from autopi.diag_screen import DiagScreen
        ds = DiagScreen(800, 480)
        ds.bottom_reserve = 44
        ds.set_colored("TALK TO AUTOPI", [("line " + str(i), "ai") for i in range(40)])
        ds.scroll_to_end()
        self.assertTrue(ds.at_end())
        ds.scroll_by(-3)
        self.assertEqual(ds._scroll, ds.max_scroll() - 3)
        self.assertFalse(ds.at_end())
        ds.set_colored("TALK TO AUTOPI", [("line " + str(i), "ai") for i in range(45)],
                       keep_scroll=True)
        ds.clamp_scroll()
        self.assertEqual(ds._scroll, 40 - ds._visible() - 3)   # stayed where you were


if __name__ == "__main__":
    unittest.main()
