"""Deterministic teaching simulator. No hardware, network, or RF operations."""
from bisect import bisect_right
from collections import deque
from datetime import timedelta
import math

from .core import parse_utc


def angle_delta(target, actual):
    return (target - actual + 180) % 360 - 180


class Session:
    def __init__(self, points, mask):
        if len(points) < 2:
            raise ValueError("Для сеанса нужны минимум две точки траектории")
        self.points = [dict(p) for p in points]
        self.start = parse_utc(points[0]["utc"])
        self.times = [(parse_utc(p["utc"]) - self.start).total_seconds() for p in points]
        if self.times[0] != 0 or any(b <= a for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Время точек должно строго возрастать")
        self.mask = mask
        self.duration = self.times[-1]
        self.events = deque(maxlen=2000)
        self.event_count = 0
        self.reset()

    def log(self, text):
        self.event_count += 1
        self.events.append({"seq": self.event_count, "utc": self.utc, "message": text})

    @property
    def utc(self):
        return (self.start + timedelta(seconds=self.elapsed)).isoformat()

    def reset(self, offset=0):
        self.elapsed = max(0, min(float(offset), self.duration))
        self.az, self.el = 0.0, 0.0
        self.command = None
        self.pending = None
        self.auto = True
        self.fault = False
        self.running = False
        self.finished = False
        self.stopped = False
        self.commands_sent = self.commands_acked = self.packets = 0
        self.packet_fraction = self.lock_time = 0.0
        self.link = False
        self.last_send = -math.inf
        self.events.clear()
        self.event_count = 0
        self.log("ИМИТАЦИЯ: виртуальный контроллер готов; антенна AZ=0°, EL=0°")

    def target(self):
        i = min(max(0, bisect_right(self.times, self.elapsed) - 1), len(self.times) - 2)
        p, q = self.points[i:i + 2]
        fraction = (self.elapsed - self.times[i]) / (self.times[i + 1] - self.times[i])
        az = (p["azimuth_deg"] + fraction * angle_delta(q["azimuth_deg"], p["azimuth_deg"])) % 360
        el = p["elevation_deg"] + fraction * (q["elevation_deg"] - p["elevation_deg"])
        return az, el

    @property
    def error(self):
        az, el = self.target()
        # Angular separation of pointing directions, including zenith geometry.
        a, b = math.radians(el), math.radians(self.el)
        dot = math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(math.radians(az - self.az))
        return math.degrees(math.acos(max(-1, min(1, dot))))

    @property
    def state(self):
        if self.finished:
            return "СЕАНС ЗАВЕРШЁН"
        if self.stopped:
            return "ОСТАНОВЛЕНО"
        if not self.running:
            return "ПАУЗА / ГОТОВНОСТЬ"
        if self.fault:
            return "НЕТ ОТВЕТА КОНТРОЛЛЕРА"
        if self.target()[1] < self.mask:
            return "ОЖИДАНИЕ ВИДИМОСТИ"
        if self.link:
            return "УЧЕБНАЯ СВЯЗЬ УСТАНОВЛЕНА"
        return "НАВЕДЕНИЕ / ЗАХВАТ"

    def start_run(self):
        if self.finished or self.stopped:
            return
        self.running = True
        self.log("Пуск: расчёт → команда → подтверждение → контроль положения")

    def pause(self):
        self.running = False
        self.log("Пауза: модель времени, приводов и передачи заморожена")

    def stop(self):
        self.running = False
        self.stopped = True
        self.link = False
        self.lock_time = 0
        self.command = self.pending = None
        self.log("СТОП: движение и учебная передача прекращены; для нового сеанса нажмите Сброс")

    def set_auto(self, enabled):
        self.auto = bool(enabled)
        if not self.auto:
            self.command = self.pending = None
        self.log("Автосопровождение включено" if enabled else "Автосопровождение отключено: удержание положения")

    def set_fault(self, enabled):
        self.fault = bool(enabled)
        self.pending = self.command = None
        self.link = False
        self.lock_time = 0
        self.log("ИМИТАЦИЯ ОТКАЗА: нет ответов, приводы заморожены" if enabled else "Ответы восстановлены; повторное наведение")

    def disturb(self):
        self.az = (self.az + 8) % 360
        self.el = max(0, min(90, self.el - 4))
        self.link = False
        self.lock_time = 0
        self.log("ВОЗМУЩЕНИЕ: смещение азимута +8°, угла места −4° (с ограничением 0…90°)")

    def advance(self, seconds):
        if not math.isfinite(seconds) or not 0 <= seconds <= 120:
            raise ValueError("Шаг модели должен быть от 0 до 120 секунд")
        remaining = seconds
        while self.running and remaining > 1e-9:
            dt = min(0.2, remaining, self.duration - self.elapsed)
            if dt <= 1e-9:
                self.finished = True
                self.running = False
                self.link = False
                self.pending = self.command = None
                self.log("Конец расчётного интервала: сеанс завершён")
                break
            self.elapsed += dt
            remaining -= dt
            az, el = self.target()
            visible = el >= self.mask
            if not visible:
                self.pending = self.command = None
            if self.auto and visible and self.elapsed - self.last_send >= 1 - 1e-8:
                self.last_send = self.elapsed
                self.commands_sent += 1
                self.log(f"РАСЧЁТ / TX #{self.commands_sent}: AZ={az:.3f}°, EL={el:.3f}° → виртуальный привод")
                if not self.fault:
                    self.pending = (self.elapsed + 0.4, az, max(0, min(90, el)))
                else:
                    self.log("TIMEOUT: подтверждение не получено")
            if self.pending and self.elapsed + 1e-8 >= self.pending[0]:
                _, ca, ce = self.pending
                self.command = (ca, ce)
                self.pending = None
                self.commands_acked += 1
                self.log(f"ACK #{self.commands_acked}: команда принята; обратная связь AZ={self.az:.2f}°, EL={self.el:.2f}°")
            if self.command and self.auto and not self.fault:
                ca, ce = self.command
                self.az = (self.az + max(-6 * dt, min(6 * dt, angle_delta(ca, self.az)))) % 360
                self.el += max(-3 * dt, min(3 * dt, ce - self.el))
            locked = visible and not self.fault and self.error <= 0.5
            self.lock_time = self.lock_time + dt if locked else 0
            old_link = self.link
            self.link = self.lock_time >= 2
            if old_link != self.link:
                self.log("ЗАХВАТ: учебный канал открыт, 10 условных пакетов/с" if self.link else "ПОТЕРЯ ЗАХВАТА: учебная передача приостановлена")
            if self.link:
                self.packet_fraction += dt * 10
                packets = int(self.packet_fraction)
                self.packets += packets
                self.packet_fraction -= packets
            if self.elapsed >= self.duration - 1e-8:
                self.finished = True
                self.running = False
                self.link = False
                self.command = self.pending = None
                self.log("Конец расчётного интервала: сеанс завершён")



