"""Main Tk application shell for the modular OLED GUI."""

from __future__ import annotations

import tkinter as tk
import time
from copy import deepcopy
from pathlib import Path
from tkinter import ttk
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

from .ivl_window import (
    ask_pixel,
    measure_one_ivl,
    measure_series_ivl,
    open_ivl_window,
    pixel_info_from_journal,
    remove_same_substrate_from_queue,
)
from .camera_window import open_camera_test_window
from .measurement_menu import pixel_ids, refresh_pixel_table, show_measurement_menu
from .report_window import open_report_window
from .recalculation_window import start_series_luminance_recalculation
from .settings_window import open_settings_window
from .spectrum_window import open_spectrum_window
from .spectral_calibration_window import calibrate_quarter_from_latest_spectrum
from .stability_window import open_stability_window
from .start_screen import show_edit_series_screen, show_new_series_screen, show_start_screen
from .widgets import enable_windows_dpi_awareness, fit_window_to_screen, window_dpi_scale


class OLEDModularApp(tk.Tk):
    """Early modular GUI shell.

    The reference GUI stays in oled_measurement_app_v2_5.py while this class
    grows into the new main application.
    """

    def __init__(self):
        enable_windows_dpi_awareness()
        super().__init__()
        self.title("OLED Measurement App")
        self._set_initial_window_geometry()
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
        self._closing = False
        self._active_measurement_session: Optional[Dict[str, Any]] = None
        self._measurement_session_history = []
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self.show_start_screen()

    def _close_app(self) -> None:
        if self._closing:
            return
        camera_window = getattr(self, "_camera_test_window", None)
        try:
            camera_exists = camera_window is not None and bool(camera_window.winfo_exists())
        except tk.TclError:
            camera_exists = False
        if camera_exists and not camera_window.shutdown_for_app_close():
            return
        self._closing = True
        self.withdraw()
        self.after(350 if camera_exists else 0, self.destroy)

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

    def begin_measurement_session(self, measurement_type: str, pixel_id: str) -> Dict[str, Any]:
        session = {
            "measurement_type": str(measurement_type).upper(),
            "pixel_id": str(pixel_id),
            "started_monotonic": time.monotonic(),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_monotonic": None,
            "ended_at": None,
        }
        self._active_measurement_session = session
        return session

    def end_measurement_session(self, session: Optional[Dict[str, Any]]) -> None:
        if not session or session.get("ended_monotonic") is not None:
            return
        session["ended_monotonic"] = time.monotonic()
        session["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._measurement_session_history.append(session)
        self._measurement_session_history = self._measurement_session_history[-100:]
        if self._active_measurement_session is session:
            self._active_measurement_session = None

    def measurement_session_for_interval(
        self,
        measurement_type: str,
        pixel_id: str,
        interval_start: float,
        interval_end: float,
    ) -> Optional[Dict[str, Any]]:
        candidates = list(self._measurement_session_history)
        if self._active_measurement_session is not None:
            candidates.append(self._active_measurement_session)
        matching = []
        for session in candidates:
            if session.get("measurement_type") != str(measurement_type).upper():
                continue
            if session.get("pixel_id") != str(pixel_id):
                continue
            session_start = float(session["started_monotonic"])
            session_end = float(session.get("ended_monotonic") or interval_end)
            overlap = min(interval_end, session_end) - max(interval_start, session_start)
            contains_instant = interval_start == interval_end and session_start <= interval_start <= session_end
            if overlap >= 0 or contains_instant:
                matching.append((max(overlap, 0.0), session_start, session))
        if not matching:
            return None
        matching.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return matching[0][2]

    def _set_initial_window_geometry(self) -> None:
        try:
            scale = window_dpi_scale(self)
            logical_screen_w = int(self.winfo_screenwidth() / scale)
            logical_screen_h = int(self.winfo_screenheight() / scale)
            preferred_w = min(max(int(logical_screen_w * 0.82), 960), 1600)
            preferred_h = min(max(int(logical_screen_h * 0.78), 640), 980)
            fit_window_to_screen(self, preferred_w, preferred_h, 640, 440)
        except Exception:
            self.geometry("1180x760")

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
            dpi_scale = window_dpi_scale(self)
            width = max(int(self.winfo_width() / dpi_scale), 1)
            height = max(int(self.winfo_height() / dpi_scale), 1)
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
        self.log_widget = None
        for child in self.winfo_children():
            child.destroy()

    def log(self, text: str) -> None:
        print(text)
        widget = self.log_widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                self.log_widget = None
                return
            widget.configure(state="normal")
            widget.insert("end", str(text) + "\n")
            widget.see("end")
            widget.configure(state="disabled")
            self.update_idletasks()
        except tk.TclError:
            self.log_widget = None

    def show_start_screen(self) -> None:
        show_start_screen(self)

    def show_new_series_screen(self) -> None:
        show_new_series_screen(self)

    def show_edit_series_screen(self) -> None:
        show_edit_series_screen(self)

    def show_measurement_menu(self) -> None:
        show_measurement_menu(self)

    def refresh_pixel_table(self, refresh_thumbnails: bool = False) -> None:
        refresh_pixel_table(self, refresh_thumbnails=refresh_thumbnails)

    def pixel_ids(self, require_ivl: bool = False, require_opening: bool = False):
        return pixel_ids(self, require_ivl=require_ivl, require_opening=require_opening)

    def open_ivl_window(self) -> None:
        open_ivl_window(self)

    def measure_one_ivl(self, pixel_id: str, params, return_to_menu: bool = True):
        return measure_one_ivl(self, pixel_id, params, return_to_menu=return_to_menu)

    def measure_series_ivl(self, params, start_pixel=None, skip_nonworking=False) -> None:
        measure_series_ivl(
            self,
            params,
            start_pixel=start_pixel,
            skip_nonworking=skip_nonworking,
        )

    def pixel_info_from_journal(self, pixel_id: str):
        return pixel_info_from_journal(self, pixel_id)

    def remove_same_substrate_from_queue(self, remaining, pixel_id: str):
        return remove_same_substrate_from_queue(self, remaining, pixel_id)

    def ask_pixel(self, title: str, values):
        return ask_pixel(self, title, values)

    def open_spectrum_window(self) -> None:
        open_spectrum_window(self)

    def calibrate_quarter_from_latest_spectrum(self) -> None:
        calibrate_quarter_from_latest_spectrum(self)

    def recalculate_series_luminance(self) -> None:
        start_series_luminance_recalculation(self)

    def open_stability_window(self) -> None:
        open_stability_window(self)

    def open_report_window(self) -> None:
        open_report_window(self)

    def open_camera_test_window(self) -> None:
        open_camera_test_window(self, context="free")

    def open_series_camera_window(self) -> None:
        open_camera_test_window(self, context="series")

    def open_settings_window(self) -> None:
        open_settings_window(self)


def main() -> int:
    app = OLEDModularApp()
    app.mainloop()
    return 0
