"""Start-screen views for the modular OLED GUI."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List

from openpyxl import load_workbook

from oled_app.constants import CONFIG_FILE, DEFAULT_ROOT, JOURNAL_FILE, MEASUREMENTS_SHEET, SCRIPT_DIR
from oled_app.series import SeriesManager
from oled_app.settings import hardware_mode_label, load_app_settings, save_app_settings
from oled_app.utils import today_iso

from .measurement_menu import build_hardware_status_bar, check_hardware_status
from .widgets import create_scrollable_frame, create_tree_with_scrollbars


def show_start_screen(app) -> None:
    app.clear()
    app.app_settings = load_app_settings()

    outer, frame = create_scrollable_frame(app, padding=22)
    outer.pack(fill="both", expand=True)
    header = ttk.Frame(frame)
    header.pack(fill="x")
    ttk.Label(header, text="OLED Measurement App", font=("Segoe UI", 22, "bold")).pack(side="left")
    ttk.Button(header, text="Настройки", command=app.open_settings_window, width=16).pack(side="right")

    mode_text = f"Режим: {hardware_mode_label(app.app_settings)}"
    if app.app_settings.get("hardware_mode") == "simulator":
        mode_text += "  |  измерения идут на эмуляторе"
    ttk.Label(frame, text=mode_text, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 2))
    build_hardware_status_bar(app, frame)
    app.after(250, lambda: check_hardware_status(app))
    ttk.Label(frame, text="Выберите существующую серию или создайте новую.", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 14))

    root_bar = ttk.Frame(frame)
    root_bar.pack(fill="x", pady=(0, 10))
    ttk.Label(root_bar, text="Корневая папка серий:").grid(row=0, column=0, sticky="w", padx=(0, 8))
    root_var = tk.StringVar(value=str(app.app_settings.get("default_root") or SCRIPT_DIR / DEFAULT_ROOT))
    root_entry = ttk.Entry(root_bar, textvariable=root_var, width=78)
    root_entry.grid(row=0, column=1, sticky="we")
    root_bar.columnconfigure(1, weight=1)
    ttk.Button(root_bar, text="Обзор", command=lambda: browse_root_and_refresh(app, root_var)).grid(row=0, column=2, padx=(8, 0))
    ttk.Button(root_bar, text="Обновить", command=lambda: refresh_series_list(app, root_var.get())).grid(row=0, column=3, padx=(8, 0))

    table, app.series_tree = create_tree_with_scrollbars(
        frame,
        columns=("deposition", "keyword", "created", "measurements", "folder"),
        height=12,
    )
    app.series_tree.heading("deposition", text="Дата напыления")
    app.series_tree.heading("keyword", text="Кодовое слово")
    app.series_tree.heading("created", text="Создана")
    app.series_tree.heading("measurements", text="Измерений")
    app.series_tree.heading("folder", text="Папка")
    app.series_tree.column("deposition", width=150, minwidth=130, stretch=False)
    app.series_tree.column("keyword", width=190, minwidth=140, stretch=False)
    app.series_tree.column("created", width=210, minwidth=170, stretch=False)
    app.series_tree.column("measurements", width=110, minwidth=90, anchor="center", stretch=False)
    app.series_tree.column("folder", width=760, minwidth=420, stretch=True)
    table.pack(fill="both", expand=True)
    app.series_tree.bind("<Double-1>", lambda _event: open_selected_series(app))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Открыть выбранную серию", command=lambda: open_selected_series(app), width=28).pack(side="left")
    ttk.Button(buttons, text="Открыть папку вручную", command=lambda: open_existing_series(app), width=24).pack(side="left", padx=(10, 0))
    ttk.Button(buttons, text="Создать новую серию", command=app.show_new_series_screen, width=24).pack(side="right")

    app.log_widget = ScrolledText(frame, height=8, state="disabled")
    app.log_widget.pack(fill="x", pady=(14, 0))
    refresh_series_list(app, root_var.get())


def browse_root_and_refresh(app, root_var: tk.StringVar) -> None:
    folder = filedialog.askdirectory(title="Корневая папка для серий")
    if not folder:
        return
    root_var.set(folder)
    app.app_settings["default_root"] = folder
    save_app_settings(app.app_settings)
    refresh_series_list(app, folder)


def find_existing_series(root_folder: Path) -> List[Dict[str, Any]]:
    root = Path(root_folder)
    if not root.exists():
        return []
    found: List[Dict[str, Any]] = []
    for cfg_path in sorted(root.rglob(CONFIG_FILE)):
        folder = cfg_path.parent
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        measurements_count: Any = ""
        journal_path = folder / JOURNAL_FILE
        if journal_path.exists():
            try:
                wb = load_workbook(journal_path, data_only=True, read_only=True)
                if MEASUREMENTS_SHEET in wb.sheetnames:
                    measurements_count = max(wb[MEASUREMENTS_SHEET].max_row - 1, 0)
                wb.close()
            except Exception:
                measurements_count = "?"
        found.append(
            {
                "folder": folder,
                "deposition_date": cfg.get("deposition_date", ""),
                "keyword": cfg.get("keyword", ""),
                "created_at": cfg.get("created_at", ""),
                "measurements_count": measurements_count,
            }
        )
    return found


def refresh_series_list(app, root_folder: str) -> None:
    if not hasattr(app, "series_tree"):
        return
    app.app_settings["default_root"] = str(root_folder)
    save_app_settings(app.app_settings)
    for item in app.series_tree.get_children():
        app.series_tree.delete(item)
    series_list = find_existing_series(Path(root_folder))
    for item in series_list:
        folder = item["folder"]
        app.series_tree.insert(
            "",
            "end",
            iid=str(folder.resolve()),
            values=(
                item.get("deposition_date", "") or "",
                item.get("keyword", "") or "",
                item.get("created_at", "") or "",
                item.get("measurements_count", "") if item.get("measurements_count", "") is not None else "",
                str(folder),
            ),
        )
    app.log(f"Найдено серий: {len(series_list)}. Режим оборудования: {hardware_mode_label(app.app_settings)}.")


def open_selected_series(app) -> None:
    if not hasattr(app, "series_tree"):
        return
    selection = app.series_tree.selection()
    if not selection:
        messagebox.showwarning("Серия", "Выберите серию в списке.")
        return
    open_series_folder(app, Path(selection[0]))


def open_existing_series(app) -> None:
    folder = filedialog.askdirectory(title="Выберите папку серии, где лежит series_config.json")
    if not folder:
        return
    open_series_folder(app, Path(folder))


def open_series_folder(app, folder: Path) -> None:
    try:
        app.series = SeriesManager(folder)
        app.show_measurement_menu()
    except Exception as exc:
        messagebox.showerror("Не удалось открыть серию", str(exc))


def show_new_series_screen(app) -> None:
    app.clear()
    outer, main = create_scrollable_frame(app, padding=22)
    outer.pack(fill="both", expand=True)
    ttk.Label(main, text="Новая серия напыления", font=("Segoe UI", 18, "bold")).pack(anchor="w")

    top = ttk.Frame(main)
    top.pack(fill="x", pady=(14, 10))
    ttk.Label(top, text="Корневая папка:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
    root_var = tk.StringVar(value=str(app.app_settings.get("default_root") or SCRIPT_DIR / DEFAULT_ROOT))
    ttk.Entry(top, textvariable=root_var, width=75).grid(row=0, column=1, sticky="we", pady=4)
    top.columnconfigure(1, weight=1)
    ttk.Button(top, text="Обзор", command=lambda: browse_root(root_var)).grid(row=0, column=2, padx=(6, 0), pady=4)

    ttk.Label(top, text="Дата напыления:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
    dep_date_var = tk.StringVar(value=today_iso())
    ttk.Entry(top, textvariable=dep_date_var, width=20).grid(row=1, column=1, sticky="w", pady=4)

    ttk.Label(top, text="Кодовое слово:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=4)
    keyword_var = tk.StringVar()
    ttk.Entry(top, textvariable=keyword_var, width=30).grid(row=2, column=1, sticky="w", pady=4)

    quarter_frame = ttk.LabelFrame(main, text="Названия четвертей")
    quarter_frame.pack(fill="x", pady=(4, 10))
    quarter_vars = {str(q): tk.StringVar(value="Q") for q in range(1, 5)}
    for q in range(1, 5):
        ttk.Label(quarter_frame, text=f"Четверть {q}:").grid(row=q - 1, column=0, sticky="e", padx=(8, 6), pady=4)
        ttk.Entry(quarter_frame, textvariable=quarter_vars[str(q)], width=16).grid(row=q - 1, column=1, sticky="w", pady=4)

    bottom = ttk.Frame(main)
    bottom.pack(fill="x", pady=(12, 0))
    ttk.Button(bottom, text="Назад", command=app.show_start_screen).pack(side="left")
    ttk.Button(
        bottom,
        text="Создать серию",
        command=lambda: create_series(app, root_var, dep_date_var, keyword_var, quarter_vars),
    ).pack(side="right")


def browse_root(root_var: tk.StringVar) -> None:
    folder = filedialog.askdirectory(title="Корневая папка для серий")
    if folder:
        root_var.set(folder)


def create_series(
    app,
    root_var: tk.StringVar,
    dep_date_var: tk.StringVar,
    keyword_var: tk.StringVar,
    quarter_vars: Dict[str, tk.StringVar],
) -> None:
    try:
        quarter_names = {str(q): quarter_vars[str(q)].get().strip() or f"Q{q}" for q in range(1, 5)}
        app.app_settings["default_root"] = root_var.get()
        save_app_settings(app.app_settings)
        app.series = SeriesManager.create_new(
            Path(root_var.get()),
            dep_date_var.get().strip() or today_iso(),
            keyword_var.get().strip(),
            quarter_names,
        )
        app.log(f"Создана серия: {app.series.series_folder}")
        app.show_measurement_menu()
    except Exception as exc:
        messagebox.showerror("Не удалось создать серию", str(exc))
