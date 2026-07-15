"""Series measurement-menu view for the modular OLED GUI."""

from __future__ import annotations

import threading
import tkinter as tk
from copy import deepcopy
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Tuple

from oled_app.hardware.probe import probe_hardware
from oled_app.series import (
    build_holder_layout,
    ivl_status_marker,
    pixel_status_color,
    quarter_code,
    quarter_description,
    short_date_for_map,
)
from oled_app.settings import hardware_mode_label, load_app_settings, save_app_settings
from oled_app.utils import as_float_or_none, read_spectrum_metrics_from_workbook, resolve_series_file

from .widgets import create_scrollable_frame, create_tree_with_scrollbars


def show_measurement_menu(app) -> None:
    assert app.series is not None
    app.clear()
    outer, main = create_scrollable_frame(app, padding=14)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(main)
    header.pack(fill="x")
    ttk.Label(header, text="Измерения OLED", font=("Segoe UI", 18, "bold")).pack(side="left")
    ttk.Button(header, text="Открыть другую серию", command=app.show_start_screen).pack(side="right")
    ttk.Button(header, text="Настройки", command=app.open_settings_window).pack(side="right", padx=(0, 10))
    ttk.Button(header, text="Камера (alpha)", command=app.open_camera_test_window).pack(side="right", padx=(0, 10))
    ttk.Button(header, text="Настройки серии", command=app.show_edit_series_screen).pack(side="right", padx=(0, 10))

    ttk.Label(main, text=f"Серия: {app.series.series_folder}").pack(anchor="w", pady=(4, 2))
    ttk.Label(main, text=f"Режим оборудования: {hardware_mode_label(app.app_settings)}", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
    build_hardware_status_bar(app, main)
    app.after(250, lambda: check_hardware_status(app))

    buttons = ttk.Frame(main)
    buttons.pack(fill="x", pady=(0, 10))
    state_after_ivl = "normal" if app.series.journal.has_any_ivl() else "disabled"
    ttk.Button(buttons, text="ВАЯХ", command=app.open_ivl_window, width=18).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Спектры", command=app.open_spectrum_window, width=18, state=state_after_ivl).grid(row=0, column=1, padx=8)
    ttk.Button(buttons, text="Стабильность", command=app.open_stability_window, width=18, state=state_after_ivl).grid(row=0, column=2, padx=8)
    ttk.Button(buttons, text="Обновить", command=app.refresh_pixel_table, width=14).grid(row=0, column=3, padx=8)
    ttk.Button(buttons, text="Журнал", command=lambda: messagebox.showinfo("Журнал", str(app.series.journal.path)), width=12).grid(row=0, column=4, padx=8)
    ttk.Button(buttons, text="Составить отчет", command=app.open_report_window, width=18, state=state_after_ivl).grid(row=0, column=5, padx=8)

    status_history = ttk.Frame(main)
    status_history.pack(fill="both", expand=True, pady=(0, 10))
    status_history.columnconfigure(0, weight=3)
    status_history.columnconfigure(1, weight=2)
    status_history.rowconfigure(0, weight=1)

    map_frame = ttk.LabelFrame(status_history, text="Подложкодержатель / карта статусов")
    map_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    create_status_holder_canvas(app, map_frame)

    ivl_history_frame = ttk.LabelFrame(status_history, text="История ВАХ по датам")
    ivl_history_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    create_ivl_history_tree(app, ivl_history_frame)

    latest_frame = ttk.LabelFrame(main, text="Последние даты и метрики")
    latest_frame.pack(fill="both", expand=True, pady=(0, 10))
    table_frame, app.tree = create_tree_with_scrollbars(
        latest_frame,
        columns=("pixel", "status", "opening", "ivl", "ivl_photo", "ivl_current", "spectrum", "spectrum_peaks", "spectrum_peak_nm", "stability"),
        height=14,
    )
    app.tree.heading("pixel", text="Пиксель")
    app.tree.heading("status", text="Статус")
    app.tree.heading("opening", text="V открытия, В")
    app.tree.heading("ivl", text="ВАЯХ")
    app.tree.heading("ivl_photo", text="Max PD, мкА")
    app.tree.heading("ivl_current", text="Max I, мА")
    app.tree.heading("spectrum", text="Спектры")
    app.tree.heading("spectrum_peaks", text="Пиков")
    app.tree.heading("spectrum_peak_nm", text="Пики, нм")
    app.tree.heading("stability", text="Стабильность")
    app.tree.column("pixel", width=150, minwidth=120, stretch=False)
    app.tree.column("status", width=140, minwidth=110, stretch=False)
    app.tree.column("opening", width=130, minwidth=110, stretch=False)
    app.tree.column("ivl", width=175, minwidth=145, stretch=False)
    app.tree.column("ivl_photo", width=105, minwidth=90, stretch=False)
    app.tree.column("ivl_current", width=95, minwidth=85, stretch=False)
    app.tree.column("spectrum", width=175, minwidth=145, stretch=False)
    app.tree.column("spectrum_peaks", width=70, minwidth=60, anchor="center", stretch=False)
    app.tree.column("spectrum_peak_nm", width=220, minwidth=150, stretch=False)
    app.tree.column("stability", width=190, minwidth=150, stretch=True)
    table_frame.pack(fill="both", expand=True)

    app.log_widget = None
    refresh_pixel_table(app)
    if state_after_ivl == "disabled":
        app.log("В журнале пока нет ВАЯХ: кнопки 'Спектры' и 'Стабильность' неактивны.")


def refresh_pixel_table(app) -> None:
    if app.series is None or not hasattr(app, "tree"):
        return
    for item in app.tree.get_children():
        app.tree.delete(item)
    rows = app.series.journal.list_pixels()
    for row in rows:
        pixel_id = row.get("Pixel ID")
        spectrum_peak_count = row.get("Last spectrum peak count", "")
        spectrum_peaks_nm = row.get("Last spectrum peaks nm", "")
        spectrum_max_intensity = row.get("Last spectrum max intensity (counts/s)", "")
        if (not spectrum_peak_count and not spectrum_peaks_nm) and row.get("Last spectrum file"):
            metrics = read_spectrum_metrics_from_workbook(resolve_series_file(app.series.series_folder, row.get("Last spectrum file")))
            spectrum_peak_count = metrics.get("peak_count", "")
            spectrum_peaks_nm = metrics.get("peaks_nm", "")
            spectrum_max_intensity = metrics.get("max_intensity", spectrum_max_intensity)
        spectrum_text = row.get("Last spectrum date", "") or ""
        if spectrum_max_intensity not in (None, ""):
            spectrum_text = f"{spectrum_text} | max {spectrum_max_intensity}" if spectrum_text else f"max {spectrum_max_intensity}"
        app.tree.insert(
            "",
            "end",
            iid=pixel_id,
            values=(
                pixel_id or "",
                row.get("Last status", "") or "",
                row.get("Opening voltage (V)", "") or "",
                row.get("Last IVL date", "") or "",
                row.get("Last IVL max photodiode (uA)", "") or "",
                row.get("Last IVL max current (mA)", "") or "",
                spectrum_text,
                spectrum_peak_count or "",
                spectrum_peaks_nm or "",
                row.get("Last stability date", "") or "",
            ),
        )
    render_status_holder_canvas(app)
    refresh_ivl_history_tree(app)


def pixel_ids(app, require_ivl: bool = False, require_opening: bool = False) -> List[str]:
    assert app.series is not None
    rows = app.series.journal.list_pixels()
    result = []
    for row in rows:
        if require_ivl and not row.get("Last IVL file"):
            continue
        if require_opening and as_float_or_none(row.get("Opening voltage (V)")) is None:
            continue
        result.append(row["Pixel ID"])
    return result


def build_hardware_status_bar(app, parent) -> None:
    bar = ttk.Frame(parent)
    bar.pack(fill="x", pady=(6, 10))
    app._hardware_status_canvas = tk.Canvas(bar, width=18, height=18, highlightthickness=0)
    app._hardware_status_canvas.pack(side="left", padx=(0, 8))

    text_frame = ttk.Frame(bar)
    text_frame.pack(side="left", fill="x", expand=True)
    app._hardware_status_title = tk.StringVar(value="Оборудование: не проверено")
    app._hardware_status_detail = tk.StringVar(value="Нажмите 'Проверить оборудование', чтобы опросить SMU и спектрометр.")
    ttk.Label(text_frame, textvariable=app._hardware_status_title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(text_frame, textvariable=app._hardware_status_detail, foreground="#555555", wraplength=900).pack(anchor="w")
    ttk.Button(bar, text="Проверить оборудование", command=lambda: check_hardware_status(app), width=24).pack(side="right", padx=(10, 0))
    set_hardware_status_indicator(app, "unknown")


def set_hardware_status_indicator(app, level: str) -> None:
    canvas = getattr(app, "_hardware_status_canvas", None)
    if canvas is None:
        return
    color = {
        "ok": "#2FA66A",
        "warning": "#D8A239",
        "error": "#C43C30",
        "checking": "#2F80ED",
        "unknown": "#A7B0B5",
    }.get(level, "#A7B0B5")
    canvas.delete("all")
    canvas.create_oval(2, 2, 16, 16, fill=color, outline=color)


def check_hardware_status(app) -> None:
    if getattr(app, "_hardware_probe_running", False):
        return
    app.app_settings = load_app_settings()
    app._hardware_probe_running = True
    set_hardware_status_indicator(app, "checking")
    if getattr(app, "_hardware_status_title", None) is not None:
        app._hardware_status_title.set("Оборудование: идет проверка")
    if getattr(app, "_hardware_status_detail", None) is not None:
        mode = hardware_mode_label(app.app_settings)
        com_text = "авто-COM" if app.app_settings.get("auto_com_port") else f"COM: {app.app_settings.get('com_port', 'COM3')}"
        app._hardware_status_detail.set(f"{mode}, {com_text}")

    settings_snapshot: Dict[str, Any] = deepcopy(app.app_settings)

    def worker() -> None:
        try:
            result = probe_hardware(settings_snapshot)
        except Exception as exc:
            result = {
                "level": "error",
                "title": "Ошибка проверки оборудования",
                "details": str(exc),
                "smu": "",
                "spectrometer": "",
            }
        app.after(0, lambda: finish_hardware_probe(app, result))

    threading.Thread(target=worker, daemon=True).start()


def finish_hardware_probe(app, result: Dict[str, Any]) -> None:
    app._hardware_probe_running = False
    level = str(result.get("level") or "unknown")
    set_hardware_status_indicator(app, level)
    auto_com = str(result.get("auto_com_port") or "")
    if auto_com:
        try:
            app.app_settings["com_port"] = auto_com
            save_app_settings(app.app_settings)
        except Exception:
            pass
    if getattr(app, "_hardware_status_title", None) is not None:
        app._hardware_status_title.set(f"Оборудование: {result.get('title', 'неизвестно')}")
    if getattr(app, "_hardware_status_detail", None) is not None:
        smu = str(result.get("smu") or "")
        spectrometer = str(result.get("spectrometer") or "")
        parts = [part for part in [smu, spectrometer] if part]
        app._hardware_status_detail.set(" | ".join(parts) if parts else "Нет деталей проверки.")
    try:
        app.log(f"Проверка оборудования: {result.get('title')}; {result.get('details')}")
    except Exception:
        pass


def create_status_holder_canvas(app, parent):
    width, height = 900, 540
    canvas = tk.Canvas(parent, width=width, height=height, background="white", highlightthickness=0)
    canvas.pack(fill="x", expand=False, padx=8, pady=8)
    app.status_canvas = canvas
    app.status_canvas_layout = build_holder_layout(width, height)
    return canvas


def render_status_holder_canvas(app) -> None:
    if app.series is None or not hasattr(app, "status_canvas"):
        return
    canvas = app.status_canvas
    width = int(canvas.cget("width"))
    height = int(canvas.cget("height"))
    draw_holder_base(canvas, width, height)
    rows = {row.get("Pixel ID"): row for row in app.series.journal.list_pixels()}
    deposition_date = short_date_for_map(str(app.series.config.get("deposition_date", "") or ""))
    layout = getattr(app, "status_canvas_layout", build_holder_layout(width, height))

    for quarter_number in [2, 1, 3, 4]:
        code = quarter_code(app.series.config, quarter_number)
        description = quarter_description(app.series.config, quarter_number)
        info = layout[quarter_number]
        number_x, number_y = info["number_xy"]
        canvas.create_text(number_x, number_y, text=str(quarter_number), font=("Segoe UI", 24, "bold"), fill="#17345F")
        if description:
            desc_x = min(max(number_x, 100), width - 100)
            desc_y = number_y - 44 if quarter_number in {1, 2} else number_y + 44
            canvas.create_text(
                desc_x,
                desc_y,
                text=description,
                font=("Segoe UI", 9, "bold"),
                fill="#17345F",
                anchor="center",
                width=180,
            )
        for substrate in info["substrates"]:
            x, y, w, h = substrate["x"], substrate["y"], substrate["w"], substrate["h"]
            substrate_id = f"{code}{quarter_number}_{substrate['substrate_number']}"
            canvas.create_text(x + w / 2, y - 10, text=deposition_date, font=("Segoe UI", 7), fill="#17345F")
            canvas.create_rectangle(x, y, x + w, y + h, fill="#FFFFFF", outline="#17345F", width=2)
            for pix in range(1, 5):
                pixel_id = f"{substrate_id}_{pix}"
                status = (rows.get(pixel_id, {}) or {}).get("Last status", "UNKNOWN")
                color = pixel_status_color(status)
                px, py, pw, ph = pixel_rect_inside_substrate(x, y, w, h, pix)
                canvas.create_rectangle(px, py, px + pw, py + ph, fill=color, outline="#808080")
                canvas.create_text(px + pw / 2, py + ph / 2, text=str(pix), font=("Segoe UI", 7))
            canvas.create_text(x + w / 2, y + h + 11, text=substrate_id, font=("Segoe UI", 8, "bold"), fill="#17345F")

    draw_status_legend(canvas, height)


def draw_holder_base(canvas: tk.Canvas, width: int, height: int, title: str = "") -> None:
    canvas.delete("all")
    if title:
        canvas.create_text(width / 2, 22, text=title, fill="#17345F", font=("Segoe UI", 10, "bold"))


def pixel_rect_inside_substrate(x: float, y: float, w: float, h: float, pixel_number: int) -> Tuple[float, float, float, float]:
    pad_x = 10
    pad_top = 13
    pad_bottom = 8
    gap = 5
    inner_w = (w - 2 * pad_x - gap) / 2
    inner_h = (h - pad_top - pad_bottom - gap) / 2
    row = 0 if pixel_number in {1, 2} else 1
    col = 0 if pixel_number in {1, 3} else 1
    px = x + pad_x + col * (inner_w + gap)
    py = y + pad_top + row * (inner_h + gap)
    return px, py, inner_w, inner_h


def draw_status_legend(canvas: tk.Canvas, height: int) -> None:
    legend_y = height - 25
    legend = [
        ("#8FD694", "рабочий"),
        ("#F2D96B", "нет контакта"),
        ("#F4A261", "требует уточнения"),
        ("#F28B82", "нераб./пробой"),
        ("#D9D9D9", "не измерен"),
    ]
    left_x = 70
    for color, label in legend:
        canvas.create_rectangle(left_x, legend_y - 7, left_x + 16, legend_y + 7, fill=color, outline="#808080")
        canvas.create_text(left_x + 22, legend_y, text=label, anchor="w", font=("Segoe UI", 8))
        left_x += 165


def create_ivl_history_tree(app, parent):
    table_frame, tree = create_tree_with_scrollbars(parent, columns=("pixel",), height=8)
    table_frame.pack(fill="both", expand=True, padx=8, pady=8)
    app.ivl_history_tree = tree
    return tree


def refresh_ivl_history_tree(app) -> None:
    if app.series is None or not hasattr(app, "ivl_history_tree"):
        return

    tree = app.ivl_history_tree
    for item in tree.get_children():
        tree.delete(item)

    pixels = [str(row.get("Pixel ID") or "") for row in app.series.journal.list_pixels()]
    measurements = [
        row for row in app.series.journal.list_measurements()
        if str(row.get("Type") or "").upper() == "IVL"
    ]
    dates = sorted({str(row.get("Measurement day") or "") for row in measurements if row.get("Measurement day")})
    date_columns = [f"d{i}" for i in range(len(dates))]
    columns = ["pixel"] + date_columns
    tree.configure(columns=columns)

    tree.heading("pixel", text="Pixel")
    tree.column("pixel", width=145, minwidth=120, stretch=False)
    for col, date_text in zip(date_columns, dates):
        tree.heading(col, text=date_text)
        tree.column(col, width=130, minwidth=105, anchor="center", stretch=False)

    latest_by_pixel_date: Dict[Tuple[str, str], str] = {}
    for row in measurements:
        pixel_id = str(row.get("Pixel ID") or "")
        day = str(row.get("Measurement day") or "")
        status = str(row.get("Status") or "")
        if pixel_id and day:
            latest_by_pixel_date[(pixel_id, day)] = status

    for pixel_id in pixels:
        values = [pixel_id]
        for day in dates:
            values.append(ivl_status_marker(latest_by_pixel_date.get((pixel_id, day), "")))
        tree.insert("", "end", iid=f"ivl_{pixel_id}", values=values)
