"""Series measurement-menu view for the modular OLED GUI."""

from __future__ import annotations

import threading
import tkinter as tk
from copy import deepcopy
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageTk

from oled_app.hardware.probe import probe_hardware
from oled_app.processing.ivl_preview import (
    create_ivl_thumbnail_from_workbook,
    ivl_thumbnail_needs_refresh,
    ivl_thumbnail_path,
)
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
    ttk.Button(header, text="Камера серии", command=app.open_series_camera_window).pack(side="right", padx=(0, 10))
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
    ttk.Button(
        buttons,
        text="Обновить",
        command=lambda: app.refresh_pixel_table(refresh_thumbnails=True),
        width=14,
    ).grid(row=0, column=3, padx=8)
    ttk.Button(buttons, text="Журнал", command=lambda: messagebox.showinfo("Журнал", str(app.series.journal.path)), width=12).grid(row=0, column=4, padx=8)
    ttk.Button(buttons, text="Составить отчет", command=app.open_report_window, width=18, state=state_after_ivl).grid(row=0, column=5, padx=8)
    ttk.Button(
        buttons,
        text="Пересчитать спектр в отдельный файл",
        command=app.calibrate_quarter_from_latest_spectrum,
        width=32,
        state=state_after_ivl,
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Button(
        buttons,
        text="Пересчитать мкА → кд/м²",
        command=app.recalculate_series_luminance,
        width=24,
        state=state_after_ivl,
    ).grid(row=1, column=2, columnspan=2, sticky="w", padx=8, pady=(8, 0))

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
    spectrum_queue_controls = ttk.Frame(latest_frame)
    spectrum_queue_controls.pack(fill="x", padx=6, pady=(6, 2))
    ttk.Label(
        spectrum_queue_controls,
        text="Очередь спектров: флажок ставится в столбце «Спектры»",
        foreground="#555555",
    ).pack(side="left")
    ttk.Button(
        spectrum_queue_controls,
        text="Отметить всю подложку",
        command=lambda: set_selected_substrate_spectrum_priority(app, True),
    ).pack(side="right", padx=(6, 0))
    ttk.Button(
        spectrum_queue_controls,
        text="Снять отметки с подложки",
        command=lambda: set_selected_substrate_spectrum_priority(app, False),
    ).pack(side="right", padx=(6, 0))
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
    app.tree.bind("<Button-1>", lambda event: handle_spectrum_queue_click(app, event), add="+")

    app.log_widget = None
    refresh_pixel_table(app)
    if state_after_ivl == "disabled":
        app.log("В журнале пока нет ВАЯХ: кнопки 'Спектры' и 'Стабильность' неактивны.")


def refresh_pixel_table(app, refresh_thumbnails: bool = False) -> None:
    if app.series is None or not hasattr(app, "tree"):
        return
    for item in app.tree.get_children():
        app.tree.delete(item)
    rows = app.series.journal.list_pixels()
    spectrum_metric_jobs = []
    spectrum_metrics_cache = getattr(app, "_spectrum_metrics_cache", {})
    app._spectrum_metrics_cache = spectrum_metrics_cache
    for row in rows:
        pixel_id = row.get("Pixel ID")
        spectrum_peak_count = row.get("Last spectrum peak count", "")
        spectrum_peaks_nm = row.get("Last spectrum peaks nm", "")
        spectrum_max_intensity = row.get("Last spectrum max intensity (counts/s)", "")
        if (not spectrum_peak_count and not spectrum_peaks_nm) and row.get("Last spectrum file"):
            workbook_path = resolve_series_file(
                app.series.series_folder,
                row.get("Last spectrum file"),
            )
            cache_key = spectrum_metrics_cache_key(workbook_path)
            cached_metrics = spectrum_metrics_cache.get(cache_key)
            if cached_metrics is None:
                spectrum_metric_jobs.append((str(pixel_id or ""), dict(row), workbook_path, cache_key))
            else:
                spectrum_peak_count = cached_metrics.get("peak_count", "")
                spectrum_peaks_nm = cached_metrics.get("peaks_nm", "")
                spectrum_max_intensity = cached_metrics.get("max_intensity", spectrum_max_intensity)
        spectrum_text = spectrum_queue_cell_text(row, spectrum_max_intensity)
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
    if spectrum_metric_jobs:
        refresh_spectrum_metrics_async(app, spectrum_metric_jobs)
    if refresh_thumbnails:
        refresh_ivl_thumbnails_async(app)


def spectrum_metrics_cache_key(workbook_path) -> tuple[str, int | None]:
    if workbook_path is None:
        return ("", None)
    try:
        modified_ns = workbook_path.stat().st_mtime_ns
    except OSError:
        modified_ns = None
    return (str(workbook_path), modified_ns)


def refresh_spectrum_metrics_async(app, jobs) -> bool:
    """Load legacy spectrum metrics without blocking the Tk event loop."""

    if app.series is None or not jobs:
        return False
    series = app.series
    series_key = str(series.series_folder.resolve())
    active = getattr(app, "_spectrum_metrics_refresh_jobs", set())
    app._spectrum_metrics_refresh_jobs = active
    if series_key in active:
        return False
    active.add(series_key)

    def worker() -> None:
        results = []
        for pixel_id, row, workbook_path, cache_key in jobs:
            metrics = read_spectrum_metrics_from_workbook(workbook_path)
            results.append((pixel_id, row, cache_key, metrics))

        def finish() -> None:
            active.discard(series_key)
            cache = getattr(app, "_spectrum_metrics_cache", {})
            for _pixel_id, _row, cache_key, metrics in results:
                cache[cache_key] = metrics
            app._spectrum_metrics_cache = cache
            if app.series is not series or not hasattr(app, "tree"):
                return
            for pixel_id, row, _cache_key, metrics in results:
                if not pixel_id or not metrics or not app.tree.exists(pixel_id):
                    continue
                values = list(app.tree.item(pixel_id, "values"))
                if len(values) < 10:
                    continue
                values[6] = spectrum_queue_cell_text(row, metrics.get("max_intensity", ""))
                values[7] = metrics.get("peak_count", "") or ""
                values[8] = metrics.get("peaks_nm", "") or ""
                app.tree.item(pixel_id, values=values)

        try:
            app.after(0, finish)
        except (RuntimeError, tk.TclError):
            pass

    threading.Thread(
        target=worker,
        name="spectrum-metrics-refresh",
        daemon=True,
    ).start()
    return True


def refresh_ivl_thumbnails(app) -> int:
    """Create or replace missing, legacy, and out-of-date IVL thumbnails."""

    if app.series is None:
        return 0
    refreshed, errors = _refresh_ivl_thumbnails(app.series)
    for error in errors:
        app.log(error)
    if refreshed:
        app.log(f"Обновлены миниатюры ВАЯХ: {refreshed}.")
    return refreshed


def _refresh_ivl_thumbnails(series, pixel_id: str | None = None) -> tuple[int, List[str]]:
    refreshed = 0
    errors = []
    for row in series.journal.list_pixels():
        row_pixel_id = str(row.get("Pixel ID") or "")
        if pixel_id is not None and row_pixel_id != pixel_id:
            continue
        workbook_path = resolve_series_file(
            series.series_folder,
            row.get("Last IVL file"),
        )
        if not row_pixel_id or workbook_path is None:
            continue
        preview_path = ivl_thumbnail_path(workbook_path, row_pixel_id)
        try:
            if ivl_thumbnail_needs_refresh(preview_path, workbook_path):
                create_ivl_thumbnail_from_workbook(workbook_path, preview_path)
                refreshed += 1
        except Exception as exc:
            errors.append(f"Не удалось обновить миниатюру ВАЯХ {row_pixel_id}: {exc}")
    return refreshed, errors


def refresh_ivl_thumbnails_async(app, pixel_id: str | None = None) -> bool:
    """Refresh thumbnails in a worker so OneDrive hydration cannot freeze Tk."""

    if app.series is None:
        return False
    series = app.series
    series_key = str(series.series_folder.resolve())
    active = getattr(app, "_ivl_thumbnail_refresh_jobs", set())
    app._ivl_thumbnail_refresh_jobs = active
    if series_key in active:
        return False
    active.add(series_key)
    if pixel_id is None:
        app.log("Обновление миниатюр ВАЯХ запущено в фоне.")

    def worker() -> None:
        refreshed, errors = _refresh_ivl_thumbnails(series, pixel_id)

        def finish() -> None:
            active.discard(series_key)
            if app.series is not series:
                return
            for error in errors:
                app.log(error)
            if refreshed:
                if pixel_id is None:
                    app.log(f"Обновлены миниатюры ВАЯХ: {refreshed}.")
                else:
                    app.log(f"Миниатюра ВАЯХ {pixel_id} готова.")

        try:
            app.after(0, finish)
        except (RuntimeError, tk.TclError):
            pass

    threading.Thread(
        target=worker,
        name="ivl-thumbnail-refresh",
        daemon=True,
    ).start()
    return True


def pixel_ids(app, require_ivl: bool = False, require_opening: bool = False) -> List[str]:
    assert app.series is not None
    rows = app.series.journal.list_pixels()
    result: List[Tuple[int, int, str]] = []
    for index, row in enumerate(rows):
        if require_ivl and not row.get("Last IVL file"):
            continue
        if require_opening and as_float_or_none(row.get("Opening voltage (V)")) is None:
            continue
        priority = bool(row.get("Spectrum priority"))
        result.append((0 if priority else 1, index, row["Pixel ID"]))
    return [pixel_id for _priority, _index, pixel_id in sorted(result)]


def spectrum_queue_cell_text(row: Dict[str, Any], spectrum_max_intensity: Any = "") -> str:
    """Show the queue checkbox exactly where the spectrum date will appear."""

    if not row.get("Last spectrum file"):
        return "☑ в очереди" if bool(row.get("Spectrum priority")) else "☐ поставить"
    spectrum_text = str(row.get("Last spectrum date", "") or "")
    if spectrum_max_intensity not in (None, ""):
        return (
            f"{spectrum_text} | max {spectrum_max_intensity}"
            if spectrum_text
            else f"max {spectrum_max_intensity}"
        )
    return spectrum_text


def handle_spectrum_queue_click(app, event):
    """Toggle the queue only when an unmeasured spectrum cell is clicked."""

    tree = getattr(app, "tree", None)
    if tree is None or tree.identify_region(event.x, event.y) != "cell":
        return None
    column_id = tree.identify_column(event.x)
    try:
        column_index = int(column_id.lstrip("#")) - 1
        column_name = list(tree["columns"])[column_index]
    except (TypeError, ValueError, IndexError):
        return None
    if column_name != "spectrum":
        return None
    pixel_id = tree.identify_row(event.y)
    if not pixel_id:
        return None
    row = app.series.journal.get_pixel(pixel_id) if app.series is not None else None
    if not row or row.get("Last spectrum file"):
        return "break"
    toggle_spectrum_priority(app, pixel_id)
    try:
        tree.selection_set(pixel_id)
        tree.focus(pixel_id)
    except tk.TclError:
        pass
    return "break"


def substrate_pixel_ids(rows: List[Dict[str, Any]], pixel_id: str) -> List[str]:
    substrate_id = str(pixel_id).rsplit("_", 1)[0]
    return [
        str(row.get("Pixel ID"))
        for row in rows
        if row.get("Pixel ID")
        and str(row.get("Pixel ID")).rsplit("_", 1)[0] == substrate_id
        and not row.get("Last spectrum file")
    ]


def set_selected_substrate_spectrum_priority(app, enabled: bool) -> None:
    if app.series is None or not hasattr(app, "tree"):
        return
    selection = app.tree.selection()
    if not selection:
        messagebox.showinfo(
            "Очередь спектров",
            "Сначала выберите пиксель нужной подложки в таблице.",
            parent=app,
        )
        return
    selected_pixel = str(selection[0])
    eligible_pixels = substrate_pixel_ids(app.series.journal.list_pixels(), selected_pixel)
    if not eligible_pixels:
        messagebox.showinfo(
            "Очередь спектров",
            "На выбранной подложке нет пикселей без снятых спектров.",
            parent=app,
        )
        return
    changed = app.series.journal.set_spectrum_priorities(eligible_pixels, enabled)
    substrate_id = selected_pixel.rsplit("_", 1)[0]
    action = "добавлена в очередь" if enabled else "убрана из очереди"
    app.log(
        f"Подложка {substrate_id} {action}: изменено отметок {changed}, "
        f"доступно пикселей {len(eligible_pixels)}."
    )
    refresh_pixel_table(app)
    if app.tree.exists(selected_pixel):
        app.tree.selection_set(selected_pixel)
        app.tree.focus(selected_pixel)


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
    quarter_layout = app.series.config.get("quarter_layout") if app.series is not None else None
    app.status_canvas_layout = build_holder_layout(width, height, quarter_layout)
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
    layout = getattr(
        app,
        "status_canvas_layout",
        build_holder_layout(width, height, app.series.config.get("quarter_layout")),
    )

    for quarter_number, info in layout.items():
        code = quarter_code(app.series.config, quarter_number)
        description = quarter_description(app.series.config, quarter_number)
        number_x, number_y = info["number_xy"]
        canvas.create_text(number_x, number_y, text=str(quarter_number), font=("Segoe UI", 24, "bold"), fill="#17345F")
        if description:
            desc_x = min(max(number_x, 100), width - 100)
            desc_y = number_y - 44 if number_y < height / 2 else number_y + 44
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
                pixel_row = rows.get(pixel_id, {}) or {}
                status = pixel_row.get("Last status", "UNKNOWN")
                color = pixel_status_color(status)
                px, py, pw, ph = pixel_rect_inside_substrate(x, y, w, h, pix)
                pixel_tag = f"pixel::{pixel_id}"
                common_tags = ("pixel", pixel_tag)
                canvas.create_rectangle(
                    px,
                    py,
                    px + pw,
                    py + ph,
                    fill=color,
                    outline="#808080",
                    tags=common_tags,
                )
                canvas.create_text(
                    px + pw / 2,
                    py + ph / 2,
                    text=str(pix),
                    font=("Segoe UI", 7),
                    tags=common_tags,
                )
                canvas.tag_bind(
                    pixel_tag,
                    "<Enter>",
                    lambda event, selected_pixel=pixel_id: show_ivl_hover_preview(
                        app,
                        selected_pixel,
                        event,
                    ),
                )
                canvas.tag_bind(pixel_tag, "<Leave>", lambda _event: hide_ivl_hover_preview(app))
            canvas.create_text(x + w / 2, y + h + 11, text=substrate_id, font=("Segoe UI", 8, "bold"), fill="#17345F")

    draw_status_legend(canvas, height)


def toggle_spectrum_priority(app, pixel_id: str) -> None:
    if app.series is None:
        return
    row = app.series.journal.get_pixel(pixel_id) or {}
    if row.get("Last spectrum file"):
        return
    enabled = not bool(row.get("Spectrum priority"))
    app.series.journal.set_spectrum_priority(pixel_id, enabled)
    app.log(
        f"{pixel_id}: {'добавлен в приоритетную очередь спектров' if enabled else 'убран из приоритетной очереди спектров'}."
    )
    refresh_pixel_table(app)


def hide_ivl_hover_preview(app) -> None:
    window = getattr(app, "_ivl_hover_window", None)
    if window is not None:
        try:
            window.destroy()
        except Exception:
            pass
    app._ivl_hover_window = None
    app._ivl_hover_photo = None


def show_ivl_hover_preview(app, pixel_id: str, event) -> None:
    hide_ivl_hover_preview(app)
    if app.series is None:
        return
    row = app.series.journal.get_pixel(pixel_id) or {}
    workbook_path = resolve_series_file(app.series.series_folder, row.get("Last IVL file"))
    if workbook_path is None:
        return
    preview_path = ivl_thumbnail_path(workbook_path)
    try:
        if ivl_thumbnail_needs_refresh(preview_path, workbook_path):
            refresh_ivl_thumbnails_async(app, pixel_id)
            if not preview_path.exists():
                show_ivl_hover_message(
                    app,
                    pixel_id,
                    "Миниатюра готовится в фоне. Наведите повторно через некоторое время.",
                    event,
                )
                return
        with Image.open(preview_path) as source:
            image = source.copy()
        photo = ImageTk.PhotoImage(image)
    except Exception as exc:
        app.log(f"Не удалось показать миниатюру ВАЯХ {pixel_id}: {exc}")
        return

    window = tk.Toplevel(app)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    ttk.Label(window, text=pixel_id, font=("Segoe UI", 9, "bold")).pack(
        anchor="w",
        padx=6,
        pady=(4, 0),
    )
    label = ttk.Label(window, image=photo)
    label.pack(padx=4, pady=4)
    position_ivl_hover_window(window, event)
    app._ivl_hover_window = window
    app._ivl_hover_photo = photo


def show_ivl_hover_message(app, pixel_id: str, message: str, event) -> None:
    window = tk.Toplevel(app)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    ttk.Label(window, text=pixel_id, font=("Segoe UI", 9, "bold")).pack(
        anchor="w",
        padx=8,
        pady=(6, 2),
    )
    ttk.Label(window, text=message, wraplength=280, justify="left").pack(
        padx=8,
        pady=(0, 7),
    )
    position_ivl_hover_window(window, event)
    app._ivl_hover_window = window
    app._ivl_hover_photo = None


def position_ivl_hover_window(window, event) -> None:
    window.update_idletasks()
    x = min(
        int(event.x_root) + 14,
        max(0, window.winfo_screenwidth() - window.winfo_reqwidth() - 8),
    )
    y = min(
        int(event.y_root) + 14,
        max(0, window.winfo_screenheight() - window.winfo_reqheight() - 8),
    )
    window.geometry(f"+{x}+{y}")


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
    col = 0 if pixel_number in {1, 4} else 1
    px = x + pad_x + col * (inner_w + gap)
    py = y + pad_top + row * (inner_h + gap)
    return px, py, inner_w, inner_h


def draw_status_legend(canvas: tk.Canvas, height: int) -> None:
    legend_y = height - 25
    legend = [
        (70, "#8FD694", "рабочий"),
        (200, "#F2D96B", "нет контакта"),
        (340, "#F4A261", "требует уточнения"),
        (535, "#F28B82", "нераб./пробой"),
        (700, "#D9D9D9", "не измерен"),
    ]
    for left_x, color, label in legend:
        canvas.create_rectangle(left_x, legend_y - 7, left_x + 16, legend_y + 7, fill=color, outline="#808080")
        canvas.create_text(left_x + 22, legend_y, text=label, anchor="w", font=("Segoe UI", 8))


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
