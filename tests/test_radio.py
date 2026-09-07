import math
import unittest
from dataclasses import replace

from primorets.radio import RadioSettings
from primorets.session import Session


class RadioTests(unittest.TestCase):
    def test_reference_value_and_linear_friis(self):
        settings = RadioSettings()
        r = settings.at_range(40000)
        self.assertAlmostEqual(r["fspl_db"], 190.509583, places=6)
        self.assertAlmostEqual(r["received_dbm"], -90.509583, places=6)
        direct = 10 * 10**3 * 10**3 * (299792458 / 2e9 / (4 * math.pi * 4e7))**2
        self.assertAlmostEqual(r["received_w"] / direct, 1, places=12)

    def test_distance_frequency_power_gains_losses(self):
        s = RadioSettings()
        base = s.at_range(20000)
        twice = s.at_range(40000)
        self.assertAlmostEqual(base["received_w"] / twice["received_w"], 4)
        self.assertAlmostEqual(twice["fspl_db"] - base["fspl_db"], 6.020599913)
        self.assertAlmostEqual(replace(s, frequency_ghz=4).at_range(20000)["received_w"] / base["received_w"], 0.25)
        self.assertAlmostEqual(replace(s, tx_power_w=20).at_range(20000)["received_w"] / base["received_w"], 2)
        self.assertAlmostEqual(replace(s, rx_gain_dbi=40).at_range(20000)["received_w"] / base["received_w"], 10)
        self.assertAlmostEqual(replace(s, losses_db=3).at_range(20000)["received_dbm"], base["received_dbm"] - 3)

    def test_invalid_parameters_and_range(self):
        for kwargs in ({"frequency_ghz": 0}, {"tx_power_w": -1}, {"losses_db": -1}, {"rx_gain_dbi": float("nan")}):
            with self.assertRaises(ValueError):
                RadioSettings(**kwargs)
        for distance in (0, -10, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                RadioSettings().at_range(distance)

    def test_receding_satellite_closes_link_and_approach_restores_it(self):
        points = [{"utc": f"2026-09-07T00:0{i}:00Z", "azimuth_deg": 0,
                   "elevation_deg": 30, "range_km": distance}
                  for i, distance in enumerate((10000, 40000, 10000))]
        # Threshold at 30000 km: loss and recovery must result from distance alone.
        threshold = RadioSettings().at_range(30000)["received_dbm"]
        m = Session(points, 10, RadioSettings(threshold_dbm=threshold))
        m.start_run()
        m.advance(20)
        self.assertTrue(m.link)
        m.advance(35)
        self.assertFalse(m.link)
        self.assertEqual(m.state, "МОЩНОСТЬ ПРИЁМА НИЖЕ ПОРОГА")
        count = m.packets
        commands = m.commands_sent
        m.advance(10)
        self.assertEqual(m.packets, count)
        self.assertGreater(m.commands_sent, commands)
        m.advance(35)
        self.assertTrue(m.link)
        self.assertGreater(m.packets, count)

    def test_settings_change_clears_lock_and_preserves_applied_settings_on_reset(self):
        points = [{"utc": f"2026-09-07T00:0{i}:00Z", "azimuth_deg": 0,
                   "elevation_deg": 30, "range_km": 40000} for i in (0, 1)]
        m = Session(points, 10)
        m.start_run()
        m.advance(20)
        self.assertTrue(m.link)
        settings = RadioSettings(threshold_dbm=0)
        m.set_radio(settings)
        self.assertFalse(m.link)
        count = m.packets
        m.advance(10)
        self.assertEqual(m.packets, count)
        m.reset()
        self.assertEqual(m.radio, settings)


if __name__ == "__main__":
    unittest.main()

