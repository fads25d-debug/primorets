import argparse
import json
import math
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalog import Catalog, default_data_dir
from .core import OrbitFilter, Station, demo_records, export_csv, parse_file, parse_utc, trajectory


class App(tk.Tk):
    def __init__(self, data_dir):
        super().__init__()
        self.title("Приморец • Планирование сопровождения")
        self.geometry("1240x860")
        self.minsize(1040, 740)
        self.data_dir = Path(data_dir)
        self.catalog = Catalog(self.data_dir / "catalog.sqlite3")
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.results = queue.Queue()
        self.busy = False
        self.points = []
        self.export_points = []
        self.session_window = None
        self.plot_mask = 10
        self.records = {}
        self.fields = {}
        self.settings_path = self.data_dir / "settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        self.configure(bg="#eef2f6")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f6")
        style.configure("TLabel", background="#eef2f6", font=("Arial", 10))
        style.configure("TLabelframe", background="#eef2f6")
        style.configure("TLabelframe.Label", background="#eef2f6", font=("Arial", 10, "bold"))
        style.configure("TButton", font=("Arial", 10), padding=6)
        style.configure("Treeview", rowheight=29, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="ПРИМОРЕЦ / Орбитальный планировщик", font=("Arial", 20, "bold")).pack(anchor="w")
        ttk.Label(body, text="Автономный режим • Локальный каталог • Геометрические направления • Время UTC").pack(anchor="w", pady=(5, 12))
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Импорт TLE / JSON", command=self.import_file).pack(side="left")
        ttk.Button(toolbar, text="Добавить учебные орбиты", command=self.add_demo).pack(side="left", padx=8)
        self.session_button = ttk.Button(toolbar, text="Имитация сеанса", command=self.open_session, state="disabled")
        self.session_button.pack(side="left", padx=8)
        self.hide_demo = tk.BooleanVar(value=self.settings.get("hide_demo", False))
        ttk.Checkbutton(toolbar, text="Скрыть учебные", variable=self.hide_demo, command=self.refresh).pack(side="left")
        self.count_text = tk.StringVar()
        ttk.Label(toolbar, textvariable=self.count_text).pack(side="right")
        filters = ttk.LabelFrame(body, text="Отбор орбит — настраиваемые критерии", padding=8)
        filters.pack(fill="x", pady=10)
        for col, (key, label, default) in enumerate([
            ("min_e", "Эксцентриситет ≥", "0.25"), ("min_apogee", "Апогей ≥, км", "20000"),
            ("min_period", "Период от, ч", "0"), ("max_period", "до, ч", "48")]):
            self.entry(filters, key, label, default, col)
        ttk.Button(filters, text="Применить", command=self.refresh).grid(row=1, column=4, padx=10)
        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        columns = ("name", "norad", "ecc", "inc", "period", "apogee", "epoch", "type")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=7, selectmode="browse")
        for column, heading, width in zip(columns,
                ("Спутник", "ID", "e", "Наклон, °", "Период, ч", "Апогей ≈, км", "Эпоха UTC", "Данные"),
                (245, 65, 70, 85, 90, 105, 155, 100)):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=column == "name")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select_record)
        self.detail = tk.StringVar(value="Импортируйте каталог или добавьте учебные орбиты.")
        ttk.Label(body, textvariable=self.detail, wraplength=1170).pack(fill="x", pady=7)
        station = ttk.LabelFrame(body, text="Точка наблюдения — укажите координаты своей станции", padding=8)
        station.pack(fill="x")
        for col, (key, label, default) in enumerate([
            ("latitude", "Широта, ° (север +)", "0"), ("longitude", "Долгота, ° (восток +)", "0"),
            ("elevation_m", "Высота WGS84, м", "0"), ("min_altitude", "Маска горизонта, °", "10")]):
            self.entry(station, key, label, default, col)
        plan = ttk.Frame(body)
        plan.pack(fill="x", pady=10)
        self.entry(plan, "start", "Начало UTC: ГГГГ-ММ-ДД ЧЧ:ММ:СС", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), 0, 29, persist=False)
        self.entry(plan, "hours", "Интервал, ч", "24", 1)
        self.entry(plan, "step", "Шаг, с", "60", 2)
        ttk.Button(plan, text="Сейчас UTC", command=self.now).grid(row=1, column=3, padx=5)
        self.calculate_button = ttk.Button(plan, text="Рассчитать", command=self.calculate)
        self.calculate_button.grid(row=1, column=4, padx=5)
        self.export_button = ttk.Button(plan, text="Экспорт CSV", command=self.export, state="disabled")
        self.export_button.grid(row=1, column=5, padx=5)
        self.canvas = tk.Canvas(body, height=230, bg="#122234", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.summary = tk.StringVar(value="График угла места появится после расчёта. Начальные координаты 0°, 0° — заполнитель.")
        ttk.Label(body, textvariable=self.summary, wraplength=1170).pack(fill="x", pady=(8, 3))
        self.status = tk.StringVar(value=f"Каталог: {self.data_dir / 'catalog.sqlite3'}")
        ttk.Label(body, textvariable=self.status, wraplength=1170).pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.after(100, self.poll)

    def entry(self, parent, key, label, default, col, width=18, persist=True):
        value = self.settings.get(key, default) if persist else default
        self.fields[key] = tk.StringVar(value=value)
        ttk.Label(parent, text=label).grid(row=0, column=col, sticky="w", padx=5)
        ttk.Entry(parent, textvariable=self.fields[key], width=width).grid(row=1, column=col, sticky="w", padx=5)

    def number(self, key):
        return float(self.fields[key].get().strip().replace(",", "."))

    def save_settings(self):
        settings = {key: var.get() for key, var in self.fields.items() if key != "start"}
        settings["hide_demo"] = self.hide_demo.get()
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)

    def refresh(self):
        try:
            orbit_filter = OrbitFilter(*(self.number(key) for key in ("min_e", "min_apogee", "min_period", "max_period")))
            all_records = self.catalog.all()
            selected = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            self.records = {}
            for key, record in all_records:
                if (self.hide_demo.get() and record.demo) or not orbit_filter.accepts(record):
                    continue
                p = record.properties()
                self.records[key] = record
                self.tree.insert("", "end", iid=key, values=(record.name, p["norad"], f"{p['eccentricity']:.5f}",
                    f"{p['inclination']:.2f}", f"{p['period_hours']:.3f}", f"{p['apogee_km']:,.0f}",
                    p["epoch"].strftime("%Y-%m-%d %H:%M"), "УЧЕБНЫЕ" if record.demo else "Импорт"))
            self.count_text.set(f"Показано {len(self.records)} / в каталоге {len(all_records)}")
            if selected and selected[0] in self.records:
                self.tree.selection_set(selected[0])
            elif self.records:
                self.tree.selection_set(next(iter(self.records)))
            else:
                self.detail.set("Нет спутников по заданным критериям. Измените фильтр или импортируйте данные.")
            self.save_settings()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Проверьте параметры", str(exc), parent=self)

    def select_record(self, event=None):
        selection = self.tree.selection()
        if not selection or selection[0] not in self.records:
            return
        record = self.records[selection[0]]
        age = (datetime.now(timezone.utc) - record.properties()["epoch"]).total_seconds() / 86400
        label = "УЧЕБНЫЕ, ВЫМЫШЛЕННЫЕ ДАННЫЕ" if record.demo else "Импортированные орбитальные данные"
        self.detail.set(f"{label} • {record.name} • Источник: {record.source} • От эпохи до текущего времени: {age:+.1f} суток")

    def import_file(self):
        path = filedialog.askopenfilename(parent=self, title="Импорт локального каталога", filetypes=[("Орбитальные данные", "*.tle *.txt *.json"), ("Все файлы", "*")])
        if not path:
            return
        try:
            added, skipped = self.catalog.import_records(parse_file(path))
            self.refresh()
            self.status.set(f"Импортировано / обновлено: {added}. Пропущено одинаковых или более старых: {skipped}.")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            messagebox.showerror("Ошибка импорта", str(exc), parent=self)

    def add_demo(self):
        self.catalog.import_records(demo_records())
        self.hide_demo.set(False)
        self.refresh()
        self.status.set("Добавлены 3 вымышленные орбиты; стандартный фильтр показывает 2 ВЭО. Эпоха: 07.09.2026.")

    def now(self):
        self.fields["start"].set(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    def calculate(self):
        if self.busy:
            return
        try:
            selected = self.tree.selection()
            if not selected or selected[0] not in self.records:
                raise ValueError("Выберите спутник в каталоге")
            record = self.records[selected[0]]
            station = Station(*(self.number(key) for key in ("latitude", "longitude", "elevation_m", "min_altitude")))
            start = parse_utc(self.fields["start"].get())
            hours, step = self.number("hours"), self.number("step")
            self.save_settings()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Проверьте параметры", str(exc), parent=self)
            return
        self.busy = True
        self.points = []
        self.export_points = []
        self.draw()
        self.export_button.configure(state="disabled")
        self.session_button.configure(state="disabled")
        self.calculate_button.configure(state="disabled")
        self.summary.set(f"Расчёт: {record.name}…")
        def work():
            try:
                points = trajectory(record, station, start, hours, step)
                self.results.put((points, record, station, None))
            except Exception as exc:
                self.results.put((None, record, station, str(exc)))
        self.pool.submit(work)

    def poll(self):
        try:
            points, record, station, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.calculate_button.configure(state="normal")
            if error:
                self.summary.set("Расчёт не выполнен.")
                messagebox.showerror("Ошибка расчёта", error, parent=self)
            else:
                self.points = points
                self.plot_mask = station.min_altitude
                epoch = record.properties()["epoch"]
                max_age = max(abs((parse_utc(points[i]["utc"]) - epoch).total_seconds()) / 86400 for i in (0, -1))
                stale = max_age > 14
                self.export_points = [dict(satellite=record.name, demo=record.demo, elements_epoch_utc=epoch.isoformat(),
                    source=record.source, beyond_14_days=stale, station_lat_deg=station.latitude,
                    station_lon_deg=station.longitude, station_height_m=station.elevation_m,
                    horizon_mask_deg=station.min_altitude, **point) for point in points]
                peak = max(points, key=lambda row: row["elevation_deg"])
                first = points[0]
                warnings = ("УЧЕБНЫЕ ДАННЫЕ. " if record.demo else "") + ("БОЛЕЕ 14 СУТОК ОТ ЭПОХИ: обновите элементы. " if stale else "")
                self.summary.set(f"{warnings}{record.name} | Станция {station.latitude}°, {station.longitude}° | "
                    f"На начало: азимут {first['azimuth_deg']:.2f}°, угол места {first['elevation_deg']:.2f}°, "
                    f"дальность {first['range_km']:.0f} км. Максимум по сетке: {peak['elevation_deg']:.2f}°. "
                    f"Выше маски: {sum(p['visible'] for p in points)} из {len(points)} точек.")
                self.status.set(f"Результат: {points[0]['utc']} — {points[-1]['utc']}. CSV сохраняет этот расчёт и его исходные параметры.")
                self.export_button.configure(state="normal")
                self.session_button.configure(state="normal" if len(points) >= 2 else "disabled")
                self.draw()
        self.after(100, self.poll)

    def draw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 180)
        left, right, top, bottom = 55, w - 25, 38, h - 35
        c.create_text(18, 16, anchor="w", text="УГОЛ МЕСТА / UTC     •     сплошная линия: расчёт     •     пунктир: маска горизонта", fill="#d1e1ef", font=("Arial", 10))
        def y(angle):
            return bottom - (angle + 90) / 180 * (bottom - top)
        for angle in (-90, -45, 0, 45, 90):
            c.create_line(left, y(angle), right, y(angle), fill="#304355")
            c.create_text(left - 8, y(angle), anchor="e", text=f"{angle}°", fill="#a4b4c6")
        if not self.points:
            c.create_text(w / 2, h / 2, text="Выберите спутник и рассчитайте траекторию", fill="#a4b4c6", font=("Arial", 12))
            return
        c.create_line(left, y(self.plot_mask), right, y(self.plot_mask), fill="#f4bd64", dash=(5, 4))
        coordinates = []
        for i, point in enumerate(self.points):
            coordinates.extend((left + i / max(1, len(self.points) - 1) * (right - left), y(point["elevation_deg"])))
        if len(coordinates) >= 4:
            c.create_line(*coordinates, fill="#5ed6bb", width=2)
        for fraction in (0, 0.25, 0.5, 0.75, 1):
            index = round(fraction * (len(self.points) - 1))
            label = parse_utc(self.points[index]["utc"]).strftime("%d.%m %H:%M")
            c.create_text(left + fraction * (right - left), bottom + 18, text=label, fill="#a4b4c6")

    def open_session(self):
        from .session_gui import SessionWindow
        if self.session_window is not None and self.session_window.winfo_exists():
            self.session_window.lift()
            return
        if len(self.points) < 2:
            return
        self.session_window = SessionWindow(self, self.points, self.export_points[0])

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Сохранить рассчитанную траекторию", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                export_csv(path, self.export_points)
                self.status.set(f"Траектория сохранена: {path}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("Ошибка сохранения", str(exc), parent=self)

    def close(self):
        if self.session_window is not None and self.session_window.winfo_exists():
            self.session_window.close()
        try:
            self.save_settings()
        except OSError:
            pass
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.catalog.close()
        self.destroy()


def main():
    parser = argparse.ArgumentParser(description="Приморец: автономный орбитальный планировщик")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="Каталог базы и настроек")
    args = parser.parse_args()
    App(args.data_dir).mainloop()

