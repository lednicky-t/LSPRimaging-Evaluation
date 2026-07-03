from __future__ import annotations

from pathlib import Path

import numpy as np


def alpha01(value: float) -> float:
    return float(np.clip(float(value), 0.0, 1.0))


def settings_int(settings, key: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = settings.value(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def settings_bool(settings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def read_bool_setting(settings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def read_float_setting(settings, key: str, default: float) -> float:
    value = settings.value(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def normalized_odd_count(value: int, minimum: int, maximum: int) -> int:
    minimum = max(int(minimum), 1)
    maximum = max(int(maximum), minimum)
    parsed = max(int(value), minimum)
    if parsed % 2 == 0:
        parsed += 1
    if parsed > maximum:
        parsed = maximum if maximum % 2 == 1 else max(maximum - 1, minimum)
    if parsed < minimum:
        parsed = minimum if minimum % 2 == 1 else min(minimum + 1, maximum)
    return parsed


def chromatic_feature_count_value(current_data, options: tuple[int, ...], default: int = 15) -> int:
    try:
        parsed = int(current_data)
    except (TypeError, ValueError):
        parsed = default
    if parsed not in options:
        parsed = default
    return parsed


def chromatic_subpixel_precision_value(current_data, default: int = 4) -> int:
    try:
        return int(current_data)
    except (TypeError, ValueError):
        return default


def display_length_suffix(use_micrometers: bool) -> str:
    return " \u00b5m" if use_micrometers else " px"


def length_px_to_display(use_micrometers: bool, microns_per_pixel: float, value_px: float) -> float:
    if use_micrometers:
        return float(value_px) * float(microns_per_pixel)
    return float(value_px)


def length_display_to_px(use_micrometers: bool, microns_per_pixel: float, displayed_value: float) -> float:
    if use_micrometers:
        return float(displayed_value) / float(microns_per_pixel)
    return float(displayed_value)


def format_length_display_value(use_micrometers: bool, microns_per_pixel: float, value_px: float) -> float:
    return float(round(length_px_to_display(use_micrometers, microns_per_pixel, value_px)))


def circle_area_text(use_micrometers: bool, microns_per_pixel: float, diameter_px: float) -> str:
    radius_px = max(float(diameter_px) / 2.0, 0.0)
    if use_micrometers:
        radius_um = radius_px * float(microns_per_pixel)
        return f"{np.pi * radius_um * radius_um:.0f} \u00b5m\u00b2"
    return f"{np.pi * radius_px * radius_px:.0f} px\u00b2"


def ring_area_text(use_micrometers: bool, microns_per_pixel: float, inner_diameter_px: float, outer_diameter_px: float) -> str:
    inner_radius_px = max(float(inner_diameter_px) / 2.0, 0.0)
    outer_radius_px = max(float(outer_diameter_px) / 2.0, inner_radius_px)
    if use_micrometers:
        scale = float(microns_per_pixel)
        inner_radius_um = inner_radius_px * scale
        outer_radius_um = outer_radius_px * scale
        return f"{np.pi * max(outer_radius_um * outer_radius_um - inner_radius_um * inner_radius_um, 0.0):.0f} \u00b5m\u00b2"
    return f"{np.pi * max(outer_radius_px * outer_radius_px - inner_radius_px * inner_radius_px, 0.0):.0f} px\u00b2"


def area_value_text(use_micrometers: bool, microns_per_pixel: float, area_px2: float) -> str:
    area_value = float(max(area_px2, 0.0))
    if use_micrometers:
        area_value *= float(microns_per_pixel) ** 2
        return f"{area_value:.0f} \u00b5m\u00b2"
    return f"{area_value:.0f} px\u00b2"


def area_delta_text(use_micrometers: bool, microns_per_pixel: float, area_px2: float) -> str:
    area_value = float(area_px2)
    if use_micrometers:
        area_value *= float(microns_per_pixel) ** 2
        return f"{area_value:.0f} \u00b5m\u00b2"
    return f"{area_value:.0f} px\u00b2"


def current_ome_zarr_compression_enabled(is_checked: bool) -> bool:
    return bool(is_checked)
