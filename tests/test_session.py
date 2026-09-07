import unittest

from primorets.session import Session, angle_delta


def fixed_track(az=30, el=35, duration=180):
    return [{"utc": "2026-09-07T00:00:00+00:00", "azimuth_deg": az, "elevation_deg": el, "range_km": 40000},
            {"utc": f"2026-09-07T00:{duration // 60:02d}:{duration % 60:02d}+00:00", "azimuth_deg": az, "elevation_deg": el, "range_km": 40000}]


class SessionTests(unittest.TestCase):
    def test_acquisition_disturbance_and_recovery(self):
        m = Session(fixed_track(), 10)
        m.start_run()
        m.advance(30)
        self.assertTrue(m.link)
        self.assertGreater(m.packets, 0)
        self.assertLess(m.error, 0.5)
        self.assertGreater(m.commands_acked, 0)
        m.disturb()
        self.assertFalse(m.link)
        m.advance(10)
        self.assertTrue(m.link)

    def test_fault_holds_drives_and_prevents_packets(self):
        m = Session(fixed_track(), 10)
        m.start_run()
        m.advance(30)
        m.set_fault(True)
        before = (m.az, m.el, m.packets, m.commands_acked)
        m.advance(10)
        self.assertEqual(before, (m.az, m.el, m.packets, m.commands_acked))
        self.assertFalse(m.link)
        m.set_fault(False)
        m.advance(10)
        self.assertTrue(m.link)

    def test_pause_stop_reset_and_completion(self):
        m = Session(fixed_track(duration=60), 10)
        m.start_run()
        m.advance(20)
        m.pause()
        elapsed = m.elapsed
        m.advance(20)
        self.assertEqual(m.elapsed, elapsed)
        m.start_run()
        m.advance(40)
        self.assertTrue(m.finished)
        self.assertFalse(m.link)
        m.reset()
        self.assertEqual(m.packets, 0)
        m.start_run()
        m.stop()
        m.start_run()
        m.advance(20)
        self.assertEqual(m.elapsed, 0)
        self.assertIsNone(m.command)

    def test_invisible_no_transmission_or_commands(self):
        m = Session(fixed_track(el=-10), 10)
        m.start_run()
        m.advance(30)
        self.assertEqual(m.commands_sent, 0)
        self.assertEqual(m.packets, 0)
        self.assertFalse(m.link)

    def test_auto_off_and_angle_wrap(self):
        points = fixed_track(az=359, duration=60)
        points[1]["azimuth_deg"] = 1
        m = Session(points, 10)
        m.elapsed = 30
        self.assertAlmostEqual(m.target()[0], 0)
        self.assertEqual(angle_delta(1, 359), 2)
        m.set_auto(False)
        m.start_run()
        m.advance(10)
        self.assertEqual((m.az, m.el, m.commands_sent), (0, 0, 0))

    def test_loss_of_visibility_closes_link(self):
        points = fixed_track(az=0, el=15, duration=120)
        points[1]["elevation_deg"] = 0
        m = Session(points, 10)
        m.start_run()
        m.advance(15)
        self.assertTrue(m.link)
        m.advance(40)
        packets = m.packets
        self.assertFalse(m.link)
        m.advance(10)
        self.assertEqual(m.packets, packets)


if __name__ == "__main__":
    unittest.main()

