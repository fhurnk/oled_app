"""GUI action for assigning one spectrum calibration to an entire quarter."""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional

from oled_app.processing.spectral_calibration import (
    QuarterIntegralCalibration,
    calibrate_quarter_spectral_integral,
    create_spectral_recalculation_workbook,
    read_spectrum_integral_points,
    spectral_recalculation_output_path,
)
from oled_app.series import ensure_scope_calibration_folder
from oled_app.series.metadata import (
    description_scope_groups,
    quarter_code,
    quarter_description,
    scope_group_for_quarter,
    series_description_scope,
    series_half_orientation,
)
from oled_app.settings import DEFAULT_APP_SETTINGS, save_app_settings
from oled_app.utils import (
    SPECTRAL_CALIBRATION_METHODS,
    as_float_or_none,
    luminance_cd_m2,
    parse_float,
    resolve_series_file,
)

from .widgets import fit_toplevel_to_content


def spectral_calibration_thresholds(settings: Dict[str, Any]) -> tuple[float, float]:
    """Read and validate the median and linear-model thresholds."""

    defaults = DEFAULT_APP_SETTINGS["spectral_calibration"]
    configured = settings.get("spectral_calibration", {}) if isinstance(settings, dict) else {}
    median_tolerance = float(
        configured.get(
            "median_tolerance_percent",
            defaults["median_tolerance_percent"],
        )
    )
    linear_outlier = float(
        configured.get(
            "linear_model_outlier_percent",
            defaults["linear_model_outlier_percent"],
        )
    )
    if not math.isfinite(median_tolerance) or not 0 < median_tolerance <= 100:
        raise ValueError("Допуск интеграла от медианы должен быть больше 0 и не больше 100%.")
    if not math.isfinite(linear_outlier) or not 0 < linear_outlier <= 100:
        raise ValueError(
            "Доля точек вне допуска для линейной модели должна быть "
            "больше 0 и не больше 100%."
        )
    return median_tolerance, linear_outlier


def quarter_spectral_candidates(
    rows: Iterable[Dict[str, Any]],
) -> Dict[int, List[str]]:
    """Return pixels with saved spectra, grouped by physical quarter."""

    grouped: Dict[int, List[str]] = {quarter: [] for quarter in range(1, 5)}
    for row in rows:
        pixel_id = str(row.get("Pixel ID") or "").strip()
        if not pixel_id or not row.get("Last spectrum file"):
            continue
        try:
            quarter_number = int(row.get("Quarter number") or 1)
        except (TypeError, ValueError):
            continue
        if quarter_number not in grouped or pixel_id in grouped[quarter_number]:
            continue
        grouped[quarter_number].append(pixel_id)
    return {
        quarter: sorted(pixel_ids)
        for quarter, pixel_ids in grouped.items()
        if pixel_ids
    }


def ask_quarter_calibration_pixels(
    app,
    candidates_by_quarter: Dict[int, List[str]],
) -> Optional[List[str]]:
    """Select one spectrum for each configured quarter/half/substrate scope."""

    dialog = tk.Toplevel(app)
    dialog.title("Спектральная калибровка областей")
    dialog.transient(app)
    dialog.grab_set()

    main = ttk.Frame(dialog, padding=14)
    main.pack(fill="both", expand=True)
    ttk.Label(
        main,
        text="Выберите области и один пиксель со спектром для каждой из них.",
        font=("Segoe UI", 10, "bold"),
    ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
    ttk.Label(
        main,
        text=(
            "Области берутся из настроек серии. Один спектр задаёт калибровку "
            "для всех четвертей выбранной половины или всей подложки."
        ),
        foreground="#555555",
        wraplength=650,
    ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

    for column, text in enumerate(("Выбрать", "Область", "Пиксель со спектром", "Доступно")):
        ttk.Label(main, text=text, font=("Segoe UI", 9, "bold")).grid(
            row=2,
            column=column,
            sticky="w",
            padx=(0, 10),
            pady=(0, 4),
        )

    selected_vars: Dict[tuple[int, ...], tk.BooleanVar] = {}
    pixel_vars: Dict[tuple[int, ...], tk.StringVar] = {}
    pixel_combos: Dict[tuple[int, ...], ttk.Combobox] = {}
    config = app.series.config if app.series is not None else {}
    groups = description_scope_groups(
        series_description_scope(config),
        series_half_orientation(config),
    )
    candidates_by_group = {
        group: sorted(
            {
                pixel
                for quarter_number in group
                for pixel in candidates_by_quarter.get(quarter_number, [])
            }
        )
        for group in groups
    }
    calibration_settings = getattr(app, "app_settings", {}).get(
        "spectral_calibration",
        DEFAULT_APP_SETTINGS["spectral_calibration"],
    )
    median_tolerance_var = tk.StringVar(
        value=str(
            calibration_settings.get(
                "median_tolerance_percent",
                DEFAULT_APP_SETTINGS["spectral_calibration"]["median_tolerance_percent"],
            )
        )
    )
    linear_outlier_var = tk.StringVar(
        value=str(
            calibration_settings.get(
                "linear_model_outlier_percent",
                DEFAULT_APP_SETTINGS["spectral_calibration"]["linear_model_outlier_percent"],
            )
        )
    )

    def update_combo_state(group: tuple[int, ...]) -> None:
        combo = pixel_combos[group]
        state = (
            "readonly"
            if candidates_by_group.get(group)
            and selected_vars[group].get()
            else "disabled"
        )
        combo.configure(state=state)

    for index, group in enumerate(groups):
        candidates = candidates_by_group.get(group, [])
        selected_vars[group] = tk.BooleanVar(value=bool(candidates))
        pixel_vars[group] = tk.StringVar(value=candidates[0] if candidates else "")
        row_number = index + 3
        check = ttk.Checkbutton(
            main,
            variable=selected_vars[group],
            command=lambda selected_group=group: update_combo_state(selected_group),
        )
        check.grid(row=row_number, column=0, sticky="w", padx=(2, 14), pady=5)
        if not candidates:
            check.configure(state="disabled")

        codes = "+".join(f"{quarter_code(config, number)}{number}" for number in group)
        description = quarter_description(config, group[0]).strip()
        if len(group) == 1:
            scope_label = f"Четверть {group[0]} — {codes}"
        elif len(group) == 2:
            scope_label = f"Половина {group[0]}+{group[1]} — {codes}"
        else:
            scope_label = f"Вся подложка — {codes}"
        if description:
            scope_label += f" · {description}"
        ttk.Label(main, text=scope_label).grid(
            row=row_number,
            column=1,
            sticky="w",
            padx=(0, 12),
            pady=5,
        )
        combo = ttk.Combobox(
            main,
            values=candidates,
            textvariable=pixel_vars[group],
            state="readonly" if candidates else "disabled",
            width=28,
        )
        combo.grid(row=row_number, column=2, sticky="ew", padx=(0, 12), pady=5)
        pixel_combos[group] = combo
        ttk.Label(
            main,
            text=f"{len(candidates)}" if candidates else "нет спектров",
            foreground="#555555",
        ).grid(row=row_number, column=3, sticky="w", pady=5)

    result: Dict[str, Optional[List[str]]] = {"pixels": None}

    def set_all(selected: bool) -> None:
        for group in groups:
            if candidates_by_group.get(group):
                selected_vars[group].set(selected)
                update_combo_state(group)

    def confirm() -> None:
        selected_pixels = [
            pixel_vars[group].get()
            for group in groups
            if selected_vars[group].get()
            and pixel_vars[group].get()
        ]
        if not selected_pixels:
            messagebox.showwarning(
                "Спектральная калибровка",
                "Выберите хотя бы одну область.",
                parent=dialog,
            )
            return
        try:
            median_tolerance = parse_float(
                median_tolerance_var.get(),
                "Допуск интеграла от медианы",
            )
            linear_outlier = parse_float(
                linear_outlier_var.get(),
                "Доля точек вне допуска для линейной модели",
            )
            spectral_calibration_thresholds(
                {
                    "spectral_calibration": {
                        "median_tolerance_percent": median_tolerance,
                        "linear_model_outlier_percent": linear_outlier,
                    }
                }
            )
        except Exception as exc:
            messagebox.showerror(
                "Параметры интегрального пересчёта",
                str(exc),
                parent=dialog,
            )
            return
        app.app_settings.setdefault("spectral_calibration", {})
        app.app_settings["spectral_calibration"].update(
            {
                "median_tolerance_percent": median_tolerance,
                "linear_model_outlier_percent": linear_outlier,
            }
        )
        save_app_settings(app.app_settings)
        result["pixels"] = selected_pixels
        dialog.destroy()

    controls_row = len(groups) + 3
    thresholds = ttk.LabelFrame(main, text="Выбор медианы или линейной модели", padding=8)
    thresholds.grid(row=controls_row, column=0, columnspan=4, sticky="ew", pady=(12, 0))
    ttk.Label(thresholds, text="Допуск интеграла от медианы, %:").grid(
        row=0,
        column=0,
        sticky="e",
        padx=(0, 8),
        pady=3,
    )
    ttk.Entry(thresholds, textvariable=median_tolerance_var, width=12).grid(
        row=0,
        column=1,
        sticky="w",
        pady=3,
    )
    ttk.Label(
        thresholds,
        text="Точек вне допуска для перехода к линейной модели, %:",
    ).grid(
        row=1,
        column=0,
        sticky="e",
        padx=(0, 8),
        pady=3,
    )
    ttk.Entry(thresholds, textvariable=linear_outlier_var, width=12).grid(
        row=1,
        column=1,
        sticky="w",
        pady=3,
    )
    ttk.Label(
        thresholds,
        text=(
            "Если доля точек вне допуска достигает второго порога, приложение "
            "проверяет систематический тренд по напряжению и при его наличии "
            "строит линейную модель."
        ),
        foreground="#555555",
        wraplength=620,
        justify="left",
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    buttons = ttk.Frame(main)
    buttons.grid(row=controls_row + 1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
    ttk.Button(buttons, text="Выбрать все доступные", command=lambda: set_all(True)).pack(
        side="left"
    )
    ttk.Button(buttons, text="Снять выбор", command=lambda: set_all(False)).pack(
        side="left",
        padx=(8, 0),
    )
    ttk.Button(buttons, text="Отмена", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="Пересчитать выбранные", command=confirm).pack(
        side="right",
        padx=(0, 8),
    )
    main.columnconfigure(2, weight=1)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    fit_toplevel_to_content(dialog, 760, 470)
    app.wait_window(dialog)
    return result["pixels"]


def calibrate_quarter_from_latest_spectrum(app) -> None:
    if app.series is None:
        return
    candidates_by_quarter = quarter_spectral_candidates(
        app.series.journal.list_pixels()
    )
    if not candidates_by_quarter:
        messagebox.showwarning(
            "Интегральный пересчёт",
            "В серии пока нет сохранённых спектров.",
            parent=app,
        )
        return
    pixel_ids = ask_quarter_calibration_pixels(app, candidates_by_quarter)
    if not pixel_ids:
        return
    median_tolerance, linear_outlier = spectral_calibration_thresholds(
        getattr(app, "app_settings", {})
    )

    completed: List[Dict[str, Any]] = []
    skipped: List[str] = []
    errors: List[str] = []
    for pixel_id in pixel_ids:
        try:
            result = _calibrate_quarter_pixel(
                app,
                pixel_id,
                median_tolerance_percent=median_tolerance,
                linear_model_outlier_percent=linear_outlier,
            )
            if result is None:
                skipped.append(pixel_id)
            else:
                completed.append(result)
        except Exception as exc:
            errors.append(f"{pixel_id}: {exc}")
            app.log(f"Ошибка спектрального пересчёта {pixel_id}: {exc}")

    summary_lines: List[str] = []
    if completed:
        summary_lines.append(f"Успешно пересчитано областей: {len(completed)}.")
        for result in completed:
            summary_lines.append(
                f"• Четверти {result.get('quarters', result['quarter'])}, {result['pixel']}: "
                f"интеграл {result['coefficient']:.9g}; "
                f"{Path(result['output']).name}"
            )
    if skipped:
        summary_lines.append("Пропущено пользователем: " + ", ".join(skipped) + ".")
    if errors:
        summary_lines.append("\nОшибки:")
        summary_lines.extend(f"• {error}" for error in errors)
    if completed:
        summary_lines.append(
            "\nФайлы сохранены в папке calibration соответствующей области. "
            "Для применения коэффициентов к ранее снятым XLSX нажмите "
            "«Пересчитать мкА → кд/м²»."
        )
    if errors:
        messagebox.showwarning(
            "Интегральный пересчёт завершён с ошибками",
            "\n".join(summary_lines),
            parent=app,
        )
    elif completed or skipped:
        messagebox.showinfo(
            "Интегральный пересчёт",
            "\n".join(summary_lines),
            parent=app,
        )


def _calibrate_quarter_pixel(
    app,
    pixel_id: str,
    *,
    median_tolerance_percent: Optional[float] = None,
    linear_model_outlier_percent: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    row = app.series.journal.get_pixel(pixel_id) or {}
    workbook_path = resolve_series_file(
        app.series.series_folder,
        row.get("Last spectrum file"),
    )
    if workbook_path is None:
        raise FileNotFoundError(
            f"не найден последний файл спектра {pixel_id}"
        )

    points = read_spectrum_integral_points(workbook_path)
    rgb_photo_coefficient = app.series.rgb_luminance_coefficient_for_pixel(
        pixel_id,
        app.app_settings,
    )
    for point in points:
        point["rgb_luminance_cd_m2"] = luminance_cd_m2(
            point.get("photodiode_uA"),
            rgb_photo_coefficient,
        )
    geometry = app.series.geometric_coefficient(app.app_settings)
    configured_integral = app.series.configured_integral_coefficient(app.app_settings)
    if median_tolerance_percent is None or linear_model_outlier_percent is None:
        configured_median, configured_linear = spectral_calibration_thresholds(
            app.app_settings
        )
        if median_tolerance_percent is None:
            median_tolerance_percent = configured_median
        if linear_model_outlier_percent is None:
            linear_model_outlier_percent = configured_linear
    quarter_number = int(row.get("Quarter number") or 1)
    target_quarters = scope_group_for_quarter(app.series.config, quarter_number)
    opening_voltage = as_float_or_none(row.get("Opening voltage (V)"))
    stored_calibration = app.series.integral_calibration_for_pixel(pixel_id)
    if (
        stored_calibration is not None
        and str(stored_calibration.get("method") or "")
        not in SPECTRAL_CALIBRATION_METHODS
    ):
        stored_calibration = None
    use_stored_calibration = False
    if stored_calibration is not None:
        source_pixel = str(stored_calibration.get("source_pixel") or "не указан")
        choice = messagebox.askyesnocancel(
            "Спектральный интеграл области",
            (
                f"Для области {'+'.join(map(str, target_quarters))} уже есть калибровка по пикселю "
                f"{source_pixel}.\n\n"
                "Да — применить сохранённый интеграл области к выбранному спектру.\n"
                "Нет — заменить калибровку области расчётом по выбранному пикселю.\n"
                "Отмена — пропустить эту область и продолжить остальные."
            ),
            parent=app,
        )
        if choice is None:
            return None
        use_stored_calibration = bool(choice)

    if use_stored_calibration:
        calibration = QuarterIntegralCalibration.from_dict(
            stored_calibration,
            integral_coefficient=configured_integral,
            geometric_coefficient=geometry,
            activation_voltage_V=opening_voltage,
        )
    else:
        calibration = calibrate_quarter_spectral_integral(
            points,
            geometry,
            source_pixel=pixel_id,
            source_file=str(workbook_path),
            integral_coefficient=configured_integral,
            activation_voltage_V=opening_voltage,
            median_tolerance_percent=median_tolerance_percent,
            linear_model_outlier_percent=linear_model_outlier_percent,
        )
    calibration_folder = ensure_scope_calibration_folder(
        app.series.series_folder,
        app.series.config,
        target_quarters,
    )
    output_path = create_spectral_recalculation_workbook(
        spectral_recalculation_output_path(
            workbook_path,
            pixel_id,
            output_dir=calibration_folder,
        ),
        workbook_path,
        pixel_id,
        quarter_number,
        points,
        calibration,
        rgb_photodiode_coefficient=rgb_photo_coefficient,
        target_quarters=target_quarters,
    )
    if not use_stored_calibration:
        app.series.save_scope_integral_calibration(
            target_quarters,
            calibration.as_dict(),
        )
    action_status = "RECALCULATED" if use_stored_calibration else "CALIBRATED"
    app.series.journal.update_after_measurement(
        "SPECTRAL_CALIBRATION",
        pixel_id,
        action_status,
        output_path,
        calibration.as_dict(),
        notes=f"Источник: {workbook_path.name}",
    )
    relative_std = (
        f"{calibration.relative_std_percent:.3f}%"
        if calibration.relative_std_percent is not None
        else "не определён"
    )
    fit_text = (
        f"; {calibration.equation}, R²={calibration.r_squared:.6f}"
        if calibration.r_squared is not None
        else f"; {calibration.equation}"
    )
    action_text = (
        f"применён к {pixel_id}"
        if use_stored_calibration
        else f"рассчитан по {pixel_id} и назначен области"
    )
    app.log(
        f"Область {'+'.join(map(str, target_quarters))}: спектральный интеграл "
        f"{calibration.coefficient:.9g}, интегральный коэффициент "
        f"{configured_integral:.9g}; произведение "
        f"{calibration.coefficient * configured_integral:.9g} "
        f"заменяет R/G/B и {action_text}; точек {calibration.points_used}, "
        f"разброс={relative_std}; допуск медианы "
        f"{calibration.inlier_threshold_percent:g}%, вне допуска "
        f"{calibration.outlier_percent:g}% при пороге линейной модели "
        f"{calibration.linear_model_outlier_threshold_percent:g}%"
        f"{fit_text}. "
        f"Результат: {output_path.name}"
    )
    return {
        "quarter": quarter_number,
        "quarters": "+".join(map(str, target_quarters)),
        "pixel": pixel_id,
        "coefficient": calibration.coefficient,
        "output": output_path,
        "used_stored_calibration": use_stored_calibration,
    }
