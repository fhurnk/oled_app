"""Series quarter metadata helpers."""

from __future__ import annotations

from itertools import permutations
from typing import Any, Dict

from oled_app.utils import safe_filename


LED_COLOR_RED = "red"
LED_COLOR_GREEN = "green"
LED_COLOR_BLUE = "blue"
LED_COLOR_WHITE = "white"

DESCRIPTION_SCOPE_QUARTER = "quarter"
DESCRIPTION_SCOPE_HALF = "half"
DESCRIPTION_SCOPE_SUBSTRATE = "substrate"

QUARTER_LAYOUT_POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right")
DEFAULT_QUARTER_LAYOUT = {
    "top_left": 2,
    "top_right": 1,
    "bottom_left": 3,
    "bottom_right": 4,
}
QUARTER_LAYOUT_OPTIONS = tuple(permutations((1, 2, 3, 4)))

LED_COLOR_SUFFIXES = {
    LED_COLOR_RED: "R",
    LED_COLOR_GREEN: "G",
    LED_COLOR_BLUE: "B",
    LED_COLOR_WHITE: "W",
}

LED_COLOR_LABELS = {
    LED_COLOR_RED: "Красный (R)",
    LED_COLOR_GREEN: "Зеленый (G)",
    LED_COLOR_BLUE: "Синий (B)",
    LED_COLOR_WHITE: "Белый (W)",
}

LED_COLOR_COEFFICIENT_KEYS = {
    LED_COLOR_RED: "luminance_red_cd_m2_per_uA",
    LED_COLOR_GREEN: "luminance_green_cd_m2_per_uA",
    LED_COLOR_BLUE: "luminance_blue_cd_m2_per_uA",
    LED_COLOR_WHITE: "luminance_white_cd_m2_per_uA",
}

DESCRIPTION_SCOPE_LABELS = {
    DESCRIPTION_SCOPE_QUARTER: "Для каждой четверти",
    DESCRIPTION_SCOPE_HALF: "Для каждой половины",
    DESCRIPTION_SCOPE_SUBSTRATE: "Для всей подложки",
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
        "w": LED_COLOR_WHITE,
        "white": LED_COLOR_WHITE,
        "б": LED_COLOR_WHITE,
        "белый": LED_COLOR_WHITE,
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


def normalize_description_scope(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        DESCRIPTION_SCOPE_QUARTER: DESCRIPTION_SCOPE_QUARTER,
        "quarters": DESCRIPTION_SCOPE_QUARTER,
        "четверть": DESCRIPTION_SCOPE_QUARTER,
        DESCRIPTION_SCOPE_HALF: DESCRIPTION_SCOPE_HALF,
        "halves": DESCRIPTION_SCOPE_HALF,
        "половина": DESCRIPTION_SCOPE_HALF,
        DESCRIPTION_SCOPE_SUBSTRATE: DESCRIPTION_SCOPE_SUBSTRATE,
        "whole": DESCRIPTION_SCOPE_SUBSTRATE,
        "подложка": DESCRIPTION_SCOPE_SUBSTRATE,
    }
    return aliases.get(text, DESCRIPTION_SCOPE_QUARTER)


def description_scope_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for scope, label in DESCRIPTION_SCOPE_LABELS.items():
        if text == label:
            return scope
    return normalize_description_scope(text)


def series_description_scope(config: Dict[str, Any]) -> str:
    return normalize_description_scope(config.get("description_scope"))


def normalize_quarter_layout(value: Any) -> Dict[str, int]:
    if isinstance(value, dict):
        try:
            layout = {
                position: int(value.get(position))
                for position in QUARTER_LAYOUT_POSITIONS
            }
        except (TypeError, ValueError):
            layout = {}
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            layout = dict(zip(QUARTER_LAYOUT_POSITIONS, (int(item) for item in value)))
        except (TypeError, ValueError):
            layout = {}
    else:
        layout = {}
    if set(layout.values()) != {1, 2, 3, 4}:
        return dict(DEFAULT_QUARTER_LAYOUT)
    return layout


def quarter_layout_order(value: Any) -> tuple[int, int, int, int]:
    layout = normalize_quarter_layout(value)
    return tuple(layout[position] for position in QUARTER_LAYOUT_POSITIONS)


def quarter_layout_label(value: Any) -> str:
    top_left, top_right, bottom_left, bottom_right = quarter_layout_order(value)
    return f"{top_left} {top_right} / {bottom_left} {bottom_right}"


def quarter_layout_from_label(value: Any) -> Dict[str, int]:
    text = str(value or "")
    numbers = [int(char) for char in text if char in "1234"]
    return normalize_quarter_layout(numbers)


def series_quarter_layout(config: Dict[str, Any]) -> Dict[str, int]:
    return normalize_quarter_layout(config.get("quarter_layout"))


def description_scope_groups(
    value: Any,
    quarter_layout: Any = None,
) -> tuple[tuple[int, ...], ...]:
    scope = normalize_description_scope(value)
    if scope == DESCRIPTION_SCOPE_HALF:
        top_left, top_right, bottom_left, bottom_right = quarter_layout_order(quarter_layout)
        return ((top_left, top_right), (bottom_left, bottom_right))
    if scope == DESCRIPTION_SCOPE_SUBSTRATE:
        return ((1, 2, 3, 4),)
    return tuple((quarter_number,) for quarter_number in quarter_layout_order(quarter_layout))


def expand_descriptions_for_scope(
    quarter_descriptions: Dict[str, str],
    value: Any,
    quarter_layout: Any = None,
) -> Dict[str, str]:
    descriptions = {
        str(q): str(quarter_descriptions.get(str(q), "") or "").strip()
        for q in range(1, 5)
    }
    for group in description_scope_groups(value, quarter_layout):
        shared = descriptions[str(group[0])]
        for quarter_number in group:
            descriptions[str(quarter_number)] = shared
    return descriptions


def quarter_base(config: Dict[str, Any], quarter_number: int) -> str:
    key = str(quarter_number)
    bases = config.get("quarter_bases")
    if isinstance(bases, dict):
        return str(bases.get(key, "") or "Q").strip() or "Q"
    names = config.get("quarter_names") if isinstance(config.get("quarter_names"), dict) else {}
    legacy_name = str(names.get(key, f"Q{quarter_number}") or f"Q{quarter_number}").strip() or f"Q{quarter_number}"
    if len(legacy_name) > 1 and legacy_name[-1:].upper() in {"R", "G", "B", "W"}:
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
    if len(legacy_name) > 1 and legacy_name[-1:].upper() in {"R", "G", "B", "W"}:
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
    description_scope: Any = DESCRIPTION_SCOPE_QUARTER,
    quarter_layout: Any = None,
) -> Dict[str, Any]:
    bases = {
        str(q): safe_filename(str(quarter_bases.get(str(q), "Q") or "Q").strip(), fallback="Q")
        for q in range(1, 5)
    }
    series_color = normalize_led_color(quarter_led_colors.get("1"))
    colors = {str(q): series_color for q in range(1, 5)}
    normalized_scope = normalize_description_scope(description_scope)
    normalized_layout = normalize_quarter_layout(quarter_layout)
    descriptions = expand_descriptions_for_scope(
        quarter_descriptions,
        normalized_scope,
        normalized_layout,
    )
    return {
        "series_led_color": series_color,
        "description_scope": normalized_scope,
        "quarter_layout": normalized_layout,
        "quarter_bases": bases,
        "quarter_descriptions": descriptions,
        "quarter_led_colors": colors,
        "quarter_names": build_quarter_names(bases, colors),
    }


def base_luminance_coefficient_for_color(app_settings: Dict[str, Any], color: Any) -> float:
    units = app_settings.get("measurement_units", {}) if isinstance(app_settings, dict) else {}
    # Legacy settings from v1.7.5/v1.7.6 may still contain the old shared coefficient.
    fallback = float(units.get("luminance_cd_m2_per_uA", 1.0) or 1.0)
    key = LED_COLOR_COEFFICIENT_KEYS[normalize_led_color(color)]
    try:
        return float(units.get(key, fallback) or fallback)
    except Exception:
        return fallback


def geometric_conversion_coefficient(app_settings: Dict[str, Any]) -> float:
    units = app_settings.get("measurement_units", {}) if isinstance(app_settings, dict) else {}
    try:
        value = float(units.get("geometric_conversion_coefficient", 1.0) or 1.0)
        return value if value > 0 else 1.0
    except Exception:
        return 1.0


def default_integral_conversion_coefficient(app_settings: Dict[str, Any]) -> float:
    units = app_settings.get("measurement_units", {}) if isinstance(app_settings, dict) else {}
    try:
        value = float(units.get("integral_conversion_coefficient", 1.0) or 1.0)
        return value if value > 0 else 1.0
    except Exception:
        return 1.0


def luminance_coefficient_for_color(app_settings: Dict[str, Any], color: Any) -> float:
    """Return the effective color coefficient including the geometry factor."""

    return (
        base_luminance_coefficient_for_color(app_settings, color)
        * geometric_conversion_coefficient(app_settings)
    )
