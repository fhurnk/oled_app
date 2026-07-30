"""GUI action for assigning one spectrum calibration to an entire quarter."""

from __future__ import annotations

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
from oled_app.series.metadata import quarter_code, quarter_description
from oled_app.utils import (
    SPECTRAL_CALIBRATION_METHODS,
    as_float_or_none,
    luminance_cd_m2,
    resolve_series_file,
)

from .widgets import fit_toplevel_to_content


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
    """Select any available quarters and one spectrum pixel for each."""

    dialog = tk.Toplevel(app)
    dialog.title("Спектральная калибровка четвертей")
    dialog.transient(app)
    dialog.grab_set()

    main = ttk.Frame(dialog, padding=14)
    main.pack(fill="both", expand=True)
    ttk.Label(
        main,
        text="Выберите четверти и пиксель-калибратор для каждой из них.",
        font=("Segoe UI", 10, "bold"),
    ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
    ttk.Label(
        main,
        text=(
            "Пересчёт каждой четверти будет сохранён в отдельный файл. "
            "Ошибка одной четверти не остановит остальные."
        ),
        foreground="#555555",
        wraplength=650,
    ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

    for column, text in enumerate(("Выбрать", "Четверть", "Пиксель со спектром", "Доступно")):
        ttk.Label(main, text=text, font=("Segoe UI", 9, "bold")).grid(
            row=2,
            column=column,
            sticky="w",
            padx=(0, 10),
            pady=(0, 4),
        )

    selected_vars: Dict[int, tk.BooleanVar] = {}
    pixel_vars: Dict[int, tk.StringVar] = {}
    pixel_combos: Dict[int, ttk.Combobox] = {}
    config = app.series.config if app.series is not None else {}

    def update_combo_state(quarter_number: int) -> None:
        combo = pixel_combos[quarter_number]
        state = (
            "readonly"
            if candidates_by_quarter.get(quarter_number)
            and selected_vars[quarter_number].get()
            else "disabled"
        )
        combo.configure(state=state)

    for quarter_number in range(1, 5):
        candidates = candidates_by_quarter.get(quarter_number, [])
        selected_vars[quarter_number] = tk.BooleanVar(value=bool(candidates))
        pixel_vars[quarter_number] = tk.StringVar(value=candidates[0] if candidates else "")
        row_number = quarter_number + 2
        check = ttk.Checkbutton(
            main,
            variable=selected_vars[quarter_number],
            command=lambda q=quarter_number: update_combo_state(q),
        )
        check.grid(row=row_number, column=0, sticky="w", padx=(2, 14), pady=5)
        if not candidates:
            check.configure(state="disabled")

        code = quarter_code(config, quarter_number)
        description = quarter_description(config, quarter_number).strip()
        quarter_label = f"{quarter_number} — {code}"
        if description:
            quarter_label += f" · {description}"
        ttk.Label(main, text=quarter_label).grid(
            row=row_number,
            column=1,
            sticky="w",
            padx=(0, 12),
            pady=5,
        )
        combo = ttk.Combobox(
            main,
            values=candidates,
            textvariable=pixel_vars[quarter_number],
            state="readonly" if candidates else "disabled",
            width=28,
        )
        combo.grid(row=row_number, column=2, sticky="ew", padx=(0, 12), pady=5)
        pixel_combos[quarter_number] = combo
        ttk.Label(
            main,
            text=f"{len(candidates)}" if candidates else "нет спектров",
            foreground="#555555",
        ).grid(row=row_number, column=3, sticky="w", pady=5)

    result: Dict[str, Optional[List[str]]] = {"pixels": None}

    def set_all(selected: bool) -> None:
        for quarter_number in range(1, 5):
            if candidates_by_quarter.get(quarter_number):
                selected_vars[quarter_number].set(selected)
                update_combo_state(quarter_number)

    def confirm() -> None:
        selected_pixels = [
            pixel_vars[quarter_number].get()
            for quarter_number in range(1, 5)
            if selected_vars[quarter_number].get()
            and pixel_vars[quarter_number].get()
        ]
        if not selected_pixels:
            messagebox.showwarning(
                "Спектральная калибровка",
                "Выберите хотя бы одну четверть.",
                parent=dialog,
            )
            return
        result["pixels"] = selected_pixels
        dialog.destroy()

    buttons = ttk.Frame(main)
    buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(12, 0))
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
    fit_toplevel_to_content(dialog, 720, 330)
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

    completed: List[Dict[str, Any]] = []
    skipped: List[str] = []
    errors: List[str] = []
    for pixel_id in pixel_ids:
        try:
            result = _calibrate_quarter_pixel(app, pixel_id)
            if result is None:
                skipped.append(pixel_id)
            else:
                completed.append(result)
        except Exception as exc:
            errors.append(f"{pixel_id}: {exc}")
            app.log(f"Ошибка спектрального пересчёта {pixel_id}: {exc}")

    summary_lines: List[str] = []
    if completed:
        summary_lines.append(f"Успешно пересчитано четвертей: {len(completed)}.")
        for result in completed:
            summary_lines.append(
                f"• Четверть {result['quarter']}, {result['pixel']}: "
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
            "\nФайлы сохранены отдельно рядом с исходными спектрами. "
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


def _calibrate_quarter_pixel(app, pixel_id: str) -> Optional[Dict[str, Any]]:
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
    quarter_number = int(row.get("Quarter number") or 1)
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
            "Спектральный интеграл четверти",
            (
                f"Для четверти {quarter_number} уже есть калибровка по пикселю "
                f"{source_pixel}.\n\n"
                "Да — применить сохранённый интеграл четверти к выбранному спектру.\n"
                "Нет — заменить калибровку четверти расчётом по выбранному пикселю.\n"
                "Отмена — пропустить эту четверть и продолжить остальные."
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
        )
    output_path = create_spectral_recalculation_workbook(
        spectral_recalculation_output_path(workbook_path, pixel_id),
        workbook_path,
        pixel_id,
        quarter_number,
        points,
        calibration,
        rgb_photodiode_coefficient=rgb_photo_coefficient,
    )
    if not use_stored_calibration:
        app.series.save_quarter_integral_calibration(
            quarter_number,
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
        else f"рассчитан по {pixel_id} и назначен четверти"
    )
    app.log(
        f"Четверть {quarter_number}: спектральный интеграл "
        f"{calibration.coefficient:.9g}, интегральный коэффициент "
        f"{configured_integral:.9g}; произведение "
        f"{calibration.coefficient * configured_integral:.9g} "
        f"заменяет R/G/B и {action_text}; точек {calibration.points_used}, "
        f"разброс={relative_std}{fit_text}. "
        f"Результат: {output_path.name}"
    )
    return {
        "quarter": quarter_number,
        "pixel": pixel_id,
        "coefficient": calibration.coefficient,
        "output": output_path,
        "used_stored_calibration": use_stored_calibration,
    }
