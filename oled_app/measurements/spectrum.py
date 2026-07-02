"""Spectrum measurement workflow for the modular OLED application."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np

from oled_app.hardware import prepare_hardware_environment, safe_shutdown_smu
from oled_app.measurements.raw_io import RawCsvWriter, cleanup_raw_files, raw_csv_path
from oled_app.processing.spectrum_results import (
    SPECTRUM_SPECTRA_RAW_HEADERS,
    SPECTRUM_SUMMARY_RAW_HEADERS,
    build_spectrum_workbook_from_raw_csv,
    create_spectrum_workbook as write_spectrum_workbook,
)
from oled_app.utils import (
    current_density_mA_cm2,
    luminance_cd_m2,
    now_str,
    safe_filename,
    timestamp_for_file,
)


@dataclass
class SpectrumParams:
    com_port: str = "COM3"
    voltage_start: float = 2.0
    voltage_end: float = 5.0
    voltage_step: float = 0.1
    opening_voltage: Optional[float] = None
    voltage_start_source: str = "opening"
    current_limit_mA: float = 6.0
    photodiode_bias_V: float = -5.0
    photodiode_range: int = 4
    target_intensity: float = 40000.0
    intensity_min: float = 20000.0
    intensity_max: float = 55000.0
    saturation_level: float = 60000.0
    min_peak_width_nm: float = 15.0
    max_peak_width_nm: float = 150.0
    t_int_initial_s: float = 0.01
    t_int_min_s: float = 0.001
    t_int_max_s: float = 10.0
    discard_first_scan_after_tint_change: bool = True
    kp: float = 0.3
    ki: float = 0.05
    max_iterations: int = 20
    tolerance: float = 0.05
    led_type: str = "auto"
    peak_search_mode_for_tint: str = "auto"
    settle_time_voltage_s: float = 0.1
    settle_time_spectrum_s: float = 0.05
    dark_spectrum_enabled: bool = False
    dark_spectrum_scans: int = 3
    baseline_correction_enabled: bool = True
    peak_detection_enabled: bool = False
    pixel_area_mm2: float = 1.0
    luminance_cd_m2_per_uA: float = 1.0

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class SpectrumHelper:
    def __init__(self, params: SpectrumParams, log: Callable[[str], None]):
        self.params = params
        self.log = log
        self._last_integration_time_us: Optional[int] = None
        self.last_optimization_started_saturated = False
        self.last_optimization_started_saturated_at_10ms = False
        self.adaptive_initial_time_enabled = False

    def init_spectrometer(self):
        import seabreeze

        seabreeze.use("cseabreeze")
        from seabreeze.spectrometers import Spectrometer, list_devices

        devices = list_devices()
        if not devices:
            raise RuntimeError("Спектрометр не найден. Проверь USB и драйвер SeaBreeze.")
        spec = Spectrometer(devices[0])
        wavelengths = spec.wavelengths()
        self.log(f"Спектрометр: {spec.model}; диапазон {wavelengths[0]:.1f}-{wavelengths[-1]:.1f} нм")
        return spec

    def set_integration_time(self, spec, integration_time_s: float) -> Tuple[float, bool]:
        p = self.params
        integration_time_s = float(np.clip(integration_time_s, p.t_int_min_s, p.t_int_max_s))
        integration_time_us = max(1, int(round(integration_time_s * 1e6)))
        changed = self._last_integration_time_us != integration_time_us
        spec.integration_time_micros(integration_time_us)
        self._last_integration_time_us = integration_time_us
        return integration_time_us / 1e6, changed

    def get_spectrum(self, spec, integration_time_s: float, discard_stale_after_change: bool = True):
        actual_t, changed = self.set_integration_time(spec, integration_time_s)
        wavelengths = spec.wavelengths()
        if discard_stale_after_change and self.params.discard_first_scan_after_tint_change and changed:
            time.sleep(self.params.settle_time_spectrum_s)
            try:
                _ = spec.intensities()
            except Exception as exc:
                self.log(f"  Не удалось сбросить первый спектр после смены T_int: {exc}")
        time.sleep(self.params.settle_time_spectrum_s)
        intensities = spec.intensities().astype(np.float64)
        return wavelengths, intensities, actual_t

    def get_dark_spectrum(self, spec, integration_time_s: float):
        spectra = []
        wavelengths = None
        actual_t = integration_time_s
        for i in range(self.params.dark_spectrum_scans):
            wavelengths, intensities, actual_t = self.get_spectrum(
                spec,
                integration_time_s,
                discard_stale_after_change=(i == 0),
            )
            spectra.append(intensities)
            time.sleep(0.05)
        return wavelengths, np.mean(spectra, axis=0), actual_t

    @staticmethod
    def smooth_array(values: np.ndarray, window: int = 9) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        if values.size < 3:
            return values.copy()
        window = int(max(3, min(window, values.size if values.size % 2 == 1 else values.size - 1)))
        if window < 3:
            return values.copy()
        kernel = np.ones(window, dtype=np.float64) / window
        padded = np.pad(values, (window // 2, window // 2), mode="edge")
        return np.convolve(padded, kernel, mode="valid")

    def estimate_baseline(self, wavelengths: np.ndarray, intensities: np.ndarray) -> Tuple[np.ndarray, Tuple[float, float], float]:
        wavelengths = np.asarray(wavelengths, dtype=np.float64)
        intensities = np.asarray(intensities, dtype=np.float64)
        if intensities.size < 3:
            value = float(np.nanmean(intensities)) if intensities.size else 0.0
            return np.full_like(intensities, value), (float("nan"), float("nan")), value

        finite = np.where(np.isfinite(wavelengths) & np.isfinite(intensities))[0]
        if finite.size < 3:
            value = float(np.nanmean(intensities[finite])) if finite.size else 0.0
            return np.full_like(intensities, value), (float("nan"), float("nan")), value

        n = int(finite.size)
        window = int(np.clip(round(n * 0.06), 8, 40))
        window = min(window, n)
        best_start = 0
        best_score = float("inf")
        for start in range(0, n - window + 1):
            idx = finite[start:start + window]
            x = wavelengths[idx]
            y = intensities[idx]
            if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)):
                continue
            span = float(max(x[-1] - x[0], 1e-9))
            slope = float(abs(y[-1] - y[0]) / span)
            scatter = float(np.nanstd(y))
            level_penalty = float(max(np.nanmean(y), 0.0)) * 1e-6
            score = scatter + slope * span * 0.25 + level_penalty
            if score < best_score:
                best_score = score
                best_start = start

        baseline_idx = finite[best_start:best_start + window]
        baseline_value = float(np.nanmean(intensities[baseline_idx]))
        baseline = np.full_like(intensities, baseline_value)
        wl_range = (float(wavelengths[baseline_idx[0]]), float(wavelengths[baseline_idx[-1]]))
        return baseline, wl_range, baseline_value

    def find_peaks_by_derivatives(self, wavelengths: np.ndarray, intensities: np.ndarray) -> List[Dict[str, float]]:
        wavelengths = np.asarray(wavelengths, dtype=np.float64)
        intensities = np.asarray(intensities, dtype=np.float64)
        if intensities.size < 5:
            return []

        smooth = self.smooth_array(intensities, window=9)
        d1 = np.gradient(smooth, wavelengths)
        d2 = np.gradient(d1, wavelengths)
        noise = float(np.nanmedian(np.abs(smooth - self.smooth_array(smooth, window=21)))) if smooth.size >= 21 else 0.0
        threshold = max(float(np.nanmax(smooth)) * 0.05, noise * 5.0, 1e-9)
        peaks: List[Dict[str, float]] = []

        for i in range(1, smooth.size - 1):
            sign_change = d1[i - 1] > 0 and d1[i + 1] < 0
            local_max = smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]
            concave_down = d2[i] < 0
            if sign_change and local_max and concave_down and smooth[i] >= threshold:
                half = smooth[i] / 2.0
                left = i
                while left > 0 and smooth[left] >= half:
                    left -= 1
                right = i
                while right < smooth.size - 1 and smooth[right] >= half:
                    right += 1
                peaks.append(
                    {
                        "wavelength_nm": float(wavelengths[i]),
                        "intensity": float(smooth[i]),
                        "fwhm_nm": float(max(wavelengths[right] - wavelengths[left], 0.0)),
                    }
                )

        peaks.sort(key=lambda item: item["intensity"], reverse=True)
        return peaks[:8]

    def process_spectrum(
        self,
        wavelengths: np.ndarray,
        spectrum: np.ndarray,
        dark: Optional[np.ndarray],
        integration_time_s: float,
    ) -> Dict[str, Any]:
        raw = np.asarray(spectrum, dtype=np.float64)
        dark_corrected = raw.copy()
        if dark is not None and self.params.dark_spectrum_enabled:
            dark_corrected = dark_corrected - np.asarray(dark, dtype=np.float64)
        if self.params.baseline_correction_enabled:
            baseline, baseline_region, baseline_value = self.estimate_baseline(wavelengths, raw)
        else:
            baseline = np.zeros_like(raw)
            baseline_region = (float("nan"), float("nan"))
            baseline_value = 0.0
        baseline_corrected = raw - baseline
        normalized = baseline_corrected / max(float(integration_time_s), 1e-9)
        peaks = self.find_peaks_by_derivatives(wavelengths, normalized) if self.params.peak_detection_enabled else []
        return {
            "raw": raw,
            "dark_corrected": dark_corrected,
            "baseline": baseline,
            "baseline_region": baseline_region,
            "baseline_value": baseline_value,
            "baseline_corrected": baseline_corrected,
            "normalized": normalized,
            "peaks": peaks,
        }

    @staticmethod
    def peak_range(wavelengths, mode: str) -> Tuple[float, float]:
        ranges = {
            "red": (580, 700),
            "green": (480, 580),
            "blue": (400, 500),
            "other": (300, 1000),
            "auto": (380, 780),
            "visible": (380, 780),
            "all": (float(wavelengths[0]), float(wavelengths[-1])),
        }
        return ranges.get(mode, ranges["auto"])

    def find_peak_region(self, wavelengths, intensities, mode: str):
        wl_min, wl_max = self.peak_range(wavelengths, mode)
        mask = (wavelengths >= wl_min) & (wavelengths <= wl_max)
        if not np.any(mask):
            return None, None, None
        roi_wl = wavelengths[mask]
        roi_int = intensities[mask]
        if len(roi_int) == 0 or np.all(~np.isfinite(roi_int)):
            return None, None, None
        idx = int(np.nanargmax(roi_int))
        peak_wl = float(roi_wl[idx])
        peak_int = float(roi_int[idx])
        half = peak_int / 2.0
        above = roi_int >= half
        if np.any(above):
            inds = np.where(above)[0]
            fwhm = float(roi_wl[inds[-1]] - roi_wl[inds[0]])
        else:
            fwhm = 0.0
        return peak_wl, peak_int, fwhm

    def analyze_quality(self, wavelengths, intensities, mode: str):
        p = self.params
        peak_wl, peak_int, fwhm = self.find_peak_region(wavelengths, intensities, mode)
        if peak_int is None:
            return 0.0, 0.0, 0.0, False, True, False, False, "NO_PEAK"
        is_sat = bool(np.any(intensities >= p.saturation_level))
        is_weak = bool(peak_int < p.intensity_min)
        is_wide = bool(fwhm > p.max_peak_width_nm)
        is_narrow = bool(fwhm < p.min_peak_width_nm)
        if is_sat:
            status = "SATURATED"
        elif is_weak:
            status = "TOO_WEAK"
        elif is_wide:
            status = "TOO_WIDE"
        elif is_narrow:
            status = "TOO_NARROW"
        elif p.intensity_min <= peak_int <= p.intensity_max:
            status = "GOOD"
        else:
            status = "OK"
        return peak_int, peak_wl, fwhm, is_sat, is_weak, is_wide, is_narrow, status

    def optimize_integration_time(self, spec):
        p = self.params
        t_int = p.t_int_initial_s
        integral = 0.0
        best = None
        best_score = float("inf")
        self.last_optimization_started_saturated = False
        self.last_optimization_started_saturated_at_10ms = False

        self.log(f"  Подбор T_int: цель {p.target_intensity:.0f} counts, область {p.peak_search_mode_for_tint}")
        for iteration in range(1, p.max_iterations + 1):
            wl, inten, actual_t = self.get_spectrum(spec, t_int)
            peak_int, peak_wl, fwhm, is_sat, is_weak, _, _, status = self.analyze_quality(wl, inten, p.peak_search_mode_for_tint)
            if iteration == 1 and is_sat:
                self.last_optimization_started_saturated = True
                self.last_optimization_started_saturated_at_10ms = actual_t >= 0.0095
            if is_sat:
                score = float("inf")
            elif is_weak:
                score = abs(peak_int - p.target_intensity) * 10.0
            else:
                score = abs(peak_int - p.target_intensity)
            if score < best_score and not is_sat:
                best_score = score
                best = (actual_t, wl.copy(), inten.copy(), peak_int, peak_wl, fwhm, status)

            self.log(f"    {iteration}: T={actual_t*1000:.2f} мс, peak={peak_int:.0f} @ {peak_wl:.1f} нм, {status}")

            if status == "GOOD" or (status == "OK" and abs(peak_int - p.target_intensity) / p.target_intensity < p.tolerance):
                best = (actual_t, wl.copy(), inten.copy(), peak_int, peak_wl, fwhm, status)
                break
            if is_sat and actual_t <= p.t_int_min_s + 1e-4:
                best = (actual_t, wl.copy(), inten.copy(), peak_int, peak_wl, fwhm, status)
                break
            if is_weak and actual_t >= p.t_int_max_s - 1e-3:
                best = (actual_t, wl.copy(), inten.copy(), peak_int, peak_wl, fwhm, status)
                break

            current_int = max(peak_int, 1.0)
            error = (p.target_intensity - current_int) / p.target_intensity
            if abs(error) > 0.9:
                kp = p.kp * 5
                ki = p.ki * 3
            elif abs(error) > 0.7:
                kp = p.kp * 3
                ki = p.ki * 2
            elif abs(error) > 0.5:
                kp = p.kp * 2
                ki = p.ki * 1.5
            else:
                kp = p.kp
                ki = p.ki
            integral += ki * error
            integral = float(np.clip(integral, -2.0, 2.0))
            adjustment = float(np.clip(kp * error + integral, -0.9, 5.0))
            t_int = float(np.clip(actual_t * (1.0 + adjustment), p.t_int_min_s, p.t_int_max_s))

        if best is None:
            return None
        t, wl, inten, _, _, _, _ = best
        dark = None
        if p.dark_spectrum_enabled:
            _, dark, _ = self.get_dark_spectrum(spec, t)
        return t, wl, inten, dark


def create_spectrum_workbook(filename: Path, pixel_id: str, params: SpectrumParams, voltage_array: Iterable[float]) -> Any:
    return write_spectrum_workbook(filename, pixel_id, params, voltage_array)


def run_spectrum_measurement(
    pixel_id: str,
    output_dir: Path,
    params: SpectrumParams,
    log: Callable[[str], None],
    app_settings: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[
        Callable[[int, float, float, np.ndarray, np.ndarray, np.ndarray, List[Dict[str, float]], str], None]
    ] = None,
) -> Dict[str, Any]:
    prepare_hardware_environment(pixel_id, app_settings, log)
    import xtralien

    helper = SpectrumHelper(params, log)
    spec = helper.init_spectrometer()

    output_dir.mkdir(parents=True, exist_ok=True)
    voltage_array = np.arange(params.voltage_start, params.voltage_end + params.voltage_step / 2, params.voltage_step)
    voltage_array = np.round(voltage_array, 6)
    measurement_timestamp = timestamp_for_file()
    file_stem = f"SPECTRUM_{safe_filename(pixel_id)}_{measurement_timestamp}"
    filename = output_dir / f"{file_stem}.xlsx"
    summary_raw_file = raw_csv_path(output_dir, f"{file_stem}_summary_raw.csv", app_settings)
    spectra_raw_file = raw_csv_path(output_dir, f"{file_stem}_spectra_raw.csv", app_settings)
    log(f"Raw CSV спектров: {summary_raw_file.name}, {spectra_raw_file.name}")

    final_status = "FAILED"
    best_spectrum_metrics = {
        "spectrum_peak_count": None,
        "spectrum_peaks_nm": "",
        "spectrum_max_intensity": None,
    }

    with RawCsvWriter(summary_raw_file, SPECTRUM_SUMMARY_RAW_HEADERS) as summary_writer:
        with RawCsvWriter(spectra_raw_file, SPECTRUM_SPECTRA_RAW_HEADERS) as spectra_writer:
            with xtralien.Device(params.com_port) as smu:
                try:
                    smu.smu1.set.enabled(True, response=0)
                    smu.smu2.set.enabled(True, response=0)
                    try:
                        smu.smu2.set.range(params.photodiode_range, response=0)
                    except Exception:
                        pass
                    smu.smu1.set.voltage(0, response=0)
                    smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
                    time.sleep(0.3)

                    for idx, voltage in enumerate(voltage_array, start=1):
                        log(f"\nСпектр {pixel_id}: точка {idx}/{len(voltage_array)}, V={voltage:.3f} В")
                        smu.smu1.set.voltage(float(voltage), response=0)
                        smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
                        time.sleep(params.settle_time_voltage_s)

                        v_led, i_led = smu.smu1.measure()[0]
                        v_pd, i_pd = smu.smu2.measure()[0]
                        i_led_mA = i_led * 1000.0
                        i_pd_uA = -i_pd * 1_000_000.0
                        j_led = current_density_mA_cm2(i_led_mA, params.pixel_area_mm2)
                        lum = luminance_cd_m2(i_pd_uA, params.luminance_cd_m2_per_uA)

                        if i_led_mA >= params.current_limit_mA:
                            status = "NEEDS_REVIEW"
                            log(f"  Стоп: ток {i_led_mA:.3f} мА >= {params.current_limit_mA:.3f} мА")
                            summary_writer.writerow(
                                {
                                    "point": idx,
                                    "date_time": now_str(),
                                    "voltage_set_V": float(voltage),
                                    "voltage_led_measured_V": float(v_led),
                                    "current_led_A": float(i_led),
                                    "current_led_mA": float(i_led_mA),
                                    "current_density_mA_cm2": j_led,
                                    "voltage_photodiode_measured_V": float(v_pd),
                                    "current_photodiode_A": float(i_pd),
                                    "current_photodiode_uA": float(i_pd_uA),
                                    "luminance_cd_m2": lum,
                                    "status": status,
                                    "peaks_detected": 0,
                                }
                            )
                            final_status = status
                            break

                        opt = helper.optimize_integration_time(spec)
                        if opt is None:
                            status = "FAILED"
                            summary_writer.writerow(
                                {
                                    "point": idx,
                                    "date_time": now_str(),
                                    "voltage_set_V": float(voltage),
                                    "voltage_led_measured_V": float(v_led),
                                    "current_led_A": float(i_led),
                                    "current_led_mA": float(i_led_mA),
                                    "current_density_mA_cm2": j_led,
                                    "voltage_photodiode_measured_V": float(v_pd),
                                    "current_photodiode_A": float(i_pd),
                                    "current_photodiode_uA": float(i_pd_uA),
                                    "luminance_cd_m2": lum,
                                    "integration_time_s": params.t_int_initial_s,
                                    "status": status,
                                    "peaks_detected": 0,
                                }
                            )
                            final_status = status
                            continue

                        t_int, wavelengths, spectrum, dark = opt
                        processed = helper.process_spectrum(wavelengths, spectrum, dark, t_int)
                        spectrum_to_save = processed["baseline_corrected"]
                        baseline_value = float(processed.get("baseline_value", 0.0))
                        baseline_region = processed.get("baseline_region", (float("nan"), float("nan")))
                        baseline_region_text = (
                            f"{float(baseline_region[0]):.2f}-{float(baseline_region[1]):.2f}"
                            if np.all(np.isfinite(np.asarray(baseline_region, dtype=np.float64)))
                            else ""
                        )
                        peaks = processed["peaks"]

                        peak_int, peak_wl, fwhm, _, _, _, _, status = helper.analyze_quality(
                            wavelengths,
                            processed["baseline_corrected"],
                            params.led_type,
                        )
                        if peaks:
                            peak_int = peaks[0]["intensity"]
                            peak_wl = peaks[0]["wavelength_nm"]
                            fwhm = peaks[0]["fwhm_nm"]
                        if status not in {"SATURATED", "FAILED", "NO_PEAK"} and (
                            helper.adaptive_initial_time_enabled or helper.last_optimization_started_saturated_at_10ms
                        ):
                            previous_t = float(params.t_int_initial_s)
                            params.t_int_initial_s = float(t_int)
                            helper.adaptive_initial_time_enabled = True
                            if helper.last_optimization_started_saturated_at_10ms:
                                log(
                                    f"  Первая проба на 10 мс была saturated; следующее начальное T_int: "
                                    f"{params.t_int_initial_s*1000:.2f} мс вместо {previous_t*1000:.2f} мс"
                                )
                        peaks_nm = ", ".join(f"{peak['wavelength_nm']:.1f}" for peak in peaks)
                        if peak_int and (
                            best_spectrum_metrics["spectrum_max_intensity"] is None
                            or float(peak_int) > float(best_spectrum_metrics["spectrum_max_intensity"])
                        ):
                            best_spectrum_metrics = {
                                "spectrum_peak_count": len(peaks),
                                "spectrum_peaks_nm": peaks_nm,
                                "spectrum_max_intensity": float(peak_int),
                            }
                        summary_writer.writerow(
                            {
                                "point": idx,
                                "date_time": now_str(),
                                "voltage_set_V": float(voltage),
                                "voltage_led_measured_V": float(v_led),
                                "current_led_A": float(i_led),
                                "current_led_mA": float(i_led_mA),
                                "current_density_mA_cm2": j_led,
                                "voltage_photodiode_measured_V": float(v_pd),
                                "current_photodiode_A": float(i_pd),
                                "current_photodiode_uA": float(i_pd_uA),
                                "luminance_cd_m2": lum,
                                "integration_time_s": float(t_int),
                                "status": status,
                                "peak_nm": float(peak_wl) if peak_wl else "",
                                "peak_intensity_processed_counts": float(peak_int) if peak_int else "",
                                "fwhm_nm": float(fwhm) if fwhm else "",
                                "peaks_detected": len(peaks),
                                "peaks_nm": peaks_nm,
                                "baseline_value_raw_counts": baseline_value,
                                "baseline_region_nm": baseline_region_text,
                            }
                        )

                        dark_array = np.asarray(dark, dtype=np.float64) if dark is not None else None
                        for wavelength, raw_value, dark_value in zip(
                            wavelengths,
                            processed["raw"],
                            dark_array if dark_array is not None else np.full_like(processed["raw"], np.nan),
                        ):
                            spectra_writer.writerow(
                                {
                                    "point": idx,
                                    "voltage_set_V": float(voltage),
                                    "integration_time_s": float(t_int),
                                    "wavelength_nm": float(wavelength),
                                    "raw_counts": float(raw_value),
                                    "dark_counts": "" if not np.isfinite(dark_value) else float(dark_value),
                                }
                            )

                        final_status = status
                        if progress_callback is not None:
                            progress_callback(idx, float(voltage), float(t_int), wavelengths, processed["raw"], spectrum_to_save, peaks, status)
                        log(f"  Raw CSV: T_int={t_int*1000:.2f} мс, peak={peak_int:.0f} @ {peak_wl:.1f} нм, {status}")
                finally:
                    safe_shutdown_smu(smu)

    filename = build_spectrum_workbook_from_raw_csv(summary_raw_file, spectra_raw_file, filename, pixel_id, params)
    kept_raw_files = cleanup_raw_files([summary_raw_file, spectra_raw_file], app_settings, log)
    return {
        "file": filename,
        "raw_files": kept_raw_files,
        "status": final_status,
        **best_spectrum_metrics,
    }
