import json
import math
import socket
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
from skyfield.framelib import itrs

from primorets.catalog import Catalog
from primorets.core import (OrbitFilter, Station, TS, demo_records, export_csv,
                           parse_file, parse_utc, trajectory)


class OrbitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_filter_and_deep_space_model(self):
        records = demo_records()
        self.assertEqual([OrbitFilter().accepts(r) for r in records], [True, True, False])
        self.assertEqual(records[0].satellite().model.method, "d")
        self.assertAlmostEqual(records[0].properties()["period_hours"], 24 / 2.006, places=8)

    def test_catalog_persistence_newer_elements_and_demo_isolation(self):
        r = demo_records()[0]
        catalog = Catalog(self.root / "catalog.db")
        real = replace(r, demo=False)
        self.assertEqual(catalog.import_records([r, real]), (2, 0))
        older = replace(real, payload=dict(real.payload, EPOCH="2026-09-06T00:00:00.000000"))
        self.assertEqual(catalog.import_records([older]), (0, 1))
        newer = replace(real, payload=dict(real.payload, EPOCH="2026-09-08T00:00:00.000000"))
        self.assertEqual(catalog.import_records([newer]), (1, 0))
        catalog.close()
        reopened = Catalog(self.root / "catalog.db")
        self.addCleanup(reopened.close)
        self.assertEqual(len(reopened.all()), 2)
        epochs = {key: record.properties()["epoch"].day for key, record in reopened.all()}
        self.assertEqual(epochs, {"demo:99001": 7, "real:99001": 8})

    def test_json_import_and_invalid_orbit(self):
        path = self.root / "satellites.json"
        path.write_text(json.dumps([r.payload for r in demo_records()]), encoding="utf-8")
        self.assertEqual(len(parse_file(path)), 3)
        path.write_text(json.dumps([dict(demo_records()[0].payload, ECCENTRICITY=1.5)]), encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_file(path)

    def test_tle_and_published_vallado_epoch_vector(self):
        # Vallado SGP4 verification case 00005, distributed with python-sgp4.
        l1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
        l2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"
        path = self.root / "test.tle"
        path.write_text(f"VANGUARD 1\n{l1}\n{l2}\n", encoding="utf-8")
        r = parse_file(path)[0]
        model = r.satellite().model
        code, position, velocity = model.sgp4(model.jdsatepoch, model.jdsatepochF)
        self.assertEqual(code, 0)
        np.testing.assert_allclose(position, [7022.46529266, -1400.08296755, 0.03995155], atol=1e-7, rtol=0)
        path.write_text(l1[:-1] + "0\n" + l2, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "контрольная"):
            parse_file(path)

    def test_pointing_against_independent_enu_geometry_at_equator(self):
        record = demo_records()[0]
        start = parse_utc("2026-09-07T00:00:00Z")
        station = Station(0, 0, 0, 10)
        point = trajectory(record, station, start, hours=1, step_seconds=3600)[0]
        # At lat=lon=0, up=X, east=Y, north=Z, observer=(WGS84 a,0,0).
        xyz = record.satellite().at(TS.from_datetime(start)).frame_xyz(itrs).km
        up, east, north = xyz - np.array([6378.137, 0, 0])
        distance = math.sqrt(up * up + east * east + north * north)
        expected_az = math.degrees(math.atan2(east, north)) % 360
        expected_el = math.degrees(math.asin(up / distance))
        self.assertAlmostEqual(point["azimuth_deg"], expected_az, places=7)
        self.assertAlmostEqual(point["elevation_deg"], expected_el, places=7)
        self.assertAlmostEqual(point["range_km"], distance, places=6)

    def test_offline_trajectory_timezones_visibility_export(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            points = trajectory(demo_records()[0], Station(55, 37, 200),
                                parse_utc("2026-09-07T03:00:00+03:00"), 24, 60)
        self.assertEqual(len(points), 1441)
        self.assertEqual(points[0]["utc"], "2026-09-07T00:00:00+00:00")
        self.assertEqual(points[-1]["utc"], "2026-09-08T00:00:00+00:00")
        for p in points:
            self.assertTrue(0 <= p["azimuth_deg"] < 360)
            self.assertTrue(-90 <= p["elevation_deg"] <= 90)
            self.assertEqual(p["visible"], p["elevation_deg"] >= 10)
        path = self.root / "track.csv"
        export_csv(path, points)
        self.assertEqual(len(path.read_text(encoding="utf-8-sig").splitlines()), 1442)

    def test_invalid_coordinates_and_excessive_grid(self):
        for args in ((91, 0, 0), (0, 181, 0), (float("nan"), 0, 0)):
            with self.assertRaises(ValueError):
                Station(*args)
        with self.assertRaises(ValueError):
            OrbitFilter(max_period=-1)
        with self.assertRaises(ValueError):
            trajectory(demo_records()[0], Station(0, 0, 0), parse_utc("2026-09-07"), 168, 1)


if __name__ == "__main__":
    unittest.main()

