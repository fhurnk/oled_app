"""Settings window for the modular OLED GUI."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict

from oled_app.constants import (
    DEFAULT_ROOT,
    HARDWARE_MODE_REAL,
    HARDWARE_MODE_SIM,
    RAW_DATA_FOLDER,
    RAW_DATA_POLICY_DELETE_AFTER_XLSX,
    RAW_DATA_POLICY_KEEP_SEPARATE,
    SCRIPT_DIR,
    SIM_CONFIG_FILE,
)
from oled_app.settings import DEFAULT_APP_SETTINGS, ensure_default_sim_config, load_app_settings, save_app_settings
from oled_app.utils import parse_float, parse_int

from .widgets import create_scrollable_frame, fit_toplevel_to_content


def open_settings_window(app) -> None:
    app.app_settings = load_app_settings()
    win = tk.Toplevel(app)
    win.title("Настройки приложения")
    win.geometry("720x640")
    win.transient(app)

    main = ttk.Frame(win, padding=12)
    main.pack(fill="both", expand=True)
    notebook = ttk.Notebook(main)
    notebook.pack(fill="both", expand=True)

    general = scrollable_notebook_tab(notebook, "Общие")
    sim_tab = scrollable_notebook_tab(notebook, "Эмулятор")
    camera_tab = scrollable_notebook_tab(notebook, "Камера")
    ivl_tab = scrollable_notebook_tab(notebook, "ВАЯХ доп.")
    spec_tab = scrollable_notebook_tab(notebook, "Спектры доп.")
    stab_tab = scrollable_notebook_tab(notebook, "Стабильность доп.")

    root_var = tk.StringVar(value=str(app.app_settings.get("default_root", "")))
    mode_var = tk.StringVar(value=str(app.app_settings.get("hardware_mode", HARDWARE_MODE_SIM)))
    com_var = tk.StringVar(value=str(app.app_settings.get("com_port", "COM3")))
    auto_com_var = tk.BooleanVar(value=bool(app.app_settings.get("auto_com_port", False)))
    units = app.app_settings.get("measurement_units", DEFAULT_APP_SETTINGS["measurement_units"])
    pixel_area_var = tk.StringVar(value=str(units.get("pixel_area_mm2", 1.0)))
    luminance_red_var = tk.StringVar(value=str(units.get("luminance_red_cd_m2_per_uA", units.get("luminance_cd_m2_per_uA", 1.0))))
    luminance_green_var = tk.StringVar(value=str(units.get("luminance_green_cd_m2_per_uA", units.get("luminance_cd_m2_per_uA", 1.0))))
    luminance_blue_var = tk.StringVar(value=str(units.get("luminance_blue_cd_m2_per_uA", units.get("luminance_cd_m2_per_uA", 1.0))))
    luminance_white_var = tk.StringVar(value=str(units.get("luminance_white_cd_m2_per_uA", units.get("luminance_cd_m2_per_uA", 1.0))))
    geometric_coefficient_var = tk.StringVar(value=str(units.get("geometric_conversion_coefficient", 1.0)))
    integral_coefficient_var = tk.StringVar(value=str(units.get("integral_conversion_coefficient", 1.0)))
    raw_settings = app.app_settings.get("raw_data", DEFAULT_APP_SETTINGS["raw_data"])
    raw_policy_labels = {
        RAW_DATA_POLICY_KEEP_SEPARATE: f"Сохранять в папке {RAW_DATA_FOLDER}",
        RAW_DATA_POLICY_DELETE_AFTER_XLSX: "Удалять после сборки XLSX",
    }
    raw_policy_values = {label: policy for policy, label in raw_policy_labels.items()}
    raw_policy_var = tk.StringVar(
        value=raw_policy_labels.get(
            str(raw_settings.get("policy", RAW_DATA_POLICY_KEEP_SEPARATE)),
            raw_policy_labels[RAW_DATA_POLICY_KEEP_SEPARATE],
        )
    )

    ttk.Label(general, text="Корневая папка серий:").grid(row=0, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Entry(general, textvariable=root_var, width=62).grid(row=0, column=1, sticky="we", pady=4)
    ttk.Button(general, text="Обзор", command=lambda: browse_root(root_var)).grid(row=0, column=2, padx=(8, 0))
    ttk.Label(general, text="Режим оборудования:").grid(row=1, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Combobox(
        general,
        textvariable=mode_var,
        values=[HARDWARE_MODE_SIM, HARDWARE_MODE_REAL],
        state="readonly",
        width=18,
    ).grid(row=1, column=1, sticky="w", pady=4)
    add_settings_entry(general, 2, "COM port по умолчанию", com_var)
    ttk.Checkbutton(general, text="Автонастройка COM порта Ossila", variable=auto_com_var).grid(row=3, column=1, sticky="w", pady=3)
    add_settings_entry(general, 4, "Площадь пикселя, мм^2", pixel_area_var)
    add_settings_entry(general, 5, "Коэфф. красный R", luminance_red_var)
    add_settings_entry(general, 6, "Коэфф. зеленый G", luminance_green_var)
    add_settings_entry(general, 7, "Коэфф. синий B", luminance_blue_var)
    add_settings_entry(general, 8, "Коэфф. белый W", luminance_white_var)
    add_settings_entry(general, 9, "Геометрический коэффициент", geometric_coefficient_var)
    add_settings_entry(general, 10, "Интегральный коэффициент", integral_coefficient_var)
    ttk.Label(general, text="Сырые CSV после обработки:").grid(row=11, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Combobox(
        general,
        textvariable=raw_policy_var,
        values=list(raw_policy_values.keys()),
        state="readonly",
        width=30,
    ).grid(row=11, column=1, sticky="w", pady=4)
    ttk.Label(
        general,
        text="simulator = встроенная эмуляция пикселя; real = настоящие xtralien/seabreeze из Python-среды.",
        foreground="#555555",
        wraplength=610,
        justify="left",
    ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(12, 0))
    ttk.Label(
        general,
        text=(
            "R/G/B/W умножаются на геометрический коэффициент. "
            "После калибровки произведение интеграла четверти и интегрального "
            "коэффициента заменяет R/G/B; ток фотодетектора и геометрический "
            "коэффициент остаются в формуле."
        ),
        foreground="#555555",
        wraplength=610,
        justify="left",
    ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(6, 0))
    general.columnconfigure(1, weight=1)

    sim_cfg_var = tk.StringVar(value=str(app.app_settings.get("simulator_config_path") or SCRIPT_DIR / SIM_CONFIG_FILE))
    ttk.Label(sim_tab, text="JSON-конфиг пикселей:").grid(row=0, column=0, sticky="e", pady=4, padx=(0, 8))
    ttk.Entry(sim_tab, textvariable=sim_cfg_var, width=62).grid(row=0, column=1, sticky="we", pady=4)
    ttk.Button(sim_tab, text="Обзор", command=lambda: browse_file_for_var(sim_cfg_var)).grid(row=0, column=2, padx=(8, 0))
    ttk.Button(sim_tab, text="Создать/обновить пример JSON", command=lambda: write_default_sim_config_from_settings(sim_cfg_var)).grid(row=1, column=1, sticky="w", pady=(8, 4))
    ttk.Label(
        sim_tab,
        text="В этом JSON задаются режимы пикселя: working, weak, nonworking, no_contact, burned/short; напряжение открытия, ток, фототок, спектральные пики, деградация.",
        foreground="#555555",
        wraplength=620,
        justify="left",
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
    sim_tab.columnconfigure(1, weight=1)

    camera_settings = app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
    camera_host_var = tk.StringVar(value=str(camera_settings.get("host", "192.168.4.1")))
    camera_port_var = tk.StringVar(value=str(camera_settings.get("port", 8765)))
    camera_timeout_var = tk.StringVar(value=str(camera_settings.get("request_timeout_s", 8.0)))
    camera_stream_timeout_var = tk.StringVar(value=str(camera_settings.get("stream_timeout_s", 12.0)))
    camera_auto_wifi_var = tk.BooleanVar(value=bool(camera_settings.get("auto_connect_wifi", False)))
    camera_wifi_profile_var = tk.StringVar(value=str(camera_settings.get("wifi_profile", "")))
    camera_wifi_interface_var = tk.StringVar(value=str(camera_settings.get("wifi_interface", "")))
    camera_wifi_timeout_var = tk.StringVar(value=str(camera_settings.get("wifi_connect_timeout_s", 25.0)))
    camera_restore_wifi_var = tk.BooleanVar(value=bool(camera_settings.get("restore_previous_wifi", True)))
    camera_download_var = tk.StringVar(value=str(camera_settings.get("download_dir", SCRIPT_DIR / "camera_downloads")))
    camera_keep_remote_var = tk.BooleanVar(value=bool(camera_settings.get("keep_remote_files_after_download", True)))
    add_settings_entry(camera_tab, 0, "IP-адрес или имя Raspberry Pi", camera_host_var, width=32)
    add_settings_entry(camera_tab, 1, "Порт сервиса", camera_port_var)
    add_settings_entry(camera_tab, 2, "Тайм-аут запросов, с", camera_timeout_var)
    add_settings_entry(camera_tab, 3, "Тайм-аут кадра LiveView, с", camera_stream_timeout_var)
    wifi_box = ttk.LabelFrame(camera_tab, text="Автоподключение к Wi-Fi Raspberry Pi", padding=10)
    wifi_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 6))
    ttk.Checkbutton(
        wifi_box,
        text="Подключаться автоматически при открытии окна камеры",
        variable=camera_auto_wifi_var,
    ).grid(row=0, column=0, columnspan=3, sticky="w")
    add_settings_entry(wifi_box, 1, "Профиль Windows", camera_wifi_profile_var, width=32)
    add_settings_entry(
        wifi_box,
        2,
        "Wi-Fi-адаптер (пусто = автоматически)",
        camera_wifi_interface_var,
        width=32,
    )
    add_settings_entry(wifi_box, 3, "Тайм-аут подключения, с", camera_wifi_timeout_var)
    ttk.Checkbutton(
        wifi_box,
        text="Возвращать прежнюю Wi-Fi-сеть после закрытия камеры",
        variable=camera_restore_wifi_var,
    ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Label(
        wifi_box,
        text=(
            "Сначала один раз подключитесь к сети Raspberry Pi средствами Windows. "
            "Приложение хранит только имя сохранённого профиля, но не пароль."
        ),
        foreground="#555555",
        wraplength=590,
        justify="left",
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
    wifi_box.columnconfigure(1, weight=1)
    ttk.Label(camera_tab, text="Папка скачивания:").grid(row=5, column=0, sticky="e", pady=3, padx=(0, 8))
    ttk.Entry(camera_tab, textvariable=camera_download_var, width=52).grid(row=5, column=1, sticky="we", pady=3)
    ttk.Button(camera_tab, text="Обзор", command=lambda: browse_root(camera_download_var)).grid(row=5, column=2, padx=(8, 0))
    ttk.Checkbutton(
        camera_tab,
        text="Оставлять фото и видео на Raspberry Pi после успешного скачивания",
        variable=camera_keep_remote_var,
    ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Label(
        camera_tab,
        text=(
            "Альфа-модуль камеры работает отдельно от измерений. На Raspberry Pi должен быть запущен "
            "сервис из папки raspberry_camera_service."
        ),
        foreground="#555555",
        wraplength=620,
        justify="left",
    ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
    camera_tab.columnconfigure(1, weight=1)

    def make_vars(section: str) -> Dict[str, tk.StringVar]:
        return {key: tk.StringVar(value=str(value)) for key, value in app.app_settings.get(section, {}).items() if not isinstance(value, bool)}

    ivl_vars = make_vars("ivl_advanced")
    ivl_bool_vars: Dict[str, tk.BooleanVar] = {}
    ivl_labels = [
        ("photodiode_bias_V", "Смещение фотодиода, В"),
        ("photodiode_range", "Диапазон фотодиода"),
        ("photodiode_threshold_uA", "Порог рабочего фототока, мкА"),
        ("working_confirmation_points", "Следующих точек для статуса WORKING"),
        ("opening_photodiode_threshold_uA", "Порог открытия по фототоку, мкА"),
        ("opening_confirmation_points", "Следующих точек для подтверждения открытия"),
        ("burnout_current_threshold_mA", "Ток пробоя/сгорания, мА"),
        ("no_contact_max_led_current_mA", "Макс. ток при отсутствии контакта, мА"),
        ("burned_confirmation_cycles", "Доп. циклов после BURNED"),
    ]
    for row, (key, label) in enumerate(ivl_labels):
        add_settings_entry(ivl_tab, row, label, ivl_vars[key])
    ttk.Label(
        ivl_tab,
        text=(
            "Статус WORKING и точка открытия требуют устойчивого превышения своих "
            "порогов: заданное число следующих точек также не должно опускаться ниже порога."
        ),
        foreground="#555555",
        wraplength=620,
        justify="left",
    ).grid(row=len(ivl_labels), column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Label(ivl_tab, text="BURNED ставится только при достижении тока пробоя/сгорания.", foreground="#555555").grid(row=len(ivl_labels) + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Label(ivl_tab, text="Эти параметры убраны из основного окна ВАЯХ, чтобы оно не было перегружено.", foreground="#555555").grid(row=len(ivl_labels) + 2, column=0, columnspan=2, sticky="w", pady=(12, 0))

    spec_vars = make_vars("spectrum_advanced")
    integration_time_keys = ("t_int_initial_s", "t_int_min_s", "t_int_max_s")
    for key in integration_time_keys:
        spec_vars[key].set(f"{float(spec_vars[key].get()) * 1000:g}")
    spec_bool_vars = {
        "reuse_previous_integration_time": tk.BooleanVar(value=bool(app.app_settings.get("spectrum_advanced", {}).get("reuse_previous_integration_time", True))),
        "discard_first_scan_after_tint_change": tk.BooleanVar(value=bool(app.app_settings.get("spectrum_advanced", {}).get("discard_first_scan_after_tint_change", True))),
        "dark_spectrum_enabled": tk.BooleanVar(value=bool(app.app_settings.get("spectrum_advanced", {}).get("dark_spectrum_enabled", False))),
        "baseline_correction_enabled": tk.BooleanVar(value=bool(app.app_settings.get("spectrum_advanced", {}).get("baseline_correction_enabled", True))),
        "peak_detection_enabled": tk.BooleanVar(value=bool(app.app_settings.get("spectrum_advanced", {}).get("peak_detection_enabled", False))),
    }
    spec_labels = [
        ("photodiode_bias_V", "Смещение фотодиода, В"),
        ("photodiode_range", "Диапазон фотодиода"),
        ("target_intensity", "Целевая интенсивность, counts"),
        ("intensity_min", "Мин. интенсивность, counts"),
        ("intensity_max", "Макс. интенсивность, counts"),
        ("saturation_level", "Насыщение, counts"),
        ("min_peak_width_nm", "Мин. FWHM, нм"),
        ("t_int_initial_s", "Начальное T_int, мс"),
        ("t_int_min_s", "Мин. T_int, мс"),
        ("t_int_max_s", "Макс. T_int, мс"),
        ("kp", "Kp подбора T_int"),
        ("ki", "Ki подбора T_int"),
        ("max_iterations", "Макс. итераций"),
        ("tolerance", "Допуск подбора"),
        ("peak_search_mode_for_tint", "Область поиска пика"),
        ("settle_time_voltage_s", "Пауза после напряжения, с"),
        ("settle_time_spectrum_s", "Пауза спектрометра, с"),
        ("dark_spectrum_scans", "Число dark-сканов"),
    ]
    for row, (key, label) in enumerate(spec_labels):
        add_settings_entry(spec_tab, row, label, spec_vars[key])
    ttk.Checkbutton(spec_tab, text="Начинать следующую точку с T_int предыдущей", variable=spec_bool_vars["reuse_previous_integration_time"]).grid(row=len(spec_labels), column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Checkbutton(spec_tab, text="Сбрасывать первый спектр после смены T_int", variable=spec_bool_vars["discard_first_scan_after_tint_change"]).grid(row=len(spec_labels) + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ttk.Checkbutton(spec_tab, text="Снимать dark spectrum", variable=spec_bool_vars["dark_spectrum_enabled"]).grid(row=len(spec_labels) + 2, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ttk.Checkbutton(spec_tab, text="Вычитать средний фон из raw-спектра", variable=spec_bool_vars["baseline_correction_enabled"]).grid(row=len(spec_labels) + 3, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ttk.Checkbutton(spec_tab, text="Искать пики производными", variable=spec_bool_vars["peak_detection_enabled"]).grid(row=len(spec_labels) + 4, column=0, columnspan=2, sticky="w", pady=(4, 0))

    stab_vars = make_vars("stability_advanced")
    stab_labels = [
        ("voltage_step_max", "Макс. шаг напряжения, В"),
        ("current_control_kp", "Kp удержания тока, В/мА"),
        ("photodiode_bias_V", "Смещение фотодиода, В"),
        ("photodiode_threshold_uA", "Порог фототока, мкА"),
        ("photodiode_range", "Диапазон фотодиода"),
    ]
    for row, (key, label) in enumerate(stab_labels):
        add_settings_entry(stab_tab, row, label, stab_vars[key])

    def save() -> None:
        try:
            settings = load_app_settings()
            settings["default_root"] = root_var.get().strip() or str(SCRIPT_DIR / DEFAULT_ROOT)
            settings["hardware_mode"] = mode_var.get().strip() or HARDWARE_MODE_REAL
            settings["com_port"] = com_var.get().strip() or "COM3"
            settings["auto_com_port"] = bool(auto_com_var.get())
            geometric_coefficient = parse_float(
                geometric_coefficient_var.get(),
                "Геометрический коэффициент",
            )
            integral_coefficient = parse_float(
                integral_coefficient_var.get(),
                "Интегральный коэффициент",
            )
            if geometric_coefficient <= 0:
                raise ValueError("Геометрический коэффициент должен быть больше нуля.")
            if integral_coefficient <= 0:
                raise ValueError("Интегральный коэффициент должен быть больше нуля.")
            settings["measurement_units"] = {
                "pixel_area_mm2": parse_float(pixel_area_var.get(), "Площадь пикселя"),
                "luminance_red_cd_m2_per_uA": parse_float(luminance_red_var.get(), "Коэффициент яркости R"),
                "luminance_green_cd_m2_per_uA": parse_float(luminance_green_var.get(), "Коэффициент яркости G"),
                "luminance_blue_cd_m2_per_uA": parse_float(luminance_blue_var.get(), "Коэффициент яркости B"),
                "luminance_white_cd_m2_per_uA": parse_float(luminance_white_var.get(), "Коэффициент яркости W"),
                "geometric_conversion_coefficient": geometric_coefficient,
                "integral_conversion_coefficient": integral_coefficient,
            }
            settings["raw_data"] = {
                "policy": raw_policy_values.get(raw_policy_var.get(), RAW_DATA_POLICY_KEEP_SEPARATE),
                "folder_name": RAW_DATA_FOLDER,
            }
            settings["simulator_config_path"] = sim_cfg_var.get().strip() or str(SCRIPT_DIR / SIM_CONFIG_FILE)
            camera_port = parse_int(camera_port_var.get(), "Порт сервиса камеры")
            if not 1 <= camera_port <= 65535:
                raise ValueError("Порт сервиса камеры должен быть от 1 до 65535.")
            wifi_timeout = parse_float(
                camera_wifi_timeout_var.get(),
                "Тайм-аут подключения Wi-Fi",
            )
            if not 3 <= wifi_timeout <= 120:
                raise ValueError("Тайм-аут подключения Wi-Fi должен быть от 3 до 120 с.")
            wifi_profile = camera_wifi_profile_var.get().strip()
            if camera_auto_wifi_var.get() and not wifi_profile:
                raise ValueError(
                    "Для автоматического подключения задайте имя сохранённого "
                    "Wi-Fi-профиля Raspberry Pi."
                )
            updated_camera = dict(camera_settings)
            updated_camera.update(
                {
                    "host": camera_host_var.get().strip() or "192.168.4.1",
                    "port": camera_port,
                    "request_timeout_s": parse_float(camera_timeout_var.get(), "Тайм-аут запросов камеры"),
                    "stream_timeout_s": parse_float(camera_stream_timeout_var.get(), "Тайм-аут LiveView"),
                    "auto_connect_wifi": bool(camera_auto_wifi_var.get()),
                    "wifi_profile": wifi_profile,
                    "wifi_interface": camera_wifi_interface_var.get().strip(),
                    "wifi_connect_timeout_s": wifi_timeout,
                    "restore_previous_wifi": bool(camera_restore_wifi_var.get()),
                    "download_dir": camera_download_var.get().strip() or str(SCRIPT_DIR / "camera_downloads"),
                    "keep_remote_files_after_download": bool(camera_keep_remote_var.get()),
                    "video_camera_settings": dict(camera_settings.get("video_camera_settings") or {}),
                    "photo_quality_settings": dict(camera_settings.get("photo_quality_settings") or {}),
                }
            )
            settings["camera"] = updated_camera
            ivl_settings = collect_section("ivl_advanced", ivl_vars, ivl_bool_vars)
            if ivl_settings["photodiode_threshold_uA"] < 0:
                raise ValueError("Порог рабочего фототока не может быть отрицательным.")
            if not 1 <= ivl_settings["working_confirmation_points"] <= 100:
                raise ValueError(
                    "Количество следующих точек для статуса WORKING "
                    "должно быть от 1 до 100."
                )
            if ivl_settings["opening_photodiode_threshold_uA"] < 0:
                raise ValueError("Порог открытия по фототоку не может быть отрицательным.")
            if not 1 <= ivl_settings["opening_confirmation_points"] <= 100:
                raise ValueError(
                    "Количество следующих точек для подтверждения открытия "
                    "должно быть от 1 до 100."
                )
            settings["ivl_advanced"] = ivl_settings
            spectrum_settings = collect_section("spectrum_advanced", spec_vars, spec_bool_vars)
            for key in integration_time_keys:
                spectrum_settings[key] = float(spectrum_settings[key]) / 1000.0
            if not 0 < spectrum_settings["t_int_min_s"] <= spectrum_settings["t_int_initial_s"] <= spectrum_settings["t_int_max_s"]:
                raise ValueError("Времена интегрирования должны удовлетворять: 0 < минимум <= начальное <= максимум.")
            settings["spectrum_advanced"] = spectrum_settings
            settings["stability_advanced"] = collect_section("stability_advanced", stab_vars, {})
            save_app_settings(settings)
            app.app_settings = settings
            if settings["hardware_mode"] == HARDWARE_MODE_SIM:
                ensure_default_sim_config(Path(settings["simulator_config_path"]))
            messagebox.showinfo("Настройки", "Настройки сохранены.", parent=win)
            win.destroy()
        except Exception as exc:
            messagebox.showerror("Ошибка настроек", str(exc), parent=win)

    bottom = ttk.Frame(main)
    bottom.pack(fill="x", pady=(12, 0))
    ttk.Button(bottom, text="Отмена", command=win.destroy).pack(side="left")
    ttk.Button(bottom, text="Сохранить", command=save).pack(side="right")
    fit_toplevel_to_content(win, 860, 760)


def scrollable_notebook_tab(notebook, title: str, padding: int = 12) -> ttk.Frame:
    outer, frame = create_scrollable_frame(notebook, padding=padding)
    notebook.add(outer, text=title)
    return frame


def add_settings_entry(parent, row: int, label: str, var: tk.StringVar, width: int = 18) -> None:
    ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="e", pady=3, padx=(0, 8))
    ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="w", pady=3)


def browse_root(var: tk.StringVar) -> None:
    folder = filedialog.askdirectory(title="Корневая папка для серий")
    if folder:
        var.set(folder)


def browse_file_for_var(var: tk.StringVar) -> None:
    filename = filedialog.askopenfilename(title="Выберите JSON-конфиг", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
    if filename:
        var.set(filename)


def write_default_sim_config_from_settings(var: tk.StringVar) -> None:
    try:
        path = ensure_default_sim_config(Path(var.get().strip() or SCRIPT_DIR / SIM_CONFIG_FILE))
        var.set(str(path))
        messagebox.showinfo("Эмулятор", f"Пример конфига создан/найден:\n{path}")
    except Exception as exc:
        messagebox.showerror("Эмулятор", str(exc))


def cast_like(default_value, raw: str):
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return parse_int(raw, "настройка")
    if isinstance(default_value, float):
        return parse_float(raw, "настройка")
    return str(raw)


def collect_section(section: str, vars_dict: Dict[str, tk.StringVar], bool_vars: Dict[str, tk.BooleanVar]) -> Dict[str, Any]:
    defaults = DEFAULT_APP_SETTINGS[section]
    result: Dict[str, Any] = {}
    for key, var in vars_dict.items():
        result[key] = cast_like(defaults.get(key, ""), var.get())
    for key, var in bool_vars.items():
        result[key] = bool(var.get())
    return result
