"""Background spectral calibration and luminance recalculation for v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import math
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Optional

from oled_app.processing.luminance_recalculation import recalculate_series_luminance
from oled_app.processing.spectral_calibration import (
    QuarterIntegralCalibration,
    calibrate_quarter_spectral_integral,
    create_spectral_recalculation_workbook,
    read_spectrum_integral_points,
    spectral_recalculation_output_path,
)
from oled_app.series import ensure_scope_calibration_folder
from oled_app.series.manager import SeriesManager
from oled_app.series.metadata import (
    description_scope_groups,
    quarter_code,
    quarter_description,
    series_description_scope,
    series_half_orientation,
)
from oled_app.settings import (
    DEFAULT_APP_SETTINGS,
    load_app_settings,
    save_app_settings,
)
from oled_app.utils import (
    SPECTRAL_CALIBRATION_METHODS,
    as_float_or_none,
    luminance_cd_m2,
    resolve_series_file,
)


class RecalculationError(RuntimeError):
    pass


class RecalculationValidationError(RecalculationError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operation_state() -> Dict[str, Any]:
    return {
        "status": "idle",
        "active": False,
        "operation": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
        "series_path": None,
        "progress": {"completed": 0, "total": 0, "current": ""},
    }


def _threshold(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecalculationValidationError(f"{label} должен быть числом.")
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 100:
        raise RecalculationValidationError(f"{label} должен быть больше 0 и не больше 100%.")
    return number


class RecalculationService:
    """Own one destructive or calibration operation at a time."""

    def __init__(
        self,
        logger=None,
        settings_loader: Callable[[], Dict[str, Any]] = load_app_settings,
        settings_saver: Callable[[Dict[str, Any]], Any] = save_app_settings,
    ) -> None:
        self._logger = logger
        self._settings_loader = settings_loader
        self._settings_saver = settings_saver
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._state = _operation_state()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def state(self, series_path: Optional[Path]) -> Dict[str, Any]:
        operation = self.snapshot()
        if series_path is None:
            return {"available": False, "operation": operation, "options": None}
        resolved = Path(series_path).resolve()
        if operation["active"] and operation.get("series_path") == str(resolved):
            return {"available": True, "operation": operation, "options": None}
        if (operation.get("series_path")
                and operation["series_path"] != str(resolved)
                and not operation["active"]):
            operation = _operation_state()
        return {
            "available": True,
            "operation": operation,
            "options": self.options(resolved),
        }

    def options(self, series_path: Path) -> Dict[str, Any]:
        manager = SeriesManager(Path(series_path))
        settings = self._settings_loader()
        defaults = DEFAULT_APP_SETTINGS["spectral_calibration"]
        configured = settings.get("spectral_calibration", {})
        rows = manager.journal.list_pixels()
        by_quarter: Dict[int, list[Dict[str, Any]]] = {number: [] for number in range(1, 5)}
        for row in rows:
            pixel_id = str(row.get("Pixel ID") or "").strip()
            source = resolve_series_file(manager.series_folder, row.get("Last spectrum file"))
            if not pixel_id or source is None:
                continue
            try:
                quarter = int(row.get("Quarter number") or 1)
            except (TypeError, ValueError):
                continue
            if quarter in by_quarter:
                by_quarter[quarter].append({
                    "pixel_id": pixel_id,
                    "quarter": quarter,
                    "source": str(source),
                })

        groups = []
        for quarters in description_scope_groups(
            series_description_scope(manager.config),
            series_half_orientation(manager.config),
        ):
            candidates = sorted(
                (item for quarter in quarters for item in by_quarter[quarter]),
                key=lambda item: item["pixel_id"],
            )
            codes = "+".join(f"{quarter_code(manager.config, number)}{number}" for number in quarters)
            description = quarter_description(manager.config, quarters[0]).strip()
            if len(quarters) == 1:
                label = f"Четверть {quarters[0]} — {codes}"
            elif len(quarters) == 2:
                label = f"Половина {'+'.join(map(str, quarters))} — {codes}"
            else:
                label = f"Вся подложка — {codes}"
            if description:
                label += f" · {description}"
            calibration = manager.config.get("quarter_integral_calibrations", {})
            stored = calibration.get(str(quarters[0])) if isinstance(calibration, dict) else None
            if not isinstance(stored, dict) or str(stored.get("method") or "") not in SPECTRAL_CALIBRATION_METHODS:
                stored = None
            groups.append({
                "key": "+".join(map(str, quarters)),
                "label": label,
                "quarters": list(quarters),
                "candidates": candidates,
                "stored_calibration": None if stored is None else {
                    "source_pixel": str(stored.get("source_pixel") or ""),
                    "method": str(stored.get("method") or ""),
                    "coefficient": as_float_or_none(stored.get("coefficient")),
                    "calculated_at": stored.get("calculated_at"),
                },
            })

        counts = {"IVL": 0, "SPECTRUM": 0, "STABILITY": 0}
        seen: set[Path] = set()
        for measurement in manager.journal.list_measurements():
            kind = str(measurement.get("Type") or "").upper()
            workbook = resolve_series_file(manager.series_folder, measurement.get("File"))
            if kind in counts and workbook is not None and workbook not in seen:
                counts[kind] += 1
                seen.add(workbook)
        return {
            "series_path": str(manager.series_folder.resolve()),
            "groups": groups,
            "thresholds": {
                "median_tolerance_percent": float(configured.get(
                    "median_tolerance_percent", defaults["median_tolerance_percent"]
                )),
                "linear_model_outlier_percent": float(configured.get(
                    "linear_model_outlier_percent", defaults["linear_model_outlier_percent"]
                )),
            },
            "measurement_counts": {**counts, "total": sum(counts.values())},
        }

    def start_calibration(self, series_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise RecalculationValidationError("Параметры калибровки должны быть объектом.")
        self._ensure_idle()
        resolved = Path(series_path).resolve()
        options = self.options(resolved)
        selections = payload.get("selections")
        if not isinstance(selections, dict):
            raise RecalculationValidationError("Выберите хотя бы одну область калибровки.")
        group_map = {group["key"]: group for group in options["groups"]}
        validated: list[Dict[str, Any]] = []
        for key, value in selections.items():
            group = group_map.get(str(key))
            if group is None or not isinstance(value, dict):
                raise RecalculationValidationError(f"Неизвестная область калибровки: {key}.")
            pixel_id = str(value.get("pixel_id") or "")
            strategy = str(value.get("strategy") or "replace")
            if strategy not in {"replace", "reuse"}:
                raise RecalculationValidationError("Режим калибровки должен быть replace или reuse.")
            if strategy == "reuse" and group["stored_calibration"] is None:
                raise RecalculationValidationError(f"Для области «{group['label']}» нет сохранённой калибровки.")
            candidate = next(
                (item for item in group["candidates"] if item["pixel_id"] == pixel_id),
                None,
            )
            if candidate is None:
                raise RecalculationValidationError(f"Выбранный спектр области «{group['label']}» не найден.")
            validated.append({**group, "candidate": candidate, "strategy": strategy})
        if not validated:
            raise RecalculationValidationError("Выберите хотя бы одну область калибровки.")

        thresholds = payload.get("thresholds")
        if not isinstance(thresholds, dict):
            raise RecalculationValidationError("Задайте пороги спектральной калибровки.")
        median = _threshold(thresholds.get("median_tolerance_percent"), "Допуск от медианы")
        linear = _threshold(
            thresholds.get("linear_model_outlier_percent"),
            "Порог перехода к линейной модели",
        )
        settings = self._settings_loader()
        settings.setdefault("spectral_calibration", {}).update({
            "median_tolerance_percent": median,
            "linear_model_outlier_percent": linear,
        })
        self._settings_saver(settings)
        self._start(
            "spectral_calibration",
            resolved,
            len(validated),
            self._run_calibration,
            validated,
            median,
            linear,
        )
        return self.snapshot()

    def start_luminance(self, series_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise RecalculationValidationError("Подтвердите замену расчётных XLSX.")
        self._ensure_idle()
        resolved = Path(series_path).resolve()
        options = self.options(resolved)
        total = int(options["measurement_counts"]["total"])
        if total <= 0:
            raise RecalculationValidationError("В серии нет книг ВАЯХ, спектров или стабильности.")
        self._start("luminance", resolved, total, self._run_luminance)
        return self.snapshot()

    def shutdown(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _ensure_idle(self) -> None:
        with self._lock:
            if self._state["active"]:
                raise RecalculationValidationError("Дождитесь завершения текущего пересчёта.")

    def _start(
        self,
        operation: str,
        series_path: Path,
        total: int,
        target: Callable[..., None],
        *args: Any,
    ) -> None:
        with self._lock:
            if self._state["active"]:
                raise RecalculationValidationError("Дождитесь завершения текущего пересчёта.")
            self._state = {
                **_operation_state(),
                "status": "running",
                "active": True,
                "operation": operation,
                "started_at": _utc_now(),
                "series_path": str(series_path),
                "progress": {"completed": 0, "total": total, "current": "Подготовка"},
            }
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(target, series_path, *args),
                daemon=True,
                name=f"oled-v2-{operation}",
            )
            self._thread.start()

    def _run_guarded(self, target: Callable[..., None], series_path: Path, *args: Any) -> None:
        try:
            result = target(series_path, *args)
            with self._lock:
                self._state.update({
                    "status": "completed",
                    "active": False,
                    "finished_at": _utc_now(),
                    "result": result,
                })
                self._state["progress"]["completed"] = self._state["progress"]["total"]
                self._state["progress"]["current"] = "Готово"
        except Exception as exc:
            with self._lock:
                self._state.update({
                    "status": "failed",
                    "active": False,
                    "finished_at": _utc_now(),
                    "error": str(exc),
                })
            if self._logger:
                self._logger.exception("Recalculation failed")

    def _run_calibration(
        self,
        series_path: Path,
        selections: list[Dict[str, Any]],
        median: float,
        linear: float,
    ) -> Dict[str, Any]:
        manager = SeriesManager(series_path)
        settings = self._settings_loader()
        completed = []
        for index, selection in enumerate(selections, start=1):
            candidate = selection["candidate"]
            pixel_id = candidate["pixel_id"]
            self._progress(index - 1, f"{selection['label']}: {pixel_id}")
            row = manager.journal.get_pixel(pixel_id) or {}
            workbook_path = resolve_series_file(manager.series_folder, row.get("Last spectrum file"))
            if workbook_path is None:
                raise FileNotFoundError(f"Не найден последний файл спектра {pixel_id}.")
            points = read_spectrum_integral_points(workbook_path)
            rgb_coefficient = manager.rgb_luminance_coefficient_for_pixel(pixel_id, settings)
            for point in points:
                point["rgb_luminance_cd_m2"] = luminance_cd_m2(
                    point.get("photodiode_uA"), rgb_coefficient
                )
            geometry = manager.geometric_coefficient(settings)
            integral = manager.configured_integral_coefficient(settings)
            opening_voltage = as_float_or_none(row.get("Opening voltage (V)"))
            quarters = tuple(int(number) for number in selection["quarters"])
            if selection["strategy"] == "reuse":
                stored = manager.integral_calibration_for_pixel(pixel_id)
                if stored is None:
                    raise RecalculationValidationError(f"Сохранённая калибровка {pixel_id} исчезла.")
                calibration = QuarterIntegralCalibration.from_dict(
                    stored,
                    integral_coefficient=integral,
                    geometric_coefficient=geometry,
                    activation_voltage_V=opening_voltage,
                )
            else:
                calibration = calibrate_quarter_spectral_integral(
                    points,
                    geometry,
                    source_pixel=pixel_id,
                    source_file=str(workbook_path),
                    integral_coefficient=integral,
                    activation_voltage_V=opening_voltage,
                    median_tolerance_percent=median,
                    linear_model_outlier_percent=linear,
                )
            output_folder = ensure_scope_calibration_folder(
                manager.series_folder, manager.config, quarters
            )
            output = create_spectral_recalculation_workbook(
                spectral_recalculation_output_path(
                    workbook_path, pixel_id, output_dir=output_folder
                ),
                workbook_path,
                pixel_id,
                int(row.get("Quarter number") or 1),
                points,
                calibration,
                rgb_photodiode_coefficient=rgb_coefficient,
                target_quarters=quarters,
            )
            if selection["strategy"] == "replace":
                manager.save_scope_integral_calibration(quarters, calibration.as_dict())
            manager.journal.update_after_measurement(
                "SPECTRAL_CALIBRATION",
                pixel_id,
                "RECALCULATED" if selection["strategy"] == "reuse" else "CALIBRATED",
                output,
                calibration.as_dict(),
                notes=f"Источник: {workbook_path.name}",
            )
            completed.append({
                "group": selection["key"],
                "pixel_id": pixel_id,
                "strategy": selection["strategy"],
                "method": calibration.method,
                "coefficient": calibration.coefficient,
                "output": str(output),
            })
            self._progress(index, output.name)
        return {"completed": len(completed), "items": completed}

    def _run_luminance(self, series_path: Path) -> Dict[str, Any]:
        manager = SeriesManager(series_path)
        messages: list[str] = []

        def log(message: str) -> None:
            messages.append(message)
            with self._lock:
                progress = self._state["progress"]
                if message.startswith(("Пересчитан ", "Ошибка пересчёта ")):
                    progress["completed"] = min(progress["total"], progress["completed"] + 1)
                progress["current"] = message

        report = recalculate_series_luminance(manager, self._settings_loader(), log=log)
        return {**asdict(report), "messages": messages[-30:]}

    def _progress(self, completed: int, current: str) -> None:
        with self._lock:
            self._state["progress"].update({"completed": completed, "current": current})
