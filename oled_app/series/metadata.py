"""Series quarter metadata helpers."""

from __future__ import annotations

from typing import Any, Dict

from oled_app.utils import safe_filename


LED_COLOR_RED = "red"
LED_COLOR_GREEN = "green"
LED_COLOR_BLUE = "blue"

LED_COLOR_SUFFIXES = {
    LED_COLOR_RED: "R",
    LED_COLOR_GREEN: "G",
    LED_COLOR_BLUE: "B",
}

LED_COLOR_LABELS = {
    LED_COLOR_RED: "Красный (R)",
    LED_COLOR_GREEN: "Зеленый (G)",
    LED_COLOR_BLUE: "Синий (B)",
}

LED_COLOR_COEFFICIENT_KEYS = {
    LED_COLOR_RED: "luminance_red_cd_m2_per_uA",
    LED_COLOR_GREEN: "luminance_green_cd_m2_per_uA",
    LED_COLOR_BLUE: "luminance_blue_cd_m2_per_uA",
}


def normalize_led_color(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "r": LED_COLOR_RED,
        "red": LED_COLOR_RED,
        "к": LED_COLOR_RED,
        "красный": LED_COLOR_RED,
        "g": LED_COLOR_GREEN,
        "green": LED_COLOR_GREEN,
        "з": LED_COLOR_GREEN,
        "зеленый": LED_COLOR_GREEN,
        "зелёный": LED_COLOR_GREEN,
        "b": LED_COLOR_BLUE,
        "blue": LED_COLOR_BLUE,
        "с": LED_COLOR_BLUE,
        "синий": LED_COLOR_BLUE,
    }
    return aliases.get(text, LED_COLOR_RED)


def led_color_suffix(value: Any) -> str:
    return LED_COLOR_SUFFIXES[normalize_led_color(value)]


def led_color_label(value: Any) -> str:
    return LED_COLOR_LABELS[normalize_led_color(value)]


def led_color_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for color, label in LED_COLOR_LABELS.items():
        if text == label:
            return color
    return normalize_led_color(text)


def quarter_base(config: Dict[str, Any], quarter_number: int) -> str:
    key = str(quarter_number)
    bases = config.get("quarter_bases")
    if isinstance(bases, dict):
        return str(bases.get(key, "") or "Q").strip() or "Q"
    names = config.get("quarter_names") if isinstance(config.get("quarter_names"), dict) else {}
    legacy_name = str(names.get(key, f"Q{quarter_number}") or f"Q{quarter_number}").strip() or f"Q{quarter_number}"
    if len(legacy_name) > 1 and legacy_name[-1:].upper() in {"R", "G", "B"}:
        return legacy_name[:-1] or "Q"
    return legacy_name


def quarter_led_color(config: Dict[str, Any], quarter_number: int) -> str:
    if "series_led_color" in config:
        return normalize_led_color(config.get("series_led_color"))
    colors = config.get("quarter_led_colors")
    if isinstance(colors, dict):
        return normalize_led_color(colors.get(str(quarter_number)))
    names = config.get("quarter_names") if isinstance(config.get("quarter_names"), dict) else {}
    legacy_name = str(names.get(str(quarter_number), "") or "").strip()
    if len(legacy_name) > 1 and legacy_name[-1:].upper() in {"R", "G", "B"}:
        return normalize_led_color(legacy_name[-1:])
    return LED_COLOR_RED


def quarter_description(config: Dict[str, Any], quarter_number: int) -> str:
    descriptions = config.get("quarter_descriptions")
    if isinstance(descriptions, dict):
        return str(descriptions.get(str(quarter_number), "") or "")
    return ""


def quarter_code(config: Dict[str, Any], quarter_number: int) -> str:
    key = str(quarter_number)
    bases = config.get("quarter_bases")
    if isinstance(bases, dict):
        base = safe_filename(quarter_base(config, quarter_number), fallback="Q")
        return safe_filename(f"{base}{led_color_suffix(quarter_led_color(config, quarter_number))}", fallback=f"Q{quarter_number}")
    names = config.get("quarter_names") if isinstance(config.get("quarter_names"), dict) else {}
    return safe_filename(names.get(key, f"Q{quarter_number}"), fallback=f"Q{quarter_number}")


def build_quarter_names(quarter_bases: Dict[str, str], quarter_led_colors: Dict[str, str]) -> Dict[str, str]:
    config = {
        "quarter_bases": quarter_bases,
        "quarter_led_colors": quarter_led_colors,
    }
    return {str(q): quarter_code(config, q) for q in range(1, 5)}


def normalize_quarter_payload(
    quarter_bases: Dict[str, str],
    quarter_descriptions: Dict[str, str],
    quarter_led_colors: Dict[str, str],
) -> Dict[str, Any]:
    bases = {
        str(q): safe_filename(str(quarter_bases.get(str(q), "Q") or "Q").strip(), fallback="Q")
        for q in range(1, 5)
    }
    series_color = normalize_led_color(quarter_led_colors.get("1"))
    colors = {str(q): series_color for q in range(1, 5)}
    descriptions = {str(q): str(quarter_descriptions.get(str(q), "") or "").strip() for q in range(1, 5)}
    return {
        "series_led_color": series_color,
        "quarter_bases": bases,
        "quarter_descriptions": descriptions,
        "quarter_led_colors": colors,
        "quarter_names": build_quarter_names(bases, colors),
    }


def luminance_coefficient_for_color(app_settings: Dict[str, Any], color: Any) -> float:
    units = app_settings.get("measurement_units", {}) if isinstance(app_settings, dict) else {}
    fallback = float(units.get("luminance_cd_m2_per_uA", 1.0) or 1.0)
    key = LED_COLOR_COEFFICIENT_KEYS[normalize_led_color(color)]
    try:
        return float(units.get(key, fallback) or fallback)
    except Exception:
        return fallback
