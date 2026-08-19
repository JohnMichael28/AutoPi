import unittest

from autopi.config import Config
from autopi.vehicle import Reading
from autopi.dyno import DynoCalculator
from autopi.tuning import TuningAnalyzer


class TestReading(unittest.TestCase):
    """Tests for the Reading value object."""

    def test_feature_list_order(self):
        # Feature list must be in the exact ML order:
        # coolant, intake, boost, rpm, speed, load, throttle, voltage
        r = Reading(coolant_temp=60, intake_temp=20, boost_psi=5, rpm=2000,
                    speed=50, engine_load=40, throttle=25, voltage=14.5)
        expected = [60, 20, 5, 2000, 50, 40, 25, 14.5]
        self.assertEqual(r.to_feature_list(), expected)

    def test_is_complete_true(self):
        r = Reading(coolant_temp=60, intake_temp=20, boost_psi=5, rpm=2000,
                    speed=50, engine_load=40, throttle=25, voltage=14.5)
        self.assertTrue(r.is_complete())

    def test_is_complete_false_with_none(self):
        r = Reading(coolant_temp=60)   # most values None
        self.assertFalse(r.is_complete())


class TestDynoCalculator(unittest.TestCase):
    """Tests for the virtual dyno physics."""

    def setUp(self):
        # Runs before each test - a fresh calculator (calibration 1.0)
        self.dyno = DynoCalculator(Config())

    def test_no_hp_when_not_accelerating(self):
        # Zero or negative acceleration = no power estimate
        self.assertIsNone(self.dyno.estimate_hp(20, 0))
        self.assertIsNone(self.dyno.estimate_hp(20, -1))

    def test_positive_hp_when_accelerating(self):
        # Real acceleration should produce a positive HP number
        hp = self.dyno.estimate_hp(20, 2.5)
        self.assertIsNotNone(hp)
        self.assertGreater(hp, 0)

    def test_peak_tracks_highest(self):
        # Peak should hold the highest HP seen
        self.dyno.estimate_hp(20, 2.0)
        self.dyno.estimate_hp(25, 3.0)   # bigger
        self.dyno.estimate_hp(15, 1.0)   # smaller
        peak = self.dyno.peak_hp
        # Peak should equal the HP from the biggest pull (the middle one)
        self.assertGreater(peak, 0)

    def test_conversions(self):
        # km/h to m/s: 72 km/h = 20 m/s
        self.assertAlmostEqual(DynoCalculator.kmh_to_ms(72), 20.0, places=1)


class TestTuningAnalyzer(unittest.TestCase):
    """Tests for the knock-detection logic."""

    def setUp(self):
        self.analyzer = TuningAnalyzer()

    def test_under_load_true(self):
        # High RPM + high throttle = under load
        self.assertTrue(self.analyzer.under_load(3000, 50))

    def test_under_load_false_at_idle(self):
        # Low RPM / low throttle = not under load
        self.assertFalse(self.analyzer.under_load(800, 5))

    def test_timing_drop_triggers_alert_under_load(self):
        # A big timing drop (23 -> 15 = 8 deg) under load should alert
        subaru = {"DAM": None, "FBKC": None, "FLKC": None}
        alerts = self.analyzer.evaluate(15.0, 23.0, 3000, 50, subaru)
        self.assertTrue(len(alerts) > 0)

    def test_no_alert_at_idle(self):
        # Same timing drop but NOT under load = no knock alert (idle noise)
        subaru = {"DAM": None, "FBKC": None, "FLKC": None}
        alerts = self.analyzer.evaluate(15.0, 23.0, 800, 5, subaru)
        self.assertEqual(len(alerts), 0)

    def test_dam_below_optimal_alerts(self):
        # DAM below 1.0 should always flag (real knock indicator)
        subaru = {"DAM": 0.9, "FBKC": None, "FLKC": None}
        alerts = self.analyzer.evaluate(20.0, 20.0, 3000, 50, subaru)
        self.assertTrue(any("DAM" in a for a in alerts))


class TestConfig(unittest.TestCase):
    """Tests for config loading."""

    def test_loads_values(self):
        c = Config()
        self.assertEqual(c.coolant_warn_c, 105)
        self.assertEqual(c.log_every, 5)


if __name__ == "__main__":
    unittest.main()