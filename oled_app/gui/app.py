"""Main Tk application shell for the modular OLED GUI."""

from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, Optional

from oled_app.constants import DEFAULT_ROOT, SCRIPT_DIR, SIM_CONFIG_FILE
from oled_app.series import SeriesManager
from oled_app.settings import (
    DEFAULT_APP_SETTINGS,
    deep_update,
    ensure_default_sim_config,
    load_app_settings,
    save_app_settings,
)

from .measurement_menu import pixel_ids, refresh_pixel_table, show_measurement_menu
from .settings_window import open_settings_window
from .start_screen import show_new_series_screen, show_start_screen


class OLEDModularApp(tk.Tk):
    """Early modular GUI shell.

    The reference GUI stays in oled_measurement_app_v2_5.py while this class
    grows into the new main application.
    """

    def __init__(self):
        super().__init__()
        self.title("OLED Measurement App")
        self._set_initial_window_geometry()
        self.minsize(640, 440)
        self.series: Optional[SeriesManager] = None
        self.log_widget: Optional[ScrolledText] = None
        self._hardware_probe_running = False
        self._hardware_status_canvas = None
        self._hardware_status_title = None
        self._hardware_status_detail = None
        self._ui_scale = 1.0
        self._base_tk_scaling = 1.0
        self._scale_after_id = None
        self._setup_gui_style()
        self.bind("<Configure>", self._schedule_ui_scale_update)
        self.app_settings: Dict[str, Any] = load_app_settings()
        ensure_default_sim_config(Path(self.app_settings.get("simulator_config_path") or SCRIPT_DIR / SIM_CONFIG_FILE))
        self.show_start_screen()

    def save_ui_preference(self, key: str, value: Any) -> None:
        self.app_settings.setdefault("ui", {})[key] = value
        save_app_settings(self.app_settings)

    def measurement_defaults(self, section: str) -> Dict[str, Any]:
        defaults = deepcopy(DEFAULT_APP_SETTINGS.get("measurement_defaults", {}).get(section, {}))
        saved = self.app_settings.get("measurement_defaults", {}).get(section, {})
        return deep_update(defaults, saved) if isinstance(saved, dict) else defaults

    def save_measurement_defaults(self, section: str, values: Dict[str, Any]) -> None:
        self.app_settings.setdefault("measurement_defaults", {})
        current = self.measurement_defaults(section)
        current.update(values)
        self.app_settings["measurement_defaults"][section] = current
        save_app_settings(self.app_settings)

    def _set_initial_window_geometry(self) -> None:
        try:
            screen_w = max(int(self.winfo_screenwidth()), 1120)
            screen_h = max(int(self.winfo_screenheight()), 760)
            width = int(min(max(screen_w * 0.68, 900), 1500))
            height = int(min(max(screen_h * 0.68, 620), 920))
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry("980x680")

    def _setup_gui_style(self) -> None:
        try:
            self._style = ttk.Style(self)
            self._font_base_sizes = {}
            self._base_tk_scaling = float(self.tk.call("tk", "scaling"))
            for name in ["TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkFixedFont"]:
                font = tkfont.nametofont(name)
                self._font_base_sizes[name] = abs(int(font.cget("size") or 10))
            self._ui_scale = 0.0
            self._apply_ui_scale()
        except Exception:
            pass

    def _schedule_ui_scale_update(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        try:
            if self._scale_after_id is not None:
                self.after_cancel(self._scale_after_id)
            self._scale_after_id = self.after(120, self._apply_ui_scale)
        except Exception:
            pass

    def _apply_ui_scale(self) -> None:
        try:
            width = max(int(self.winfo_width()), 1)
            height = max(int(self.winfo_height()), 1)
            scale = float(min(max(min(width / 1120.0, height / 760.0), 0.78), 1.03))
            if abs(scale - getattr(self, "_ui_scale", 1.0)) < 0.025:
                return
            self._ui_scale = scale
            self.tk.call("tk", "scaling", max(1.0, self._base_tk_scaling))
            for name, base_size in getattr(self, "_font_base_sizes", {}).items():
                tkfont.nametofont(name).configure(size=max(8, int(round(base_size * scale))))
            default_font = tkfont.nametofont("TkDefaultFont")
            rowheight = max(20, int(default_font.metrics("linespace") * 1.32))
            style = getattr(self, "_style", ttk.Style(self))
            style.configure("Treeview", rowheight=rowheight)
            style.configure("Treeview.Heading", padding=(max(2, int(3 * scale)), max(2, int(4 * scale))))
            self.option_add("*TCombobox*Listbox.font", default_font)
        except Exception:
            pass

    def clear(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def log(self, text: str) -> None:
        print(text)
        if self.log_widget is not None:
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", str(text) + "\n")
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")
            self.update_idletasks()

    def show_start_screen(self) -> None:
        show_start_screen(self)

    def show_new_series_screen(self) -> None:
        show_new_series_screen(self)

    def show_measurement_menu(self) -> None:
        show_measurement_menu(self)

    def refresh_pixel_table(self) -> None:
        refresh_pixel_table(self)

    def pixel_ids(self, require_ivl: bool = False, require_opening: bool = False):
        return pixel_ids(self, require_ivl=require_ivl, require_opening=require_opening)

    def open_ivl_window(self) -> None:
        messagebox.showinfo("ВАЯХ", "Окно ВАЯХ будет подключено следующим GUI-подэтапом.")

    def open_spectrum_window(self) -> None:
        messagebox.showinfo("Спектры", "Окно спектров будет подключено следующим GUI-подэтапом.")

    def open_stability_window(self) -> None:
        messagebox.showinfo("Стабильность", "Окно стабильности будет подключено следующим GUI-подэтапом.")

    def open_report_window(self) -> None:
        messagebox.showinfo("Составить отчет", "Окно отчета будет подключено следующим GUI-подэтапом.")

    def open_settings_window(self) -> None:
        open_settings_window(self)


def main() -> int:
    app = OLEDModularApp()
    app.mainloop()
    return 0
