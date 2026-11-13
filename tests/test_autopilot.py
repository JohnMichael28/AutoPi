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
        st = self.step(1, 2000, 71, speed=40)
        self.assertEqual(st["phase"], ALL_CLEAR)
        self.assertFalse(st["cold"])
        st = self.step(9, 2000, 80, speed=40)
        self.assertEqual(st["phase"], WARM)
        self.assertEqual(st["headline"], "")

    def test_idle_stays_high_gives_go_after_max_wait(self):
        self.step(0, 1500, -5)
        st = None
        for _ in range(91):
            st = self.step(1, 1300, -3)
        self.assertEqual(st["phase"], DRIVE_GENTLY)

    def test_warm_restart_skips(self):
        st = self.step(0, 900, 88)
        self.assertEqual(st["phase"], WARM)
        self.assertFalse(st["cold"])

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
        self.assertEqual(names[0], "coolant (avg) +4.4 sigma from normal")


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
            warmup_fn=lambda: {"cold": False, "phase": "warm"})

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
        # result header + 8 numbered sections, always in the same order
        self.assertEqual(len(heads), 9)
        self.assertTrue(heads[1].startswith("--- 1. STORED CODES"))
        self.assertTrue(heads[8].startswith("--- 8. ML ANOMALY MONITOR"))
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

    def test_unreachable_home_is_skipped_after_first_failure(self):
        import autopi.ai_client as mod
        c = self.make()
        posts = []

        def fake_post(url, **kw):
            posts.append(url)
            raise mod.requests.exceptions.ConnectionError("no route")
        old = mod.requests.post
        mod.requests.post = fake_post
        c._AIClient__finder.find = lambda: None
        try:
            self.assertTrue(c.ask("a").startswith("AI unavailable"))
            first = len(posts)
            self.assertTrue(c.ask("b").startswith("AI unavailable"))
        finally:
            mod.requests.post = old
        self.assertEqual(len(posts) - first, 1)   # only the local try; home skipped


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
                         warmup_fn=lambda: {"cold": False})
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


class AnomalyStatusTests(unittest.TestCase):
    def test_missing_model_reports_reason(self):
        from autopi.anomaly_feed import AnomalyFeed
        feed = AnomalyFeed("models/definitely_missing.joblib")
        st = feed.status()
        self.assertFalse(st["loaded"])
        self.assertTrue(st["reason"])
        self.assertEqual(feed.push({"rpm": 800})["severity"], "ok")


if __name__ == "__main__":
    unittest.main()
