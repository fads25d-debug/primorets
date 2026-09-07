"""Offline orbit calculations; angles are geometric, times are UTC."""
import csv
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from skyfield.api import EarthSatellite, load, wgs84

TS = load.timescale(builtin=True)


@dataclass
class Record:
    name: str
    kind: str
    payload: dict
    source: str
    demo: bool = False

    def satellite(self):
        if self.kind == "tle":
            return EarthSatellite(self.payload["line1"], self.payload["line2"], self.name, TS)
        return EarthSatellite.from_omm(TS, self.payload)

    def properties(self):
        sat = self.satellite()
        m = sat.model
        return {
            "norad": m.satnum,
            "epoch": sat.epoch.utc_datetime(),
            "eccentricity": m.ecco,
            "inclination": math.degrees(m.inclo),
            "period_hours": 2 * math.pi / m.no_kozai / 60,
            # SGP4 mean semimajor axis; descriptive filter, not osculating altitude.
            "apogee_km": (m.a * (1 + m.ecco) - 1) * m.radiusearthkm,
        }


def validate(record):
    sat = record.satellite()
    m = sat.model
    if not all(math.isfinite(v) for v in (m.ecco, m.inclo, m.no_kozai, m.a)):
        raise ValueError("Орбитальные параметры должны быть конечными числами")
    if not 0 <= m.ecco < 1 or not 0 <= m.inclo <= math.pi or m.no_kozai <= 0:
        raise ValueError("Недопустимые параметры орбиты")
    pos = sat.at(sat.epoch)
    if pos.message or not np.isfinite(pos.position.km).all():
        raise ValueError(f"Ошибка SGP4 на эпоху элементов: {pos.message}")
    return record


def parse_file(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    records = []
    if path.suffix.lower() == ".json":
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError("Ожидается массив записей GP JSON")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Запись GP JSON должна быть объектом")
            # CelesTrak GP JSON omits these default OMM metadata fields.
            row = dict(row)
            for key, value in {"CENTER_NAME": "EARTH", "REF_FRAME": "TEME",
                               "TIME_SYSTEM": "UTC", "MEAN_ELEMENT_THEORY": "SGP4"}.items():
                row.setdefault(key, value)
                if row[key] != value:
                    raise ValueError(f"Не поддерживается {key}={row[key]}")
            records.append(Record(str(row.get("OBJECT_NAME", row.get("NORAD_CAT_ID", ""))),
                                  "omm", row, path.name))
    else:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        i = 0
        while i < len(lines):
            name = ""
            if not lines[i].startswith("1 "):
                name = lines[i].removeprefix("0 ")
                i += 1
            if i + 1 >= len(lines):
                raise ValueError("Неполная пара строк TLE")
            l1, l2 = lines[i:i + 2]
            for number, line in ((1, l1), (2, l2)):
                if len(line) != 69 or not line.startswith(f"{number} "):
                    raise ValueError("Строка TLE должна содержать 69 символов и номер строки")
                checksum = sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in line[:68]) % 10
                if not line[68].isdigit() or checksum != int(line[68]):
                    raise ValueError("Не совпадает контрольная сумма TLE")
            if l1[2:7] != l2[2:7]:
                raise ValueError("Разные номера спутника в строках TLE")
            records.append(Record(name or l1[2:7].strip(), "tle", {"line1": l1, "line2": l2}, path.name))
            i += 2
    if not records:
        raise ValueError("Файл не содержит спутников")
    # Validate everything before allowing any database changes.
    for record in records:
        validate(record)
    return records


@dataclass
class OrbitFilter:
    min_e: float = 0.25
    min_apogee: float = 20000
    min_period: float = 0
    max_period: float = 48

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.min_e, self.min_apogee, self.min_period, self.max_period)):
            raise ValueError("Фильтры должны быть конечными числами")
        if not 0 <= self.min_e < 1 or self.min_apogee < 0 or not 0 <= self.min_period < self.max_period:
            raise ValueError("Проверьте границы фильтра орбит")

    def accepts(self, record):
        p = record.properties()
        return (p["eccentricity"] >= self.min_e and p["apogee_km"] >= self.min_apogee
                and self.min_period <= p["period_hours"] <= self.max_period)


@dataclass
class Station:
    latitude: float
    longitude: float
    elevation_m: float
    min_altitude: float = 10

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.latitude, self.longitude, self.elevation_m, self.min_altitude)):
            raise ValueError("Координаты должны быть конечными числами")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("Широта: −90…90°, долгота: −180…180°")
        if not -500 <= self.elevation_m <= 10000 or not 0 <= self.min_altitude <= 90:
            raise ValueError("Высота: −500…10000 м, маска горизонта: 0…90°")


def parse_utc(text):
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def trajectory(record, station, start, hours=24, step_seconds=60):
    if start.tzinfo is None:
        raise ValueError("Время должно содержать часовой пояс")
    if not math.isfinite(hours) or not math.isfinite(step_seconds) or not 0 < hours <= 168 or not 1 <= step_seconds <= 3600:
        raise ValueError("Интервал: до 168 ч, шаг: 1…3600 с")
    count = int(hours * 3600 / step_seconds) + 1
    if count > 25000:
        raise ValueError("Слишком много точек: увеличьте шаг (максимум 25000)")
    start = start.astimezone(timezone.utc)
    dates = [start + timedelta(seconds=i * step_seconds) for i in range(count)]
    times = TS.from_datetimes(dates)
    sat = record.satellite()
    geocentric = sat.at(times)
    if any(message for message in geocentric.message):
        raise ValueError("SGP4 не может рассчитать этот интервал; проверьте орбитальные данные")
    observer = wgs84.latlon(station.latitude, station.longitude, elevation_m=station.elevation_m)
    alt, az, distance = (sat - observer).at(times).altaz()
    if not np.isfinite([alt.degrees, az.degrees, distance.km]).all():
        raise ValueError("Расчёт вернул неопределённые координаты")
    return [{"utc": date.isoformat(), "azimuth_deg": float(a), "elevation_deg": float(e),
             "range_km": float(d), "visible": bool(e >= station.min_altitude)}
            for date, a, e, d in zip(dates, az.degrees, alt.degrees, distance.km)]


def export_csv(path, points):
    if not points:
        raise ValueError("Сначала рассчитайте траекторию")
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(points[0]))
        writer.writeheader()
        writer.writerows(points)


def demo_records():
    """Fictional elements; identifiers never enter the real catalog namespace."""
    records = []
    for number, name, e, motion, inc in (
        (99001, "УЧЕБНЫЙ • ВЭО 12 ч", 0.72, 2.006, 63.4),
        (99002, "УЧЕБНЫЙ • ВЭО 24 ч", 0.30, 1.0027, 63.4),
        (99003, "УЧЕБНЫЙ • Низкая орбита", 0.001, 15.2, 51.6),
    ):
        payload = dict(OBJECT_NAME=name, OBJECT_ID="2026-001A", EPOCH="2026-09-07T00:00:00.000000",
                       MEAN_MOTION=motion, ECCENTRICITY=e, INCLINATION=inc,
                       RA_OF_ASC_NODE=45, ARG_OF_PERICENTER=270, MEAN_ANOMALY=180,
                       EPHEMERIS_TYPE=0, CLASSIFICATION_TYPE="U", NORAD_CAT_ID=number,
                       ELEMENT_SET_NO=1, REV_AT_EPOCH=1, BSTAR=0, MEAN_MOTION_DOT=0,
                       MEAN_MOTION_DDOT=0, CENTER_NAME="EARTH", REF_FRAME="TEME",
                       TIME_SYSTEM="UTC", MEAN_ELEMENT_THEORY="SGP4")
        records.append(validate(Record(name, "omm", payload, "Вымышленные учебные данные", True)))
    return records

