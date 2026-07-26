"""GUI action for assigning one spectrum calibration to an entire quarter."""

from __future__ import annotations

from tkinter import messagebox

from oled_app.processing.spectral_calibration import (
    QuarterIntegralCalibration,
    calibrate_quarter_spectral_integral,
    create_spectral_recalculation_workbook,
    read_spectrum_integral_points,
    spectral_recalculation_output_path,
)
from oled_app.utils import as_float_or_none, luminance_cd_m2, resolve_series_file


def calibrate_quarter_from_latest_spectrum(app) -> None:
    if app.series is None:
        return
    candidates = [
        str(row.get("Pixel ID"))
        for row in app.series.journal.list_pixels()
        if row.get("Last spectrum file")
    ]
    if not candidates:
        messagebox.showwarning(
            "Интегральный пересчёт",
            "В серии пока нет сохранённых спектров.",
            parent=app,
        )
        return
    pixel_id = app.ask_pixel(
        "Пиксель-калибратор четверти",
        candidates,
    )
    if not pixel_id:
        return
    row = app.series.journal.get_pixel(pixel_id) or {}
    workbook_path = resolve_series_file(
        app.series.series_folder,
        row.get("Last spectrum file"),
    )
    if workbook_path is None:
        messagebox.showerror(
            "Интегральный пересчёт",
            f"Не найден последний файл спектра {pixel_id}.",
            parent=app,
        )
        return

    try:
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
        stored_calibration = app.series.integral_calibration_for_pixel(pixel_id)
        if (
            stored_calibration is not None
            and stored_calibration.get("method") != "normalized_shape_integral_median"
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
                    "Отмена — не выполнять пересчёт."
                ),
                parent=app,
            )
            if choice is None:
                return
            use_stored_calibration = bool(choice)

        if use_stored_calibration:
            coefficient = as_float_or_none(stored_calibration.get("coefficient"))
            if coefficient is None or coefficient <= 0:
                raise ValueError("Сохранённый спектральный интеграл четверти некорректен.")
            calibration = QuarterIntegralCalibration(
                integral_coefficient=configured_integral,
                coefficient=float(coefficient),
                geometric_coefficient=geometry,
                effective_coefficient=float(coefficient) * configured_integral * geometry,
                points_used=int(stored_calibration.get("points_used") or 0),
                relative_std_percent=as_float_or_none(
                    stored_calibration.get("relative_std_percent")
                ),
                integral_min=float(stored_calibration.get("integral_min") or coefficient),
                integral_max=float(stored_calibration.get("integral_max") or coefficient),
                source_pixel=str(stored_calibration.get("source_pixel") or ""),
                source_file=str(stored_calibration.get("source_file") or ""),
                calculated_at=str(stored_calibration.get("calculated_at") or ""),
            )
        else:
            calibration = calibrate_quarter_spectral_integral(
                points,
                geometry,
                source_pixel=pixel_id,
                source_file=str(workbook_path),
                integral_coefficient=configured_integral,
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
            f"разброс={relative_std}. "
            f"Результат: {output_path.name}"
        )
        result_intro = (
            f"Сохранённая калибровка четверти {quarter_number} применена к {pixel_id}.\n\n"
            if use_stored_calibration
            else f"Калибровка пикселя {pixel_id} назначена всей четверти {quarter_number}.\n\n"
        )
        messagebox.showinfo(
            "Интегральный пересчёт",
            result_intro
            + (
                f"Интегральный коэффициент настроек: {configured_integral:.9g}\n"
                f"Спектральный интеграл четверти: {calibration.coefficient:.9g}\n"
                f"Новый коэффициент вместо R/G/B: "
                f"{calibration.coefficient * configured_integral:.9g}\n"
                f"Прежний коэффициент R/G/B с геометрией: {rgb_photo_coefficient:.9g}\n"
                f"Геометрический коэффициент: {geometry:.9g}\n"
                f"Точек: {calibration.points_used}\n"
                f"Относительный разброс интеграла: {relative_std}\n\n"
                f"Результаты сохранены отдельно:\n{output_path}\n\n"
                "Чтобы применить новый коэффициент к ранее снятым XLSX серии, "
                "нажмите «Пересчитать мкА → кд/м²»."
            ),
            parent=app,
        )
    except Exception as exc:
        messagebox.showerror(
            "Ошибка интегрального пересчёта",
            str(exc),
            parent=app,
        )
