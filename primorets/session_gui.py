import json
import math
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .session import Session


class SessionWindow(tk.Toplevel):
    def __init__(self, parent, points, metadata):
        super().__init__(parent)
        self.title("Приморец • Имитация сеанса связи")
        self.geometry("1150x830")
        self.minsize(1000, 760)
        self.metadata = dict(metadata)
        self.model = Session(points, metadata["horizon_mask_deg"])
        self.last_event = 0
        self.last_wall = time.monotonic()
        self.timer = None
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="УЧЕБНАЯ ИМИТАЦИЯ СЕАНСА", font=("Arial", 19, "bold")).pack(anchor="w")
        ttk.Label(body, text=f"{metadata['satellite']} • Станция {metadata['station_lat_deg']}°, {metadata['station_lon_deg']}°\n"
                  "Орбита — из последнего расчёта; контроллер, приводы и обмен пакетами — виртуальные.", wraplength=1080).pack(anchor="w", pady=5)
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=6)
        for label, command in [("▶ Пуск", self.start), ("Пауза", self.pause), ("■ Стоп", self.stop),
                                ("Сброс", self.reset), ("С начала видимости", self.visible_start)]:
            ttk.Button(controls, text=label, command=command).pack(side="left", padx=(0, 5))
        ttk.Label(controls, text="Скорость времени:").pack(side="left", padx=5)
        self.speed = tk.StringVar(value="10")
        ttk.Combobox(controls, textvariable=self.speed, values=("1", "10", "30", "60"), state="readonly", width=5).pack(side="left")
        self.state = tk.StringVar()
        ttk.Label(body, textvariable=self.state, font=("Arial", 13, "bold")).pack(anchor="w", pady=5)
        self.progress = ttk.Progressbar(body, maximum=self.model.duration)
        self.progress.pack(fill="x")
        faults = ttk.Frame(body)
        faults.pack(fill="x", pady=8)
        self.auto = tk.BooleanVar(value=True)
        self.fault = tk.BooleanVar(value=False)
        ttk.Checkbutton(faults, text="Автосопровождение", variable=self.auto, command=self.change_auto).pack(side="left")
        ttk.Checkbutton(faults, text="Имитировать отказ контроллера", variable=self.fault, command=self.change_fault).pack(side="left", padx=10)
        ttk.Button(faults, text="Сместить антенну", command=self.disturb).pack(side="left")
        ttk.Button(faults, text="Сохранить отчёт JSON", command=self.save).pack(side="right")
        self.canvas = tk.Canvas(body, bg="#122234", height=280, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.telemetry = tk.StringVar()
        ttk.Label(body, textvariable=self.telemetry, font=("Arial", 11), wraplength=1090).pack(fill="x", pady=8)
        ttk.Label(body, text="Цепочка: интерполяция орбиты → TX команды → ACK → движение → сравнение углов → учебные пакеты").pack(anchor="w")
        frame = ttk.LabelFrame(body, text="Журнал виртуального обмена — последние 2000 событий", padding=5)
        frame.pack(fill="both", expand=True, pady=8)
        self.log = tk.Text(frame, height=8, bg="#182b3e", fg="#d5e4f3", font=("Consolas", 10), wrap="word", state="disabled")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(body, text="Учебная модель: AZ 6°/с, EL 3°/с; захват ≤0,5° за 2 с; ACK через 0,4 с. "
                  "Это не характеристики реальной станции.", wraplength=1090).pack(anchor="w")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.render()
        self.timer = self.after(100, self.tick)

    def start(self):
        self.last_wall = time.monotonic()
        self.model.start_run()
        self.render()

    def pause(self):
        self.model.pause()
        self.render()

    def stop(self):
        self.model.stop()
        self.render()

    def reset(self, offset=0):
        self.model.reset(offset)
        self.auto.set(True)
        self.fault.set(False)
        self.last_event = 0
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.render()

    def visible_start(self):
        for seconds, point in zip(self.model.times[:-1], self.model.points[:-1]):
            if point["elevation_deg"] >= self.model.mask:
                self.reset(seconds)
                self.model.log("Переход к первой видимой точке расчётной сетки")
                self.render()
                return
        messagebox.showinfo("Нет видимости", "В этом интервале нет видимых точек для начала сеанса. Измените время или станцию в главном окне и пересчитайте.", parent=self)

    def change_auto(self):
        self.model.set_auto(self.auto.get())
        self.render()

    def change_fault(self):
        self.model.set_fault(self.fault.get())
        self.render()

    def disturb(self):
        self.model.disturb()
        self.render()

    def tick(self):
        now = time.monotonic()
        # Avoid fast-forwarding through a long OS suspension or modal dialog.
        elapsed = min(now - self.last_wall, 0.5)
        self.last_wall = now
        self.model.advance(elapsed * float(self.speed.get()))
        self.render()
        self.timer = self.after(100, self.tick)

    def render(self):
        m = self.model
        az, el = m.target()
        self.state.set(f"{m.state}  |  Модель UTC: {m.utc[:19].replace('T', ' ')}")
        self.progress["value"] = m.elapsed
        self.telemetry.set(f"Цель: AZ {az:.2f}° / EL {el:.2f}°     Антенна: AZ {m.az:.2f}° / EL {m.el:.2f}°     "
                           f"Ошибка: {m.error:.3f}°\nКоманды TX: {m.commands_sent}  /  ACK: {m.commands_acked}     "
                           f"Учебные пакеты: {m.packets}     Канал: {'ОТКРЫТ' if m.link else 'ЗАКРЫТ'}")
        new_events = [e for e in m.events if e["seq"] > self.last_event]
        if new_events:
            self.log.configure(state="normal")
            for event in new_events:
                self.log.insert("end", f"{event['utc'][11:23]}  {event['message']}\n")
            lines = int(self.log.index("end-1c").split(".")[0])
            if lines > 2001:
                self.log.delete("1.0", f"{lines - 2000}.0")
            self.log.see("end")
            self.log.configure(state="disabled")
            self.last_event = new_events[-1]["seq"]
        self.draw()

    def draw(self):
        c, m = self.canvas, self.model
        c.delete("all")
        w, h = max(c.winfo_width(), 900), max(c.winfo_height(), 250)
        az, el = m.target()
        radius = min(95, (h - 65) / 2)
        x, y = w * 0.22, h * 0.54
        c.create_text(x, 18, text="АЗИМУТ / ВИД СВЕРХУ", fill="#d5e4f3", font=("Arial", 11, "bold"))
        c.create_oval(x-radius, y-radius, x+radius, y+radius, outline="#47647b", width=2)
        for angle, label in ((0, "С 0°"), (90, "В 90°"), (180, "Ю 180°"), (270, "З 270°")):
            a = math.radians(angle)
            c.create_text(x + (radius+18)*math.sin(a), y - (radius+18)*math.cos(a), text=label, fill="#91a9bf")
        for angle, color, dash in ((az, "#f4bd64", (4, 3)), (m.az, "#5ed6bb", ())):
            a = math.radians(angle)
            c.create_line(x, y, x+radius*math.sin(a), y-radius*math.cos(a), fill=color, width=3, arrow="last", dash=dash)
        x, y = w * 0.60, h * 0.75
        c.create_text(w * 0.64, 18, text="АНТЕННА / УГОЛ МЕСТА", fill="#d5e4f3", font=("Arial", 11, "bold"))
        c.create_line(x-50, y+28, x+160, y+28, fill="#47647b", width=2)
        c.create_polygon(x-20, y+28, x, y, x+20, y+28, outline="#91a9bf", fill="#263f53")
        for angle, color, dash in ((el, "#f4bd64", (4, 3)), (m.el, "#5ed6bb", ())):
            a = math.radians(angle)
            dx, dy = 120*math.cos(a), -120*math.sin(a)
            c.create_line(x, y, x+dx, y+dy, fill=color, width=3, arrow="last", dash=dash)
        a = math.radians(m.el)
        # A stylized reflector perpendicular to the actual pointing direction.
        cx, cy = x+35*math.cos(a), y-35*math.sin(a)
        c.create_line(cx-30*math.sin(a), cy-30*math.cos(a), cx+30*math.sin(a), cy+30*math.cos(a), fill="#5ed6bb", width=8)
        c.create_text(w*0.87, h*0.4, text="● КАНАЛ" if m.link else "○ КАНАЛ", fill="#5ed6bb" if m.link else "#f4bd64", font=("Arial", 15, "bold"))
        c.create_text(w*0.87, h*0.55, text=f"{m.packets}\nучебных пакетов", fill="#d5e4f3", font=("Arial", 12))
        c.create_text(w/2, h-12, text="Жёлтый пунктир — расчётное направление     Зелёная стрелка — виртуальная антенна", fill="#d5e4f3")

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", filetypes=[("Отчёт JSON", "*.json")])
        if not path:
            return
        m = self.model
        report = {"simulation_only": True, "metadata": self.metadata, "state": m.state,
                  "simulation_utc": m.utc, "elapsed_seconds": m.elapsed, "azimuth_deg": m.az,
                  "elevation_deg": m.el, "error_deg": m.error, "commands_sent": m.commands_sent,
                  "commands_acked": m.commands_acked, "simulated_packets": m.packets,
                  "events_total": m.event_count, "events_omitted": m.event_count-len(m.events), "events": list(m.events)}
        try:
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror("Ошибка сохранения", str(exc), parent=self)

    def close(self):
        self.model.stop()
        if self.timer is not None:
            self.after_cancel(self.timer)
        self.destroy()

