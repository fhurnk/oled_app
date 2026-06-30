"""Progress windows for modular measurement workflows."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from oled_app.measurements.ivl import MeasurementStopped
from oled_app.utils import as_float_or_none

from .widgets import fit_toplevel_to_content


class IVLProgressWindow:
    def __init__(self, parent: tk.Misc, pixel_id: str, params: Any):
        self.closed = False
        self.stop_requested = False
        self.pixel_id = pixel_id
        self.params = params
        self.current_cycle = 1
        self.points: List[Tuple[float, float, float, float, float]] = []
        self.win = tk.Toplevel(parent)
        self.win.title(f"ВАЯХ: {pixel_id}")
        self.win.geometry("980x700")
        self.win.minsize(680, 460)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        main = ttk.Frame(self.win, padding=10)
        main.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value=f"Пиксель {pixel_id}: ожидание старта")
        ttk.Label(main, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Остановить измерение и поставить 0 В", command=self.request_stop).pack(side="left")
        ui_settings = getattr(parent, "app_settings", {}).get("ui", {}) if hasattr(parent, "app_settings") else {}
        self.graph_mode = tk.StringVar(value=str(ui_settings.get("last_ivl_graph_mode", "raw") or "raw"))
        ttk.Radiobutton(controls, text="I / ФД", variable=self.graph_mode, value="raw", command=self._on_graph_mode_changed).pack(side="left", padx=(14, 0))
        ttk.Radiobutton(controls, text="J / L", variable=self.graph_mode, value="converted", command=self._on_graph_mode_changed).pack(side="left", padx=(8, 0))
        ttk.Label(
            controls,
            text=f"S={params.pixel_area_mm2:g} мм^2; k={params.luminance_cd_m2_per_uA:g} кд/м^2/мкА",
            foreground="#555555",
        ).pack(side="left", padx=(12, 0))

        self.canvas = tk.Canvas(main, width=860, height=280, bg="white", highlightthickness=1, highlightbackground="#BFBFBF")
        self.canvas.pack(fill="x", pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _event: self._redraw_plot())

        table_wrap = ttk.Frame(main)
        table_wrap.pack(fill="both", expand=True)
        columns = ("cycle", "point", "vset", "vled", "iled", "jled", "ipd", "lum")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings", height=12)
        headers = {
            "cycle": "Цикл",
            "point": "Точка",
            "vset": "V set, В",
            "vled": "V LED, В",
            "iled": "I LED, мА",
            "jled": "J, мА/см^2",
            "ipd": "I ФД, мкА",
            "lum": "L, кд/м^2",
        }
        widths = {"cycle": 60, "point": 70, "vset": 100, "vled": 100, "iled": 100, "jled": 115, "ipd": 105, "lum": 115}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], minwidth=widths[col], stretch=(col == "ipd"))
        yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)
        fit_toplevel_to_content(self.win, 980, 700)
        self._redraw_plot()
        self._safe_update()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        except Exception:
            pass

    def request_stop(self) -> None:
        if self.closed:
            return
        self.stop_requested = True
        self.status_var.set("Остановка запрошена: на следующей точке будет отправлено 0 В")
        self._safe_update()

    def set_status(self, text: str) -> None:
        if self.closed:
            return
        self.status_var.set(text)
        self._safe_update()

    def add_point(self, cycle_number: int, row: Dict[str, Any]) -> None:
        if self.closed:
            return
        if self.stop_requested:
            raise MeasurementStopped("Измерение остановлено пользователем")
        if cycle_number != self.current_cycle:
            self.current_cycle = cycle_number
            self.points = []
            for item in self.tree.get_children():
                self.tree.delete(item)
        v = float(row.get("Voltage OLED / LED measured (V)", row.get("Voltage set (V)", 0.0)) or 0.0)
        i = float(row.get("Current OLED / LED (mA)", 0.0) or 0.0)
        pd = float(row.get("Photodiode current (uA)", 0.0) or 0.0)
        j = as_float_or_none(row.get("Current density (mA/cm^2)"))
        lum = as_float_or_none(row.get("Luminance (cd/m^2)"))
        j = float(j) if j is not None else 0.0
        lum = float(lum) if lum is not None else 0.0
        self.points.append((v, i, pd, j, lum))
        self.tree.insert(
            "",
            "end",
            values=(
                cycle_number,
                row.get("Point", ""),
                f"{float(row.get('Voltage set (V)', 0.0)):.4f}",
                f"{v:.4f}",
                f"{i:.4f}",
                f"{j:.4f}",
                f"{pd:.4f}",
                f"{lum:.4f}",
            ),
        )
        children = self.tree.get_children()
        if children:
            self.tree.see(children[-1])
        self.status_var.set(
            f"Пиксель {self.pixel_id} | цикл {cycle_number} | точка {row.get('Point', '')} | "
            f"V={v:.3f} В | I={i:.3f} мА | J={j:.3f} мА/см^2 | L={lum:.3f} кд/м^2"
        )
        self._redraw_plot()
        self._safe_update()

    def _on_graph_mode_changed(self) -> None:
        parent = self.win.master
        try:
            if hasattr(parent, "save_ui_preference"):
                parent.save_ui_preference("last_ivl_graph_mode", self.graph_mode.get())
        except Exception:
            pass
        self._redraw_plot()

    def _redraw_plot(self) -> None:
        if self.closed:
            return
        canvas = self.canvas
        canvas.delete("all")
        width = int(canvas.winfo_width() or 860)
        height = int(canvas.winfo_height() or 280)
        left, top, right, bottom = 68, 18, width - 72, height - 38
        canvas.create_rectangle(left, top, right, bottom, outline="#A0A0A0")
        converted = self.graph_mode.get() == "converted"
        left_axis = "J OLED\nмА/см^2" if converted else "I OLED\nмА"
        right_axis = "L\nкд/м^2" if converted else "I PD\nмкА"
        blue_label = "J OLED" if converted else "I OLED"
        red_label = "L" if converted else "I PD"
        title_suffix = "плотность тока и светимость" if converted else "ток OLED и фототок"
        canvas.create_text(width - 28, (top + bottom) / 2, text=right_axis, font=("Segoe UI", 9), justify="center", fill="#C43C30")
        canvas.create_line(right - 158, top + 14, right - 132, top + 14, fill="#0B61A4", width=2)
        canvas.create_text(right - 126, top + 14, text=blue_label, anchor="w", font=("Segoe UI", 8), fill="#0B61A4")
        canvas.create_line(right - 78, top + 14, right - 52, top + 14, fill="#C43C30", width=2)
        canvas.create_text(right - 46, top + 14, text=red_label, anchor="w", font=("Segoe UI", 8), fill="#C43C30")
        canvas.create_text((left + right) / 2, 8, text=f"ВАХ / ВАЯХ - {title_suffix}, {self.pixel_id}", font=("Segoe UI", 10, "bold"))
        canvas.create_text((left + right) / 2, height - 12, text="Напряжение, В", font=("Segoe UI", 9))
        canvas.create_text(18, (top + bottom) / 2, text=left_axis, font=("Segoe UI", 9), justify="center", fill="#0B61A4")

        if not self.points:
            canvas.create_text((left + right) / 2, (top + bottom) / 2, text="Данные появятся во время измерения", fill="#666666")
            return

        max_x = max(max(v for v, *_rest in self.points), 0.1)
        if converted:
            left_values = [j for _v, _i, _pd, j, _lum in self.points]
            right_values = [lum for _v, _i, _pd, _j, lum in self.points]
        else:
            left_values = [i for _v, i, _pd, _j, _lum in self.points]
            right_values = [pd for _v, _i, pd, _j, _lum in self.points]
        max_left = max(max(abs(v) for v in left_values), 0.1) * 1.08
        max_right = max(max(abs(v) for v in right_values), 0.1) * 1.08
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            y = bottom - frac * (bottom - top)
            val_left = frac * max_left
            val_right = frac * max_right
            canvas.create_line(left, y, right, y, fill="#EEEEEE")
            canvas.create_text(left - 8, y, text=f"{val_left:.2f}", anchor="e", font=("Segoe UI", 8), fill="#0B61A4")
            canvas.create_text(right + 8, y, text=f"{val_right:.2f}", anchor="w", font=("Segoe UI", 8), fill="#C43C30")
        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            x = left + frac * (right - left)
            val = frac * max_x
            canvas.create_line(x, top, x, bottom, fill="#F4F4F4")
            canvas.create_text(x, bottom + 14, text=f"{val:.2f}", anchor="n", font=("Segoe UI", 8))

        led_coords: List[float] = []
        pd_coords: List[float] = []
        for v, i, pd, j, lum in self.points:
            y_left_value = j if converted else i
            y_right_value = lum if converted else abs(pd)
            x = left + (v / max_x) * (right - left)
            y_led = bottom - (y_left_value / max_left) * (bottom - top)
            y_pd = bottom - (y_right_value / max_right) * (bottom - top)
            led_coords.extend([x, y_led])
            pd_coords.extend([x, y_pd])
            canvas.create_oval(x - 2.5, y_led - 2.5, x + 2.5, y_led + 2.5, fill="#0B61A4", outline="#0B61A4")
            canvas.create_rectangle(x - 2.5, y_pd - 2.5, x + 2.5, y_pd + 2.5, fill="#C43C30", outline="#C43C30")
        if len(led_coords) >= 4:
            canvas.create_line(*led_coords, fill="#0B61A4", width=2)
        if len(pd_coords) >= 4:
            canvas.create_line(*pd_coords, fill="#C43C30", width=2)

    def _safe_update(self) -> None:
        if self.closed:
            return
        try:
            self.win.update_idletasks()
            self.win.update()
        except Exception:
            self.closed = True


class SpectrumProgressWindow:
    def __init__(self, parent: tk.Misc, pixel_id: str):
        self.closed = False
        self.pixel_id = pixel_id
        self.last: Optional[Dict[str, Any]] = None
        self.win = tk.Toplevel(parent)
        self.win.title(f"Спектр: {pixel_id}")
        self.win.geometry("980x700")
        self.win.minsize(680, 460)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        main = ttk.Frame(self.win, padding=10)
        main.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value=f"Пиксель {pixel_id}: ожидание спектра")
        ttk.Label(main, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self.canvas = tk.Canvas(main, width=820, height=360, bg="white", highlightthickness=1, highlightbackground="#BFBFBF")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        fit_toplevel_to_content(self.win, 980, 700)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        except Exception:
            pass

    def update_spectrum(
        self,
        point: int,
        voltage: float,
        t_int: float,
        wavelengths: np.ndarray,
        raw: np.ndarray,
        normalized: np.ndarray,
        peaks: List[Dict[str, float]],
        status: str,
    ) -> None:
        if self.closed:
            return
        wavelengths_arr = np.asarray(wavelengths, dtype=np.float64)
        normalized_arr = np.asarray(normalized, dtype=np.float64)
        if normalized_arr.size and np.any(np.isfinite(normalized_arr)):
            max_idx = int(np.nanargmax(normalized_arr))
            max_wavelength = float(wavelengths_arr[max_idx])
            max_intensity = float(normalized_arr[max_idx])
        else:
            max_wavelength = 0.0
            max_intensity = 0.0
        self.last = {
            "point": point,
            "voltage": voltage,
            "t_int": t_int,
            "wavelengths": wavelengths_arr,
            "raw": np.asarray(raw, dtype=np.float64),
            "normalized": normalized_arr,
            "peaks": peaks,
            "status": status,
            "max_wavelength": max_wavelength,
            "max_intensity": max_intensity,
        }
        peak_text = ", ".join(f"{peak['wavelength_nm']:.1f}" for peak in peaks[:5]) or "нет"
        self.status_var.set(
            f"Точка {point}, V={voltage:.3f} В, T_int={t_int*1000:.2f} мс, "
            f"max={max_wavelength:.1f} нм / {max_intensity:.0f}, пики: {peak_text}, {status}"
        )
        self._redraw()
        try:
            self.win.update_idletasks()
            self.win.update()
        except Exception:
            self.closed = True

    def _redraw(self) -> None:
        if self.closed:
            return
        canvas = self.canvas
        canvas.delete("all")
        width = int(canvas.winfo_width() or 820)
        height = int(canvas.winfo_height() or 360)
        left, top, right, bottom = 62, 24, width - 28, height - 38
        canvas.create_rectangle(left, top, right, bottom, outline="#A0A0A0")
        canvas.create_text((left + right) / 2, 10, text="Live spectrum: raw and corrected counts/s", font=("Segoe UI", 10, "bold"))
        canvas.create_text((left + right) / 2, height - 12, text="Wavelength, nm", font=("Segoe UI", 9))
        canvas.create_text(20, (top + bottom) / 2, text="a.u.", font=("Segoe UI", 9), justify="center")
        canvas.create_line(right - 210, top + 14, right - 184, top + 14, fill="#999999", width=2)
        canvas.create_text(right - 178, top + 14, text="raw", anchor="w", font=("Segoe UI", 8), fill="#666666")
        canvas.create_line(right - 145, top + 14, right - 119, top + 14, fill="#0B61A4", width=2)
        canvas.create_text(right - 113, top + 14, text="corrected", anchor="w", font=("Segoe UI", 8), fill="#0B61A4")

        if not self.last:
            canvas.create_text((left + right) / 2, (top + bottom) / 2, text="Спектр появится во время съемки", fill="#666666")
            return

        wavelengths = self.last["wavelengths"]
        raw = self.last["raw"]
        normalized = self.last["normalized"]
        if wavelengths.size < 2:
            return
        x_min, x_max = float(np.nanmin(wavelengths)), float(np.nanmax(wavelengths))
        raw_scaled = raw / max(float(np.nanmax(raw)), 1e-9)
        norm_max = max(float(np.nanmax(normalized)), 1e-9)
        norm_scaled = normalized / norm_max

        def to_xy(x_value, y_value):
            x = left + ((float(x_value) - x_min) / max(x_max - x_min, 1e-9)) * (right - left)
            y = bottom - float(np.clip(y_value, 0.0, 1.0)) * (bottom - top)
            return x, y

        for frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            y = bottom - frac * (bottom - top)
            canvas.create_line(left, y, right, y, fill="#EEEEEE")
            x = left + frac * (right - left)
            canvas.create_line(x, top, x, bottom, fill="#F4F4F4")
            canvas.create_text(x, bottom + 14, text=f"{x_min + frac*(x_max-x_min):.0f}", anchor="n", font=("Segoe UI", 8))

        raw_coords: List[float] = []
        norm_coords: List[float] = []
        step = max(1, int(wavelengths.size / 700))
        for wavelength, raw_value, norm_value in zip(wavelengths[::step], raw_scaled[::step], norm_scaled[::step]):
            raw_coords.extend(to_xy(wavelength, raw_value))
            norm_coords.extend(to_xy(wavelength, norm_value))
        if len(raw_coords) >= 4:
            canvas.create_line(*raw_coords, fill="#999999", width=1)
        if len(norm_coords) >= 4:
            canvas.create_line(*norm_coords, fill="#0B61A4", width=2)
        max_wavelength = float(self.last.get("max_wavelength") or 0.0)
        max_intensity = float(self.last.get("max_intensity") or 0.0)
        if max_wavelength > 0:
            x, y = to_xy(max_wavelength, max_intensity / norm_max)
            canvas.create_line(x, top, x, bottom, fill="#C43C30", dash=(4, 3))
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#C43C30", outline="#C43C30")
            canvas.create_text(x + 8, max(top + 30, y - 10), text=f"max {max_wavelength:.1f} нм", anchor="w", fill="#C43C30", font=("Segoe UI", 8, "bold"))

        for peak in self.last["peaks"][:8]:
            x, y = to_xy(peak["wavelength_nm"], peak["intensity"] / norm_max)
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#C43C30", outline="#C43C30")
            canvas.create_text(x, y - 10, text=f"{peak['wavelength_nm']:.0f}", fill="#C43C30", font=("Segoe UI", 8))
