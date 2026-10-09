"""Every UI-state value that is wired by hand (not a widget `bind`) as one
table (2026-10-07), and the one-time migration from the `AppSettings` fields
some of them replaced. A widget's own `ui_state.bind("area/name", widget)` key
stays next to the widget (that is the one-line convention); everything else is
named here, so no key is a string literal in two places.

Why: `AppSettings` had grown to 37 hand-wired fields (a field, a constructor
argument and a signal per value), the shape the 2026-09-30 status doc warned
about at 5. Values that belong to a panel's controls now live in the generic
`AppSettings.ui_state` dict (`panels/ui_state.py`), keyed "area/name". A value
that is wired by hand (not a widget `bind`) is one row below: key, default,
type. Adding one is a row plus its wiring line - no new `AppSettings` field, no
migration of the settings schema.

`AppSettings` keeps only app-level state (last dataset, theme, window and dock
layout, open Workflow section) and `ui_state` itself.

**Reading is validated.** A settings file is JSON a person can edit: a value of
the wrong type falls back to the row's default instead of reaching a panel.

**Migration** (`migrate_legacy_fields`, called by `load_app_settings`): a
settings file written before this change has the old fields at the top level.
Each is copied to its new key unless that key is already set (a new-style value
wins), and the old field is dropped on the next save. Composite values
(the Image view range, the highlight range with its dataset) are only migrated
when every part is present and valid. Nothing else about the file changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeGuard


class _Getter(Protocol):
    def get(self, key: str, default: object = None) -> object: ...


@dataclass(frozen=True)
class UiKey:
    key: str
    default: object
    kind: type  # bool, int, float or str
    legacy: str | None = None  # the AppSettings field this key replaced, migrated on load

    def valid(self, value: object) -> bool:
        if value is None:
            return self.default is None  # `None` is only a legal saved value for keys that default to it
        if self.kind is bool:
            return isinstance(value, bool)
        if self.kind is int:
            return isinstance(value, int) and not isinstance(value, bool)
        if self.kind is float:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return isinstance(value, self.kind)


def read(store: _Getter, key: UiKey) -> object:
    """The saved value if it has the right type, else the key's default."""
    value = store.get(key.key)
    if value is None or not key.valid(value):
        return key.default
    if key.kind is float and isinstance(value, int):
        return float(value)
    return value


# -- Histogram panel ---------------------------------------------------------------
HISTOGRAM_Y_MODE = UiKey("histogram/y_mode", "percent", str, "histogram_y_mode")
HISTOGRAM_LOG_Y = UiKey("histogram/log_y", False, bool, "histogram_log_y")
HISTOGRAM_BIN_WIDTH = UiKey("histogram/bin_width_px", 512.0, float, "histogram_bin_width_px")
HISTOGRAM_LINE_WIDTH = UiKey("histogram/line_width_px", 1.5, float, "histogram_line_width_px")

# -- Image panel: ribbon, overlays, background view -----------------------------------
IMAGE_RIBBON_CATEGORY = UiKey("image/ribbon_category", None, str, "image_ribbon_category")
IMAGE_SHOW_BACKGROUND = UiKey("image/show_background", False, bool, "show_background")
MASK_OVERLAY_VISIBLE = UiKey("image/mask_overlay_visible", True, bool, "mask_overlay_visible")
MASK_OVERLAY_COLOR = UiKey("image/mask_overlay_color", None, str, "mask_overlay_color")  # None = the theme's color
MASK_OVERLAY_ALPHA = UiKey("image/mask_overlay_alpha", 0.5, float, "mask_overlay_alpha")
HIGHLIGHT_OVERLAY_VISIBLE = UiKey("image/highlight_overlay_visible", True, bool, "highlight_overlay_visible")
HIGHLIGHT_OVERLAY_COLOR = UiKey("image/highlight_overlay_color", None, str, "highlight_overlay_color")
HIGHLIGHT_OVERLAY_ALPHA = UiKey("image/highlight_overlay_alpha", 0.42, float, "highlight_overlay_alpha")
OVERLAY_KEYS = {
    "mask": (MASK_OVERLAY_VISIBLE, MASK_OVERLAY_COLOR, MASK_OVERLAY_ALPHA),
    "highlight": (HIGHLIGHT_OVERLAY_VISIBLE, HIGHLIGHT_OVERLAY_COLOR, HIGHLIGHT_OVERLAY_ALPHA),
}

# -- Chromatic tab ------------------------------------------------------------------------
CHROMATIC_LANDMARK_COUNT = UiKey("chromatic/landmark_count", 15, int, "chromatic_landmark_count")
CHROMATIC_STRIDE = UiKey("chromatic/stride", 3, int, "chromatic_stride")
CHROMATIC_BORDER_PERCENT = UiKey("chromatic/border_percent", 5.0, float, "chromatic_border_percent")
CHROMATIC_MAX_STEP = UiKey("chromatic/max_step_px", 5.0, float, "chromatic_max_step_px")
CHROMATIC_FEATURE_DIAMETER = UiKey("chromatic/feature_diameter_px", None, float, "chromatic_feature_diameter_px")
CHROMATIC_SHOW_LANDMARKS = UiKey("chromatic/show_landmarks", True, bool, "chromatic_show_landmarks")
CHROMATIC_ALL_WAVELENGTHS = UiKey("chromatic/all_wavelengths", False, bool, "chromatic_landmarks_all_wavelengths")

# -- Keys with no retired field behind them (panels read and write these through `restore_ui_state`) ----
HISTOGRAM_CURSOR_READOUT = UiKey("histogram/cursor_readout", False, bool)
IMAGE_CURSOR_READOUT = UiKey("image/cursor_readout", False, bool)
IMAGE_MASK_EDIT_TOOL = UiKey("image/mask_edit_tool", None, str)  # a `MaskEditTool` value
IMAGE_SCALE_BAR_COLOR = UiKey("image/scale_bar_color", None, str)
IMAGE_ROI_LABELS = UiKey("image/roi_labels", False, bool)
ROI_OVERLAY_KEYS = {
    kind: {
        "visible": UiKey(f"image/roi_{kind}_visible", True, bool),
        "color": UiKey(f"image/roi_{kind}_color", None, str),  # None = the panel's own default colour
        "alpha": UiKey(f"image/roi_{kind}_alpha", 1.0, float),
    }
    for kind in ("sample", "reference")
}
IMAGE_ROI_FILL_MAX_OPACITY = UiKey("image/roi_fill_max_opacity", 1.0, float)  # Options menu; 1.0 = fill fully opaque at slider 100 %
ROI_ROUND_POSITIONS = UiKey("roi/round_positions", True, bool)  # Options menu: store ROI positions to 0.1 px
MASK_SCOPE = UiKey("mask/scope", None, str)  # a `MaskScope` value
ROI_SCOPE = UiKey("roi/scope", None, str)  # "persistent" | "individual"; None = persistent
ARRAY_KEYS = {  # ROIs tab, "Array" group (panels/image/array_controls.py); lengths are stored in pixels
    "mode": UiKey("array/mode", "auto", str),
    "rows": UiKey("array/rows", 0, int),
    "cols": UiKey("array/cols", 0, int),
    "diameter": UiKey("array/diameter_px", 0.0, float),
    "pitch_x": UiKey("array/pitch_x_px", 0.0, float),
    "pitch_y": UiKey("array/pitch_y_px", 0.0, float),
    "rotation": UiKey("array/rotation_deg", 0.0, float),
    "anchor_x": UiKey("array/anchor_x_px", 0.0, float),
    "anchor_y": UiKey("array/anchor_y_px", 0.0, float),
    "snap": UiKey("array/snap_to_image", False, bool),
    "bright": UiKey("array/bright_spots", False, bool),
    "edge_model": UiKey("array/edge_model", "plateau_fraction", str),
    "edge_fraction": UiKey("array/edge_fraction_percent", 15.0, float),
    "edge_sigma": UiKey("array/edge_sigma_k", 3.0, float),
    "size_mode": UiKey("array/size_mode", "uniform", str),
    "inner_mode": UiKey("array/ring_inner_mode", "measured", str),
    "ring_fraction": UiKey("array/ring_background_percent", 5.0, float),
    "ring_margin": UiKey("array/ring_margin_px", 0.0, float),
    "inner_ratio": UiKey("array/ring_inner_ratio", 1.4, float),
    "thickness_mode": UiKey("array/ring_thickness_mode", "equal_area", str),
    "thickness": UiKey("array/ring_width_px", 6.0, float),
    "outer_ratio": UiKey("array/ring_outer_ratio", 1.25, float),
    "ring_size_mode": UiKey("array/ring_size_mode", "uniform", str),
}
HAND_WIRED_KEYS: tuple[UiKey, ...] = (
    HISTOGRAM_CURSOR_READOUT, IMAGE_CURSOR_READOUT, IMAGE_MASK_EDIT_TOOL, IMAGE_SCALE_BAR_COLOR, IMAGE_ROI_LABELS,
    IMAGE_ROI_FILL_MAX_OPACITY,
    *(key for keys_for_kind in ROI_OVERLAY_KEYS.values() for key in keys_for_kind.values()),
    ROI_ROUND_POSITIONS, MASK_SCOPE, ROI_SCOPE, *ARRAY_KEYS.values(),
)

ALL_KEYS: tuple[UiKey, ...] = (
    HISTOGRAM_Y_MODE, HISTOGRAM_LOG_Y, HISTOGRAM_BIN_WIDTH, HISTOGRAM_LINE_WIDTH,
    IMAGE_RIBBON_CATEGORY, IMAGE_SHOW_BACKGROUND,
    MASK_OVERLAY_VISIBLE, MASK_OVERLAY_COLOR, MASK_OVERLAY_ALPHA,
    HIGHLIGHT_OVERLAY_VISIBLE, HIGHLIGHT_OVERLAY_COLOR, HIGHLIGHT_OVERLAY_ALPHA,
    CHROMATIC_LANDMARK_COUNT, CHROMATIC_STRIDE, CHROMATIC_BORDER_PERCENT, CHROMATIC_MAX_STEP,
    CHROMATIC_FEATURE_DIAMETER, CHROMATIC_SHOW_LANDMARKS, CHROMATIC_ALL_WAVELENGTHS,
)

# -- Composite values (several parts that only make sense together) ------------------------------
REFERENCE_FRAME = "reference_frame"
"""``{"dataset": str, "mode": "auto" | "manual", "cube": int | None, "wavelength": float | None}``,
kept per dataset like the highlight range."""

ROI_TABLE_SORT = "roi_table/sort"  # [column, descending]
ROI_TABLE_COLLAPSED = "roi_table/collapsed"  # sorted group ids
ROI_TABLE_COLUMN_WIDTHS = "roi_table/column_widths"  # one width per column

IMAGE_VIEW_RANGE = "image/view_range"
"""``[[x_min, x_max], [y_min, y_max]]`` in image pixels; replaced the four
`image_view_*` fields. Applied only when all four numbers are present."""

HIGHLIGHT_RANGE = "histogram/highlight_range"
"""``{"dataset": str, "min": float, "max": float}``: an intensity range means
nothing on another dataset, so it carries the dataset folder it was set on
(replaced `highlight_range_min/max/dataset`)."""


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers(value: object, count: int) -> list[float] | None:
    """`value` as exactly `count` numbers, or `None`."""
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    numbers: list[float] = []
    for item in value:
        if not _number(item):
            return None
        numbers.append(float(item))
    return numbers


def read_view_range(store: _Getter) -> tuple[tuple[float, float], tuple[float, float]] | None:
    value = store.get(IMAGE_VIEW_RANGE)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x_range, y_range = _numbers(value[0], 2), _numbers(value[1], 2)
    if x_range is None or y_range is None:
        return None
    return (x_range[0], x_range[1]), (y_range[0], y_range[1])


def read_highlight_range(store: _Getter) -> tuple[str, float, float] | None:
    """``(dataset_folder, min, max)`` or `None` when nothing valid is saved."""
    value = store.get(HIGHLIGHT_RANGE)
    if not isinstance(value, dict):
        return None
    dataset, low, high = value.get("dataset"), value.get("min"), value.get("max")
    if not isinstance(dataset, str) or not _number(low) or not _number(high):
        return None
    return dataset, float(low), float(high)


def migrate_legacy_fields(payload: Mapping[str, object], ui_state: dict[str, object]) -> bool:
    """Copy the retired top-level `AppSettings` fields in `payload` into
    `ui_state` (see the module docstring). Returns whether anything was
    migrated. An existing `ui_state` value is never overwritten."""
    changed = False
    for ui_key in ALL_KEYS:
        if ui_key.legacy is None or ui_key.legacy not in payload or ui_key.key in ui_state:
            continue
        value = payload[ui_key.legacy]
        if value is not None and ui_key.valid(value):
            ui_state[ui_key.key] = float(value) if ui_key.kind is float and isinstance(value, int) else value
            changed = True

    if IMAGE_VIEW_RANGE not in ui_state:
        parts = _numbers(
            [payload.get(name) for name in ("image_view_x_min", "image_view_x_max", "image_view_y_min", "image_view_y_max")],
            4,
        )
        if parts is not None:
            ui_state[IMAGE_VIEW_RANGE] = [[parts[0], parts[1]], [parts[2], parts[3]]]
            changed = True

    if HIGHLIGHT_RANGE not in ui_state:
        low, high = payload.get("highlight_range_min"), payload.get("highlight_range_max")
        dataset = payload.get("highlight_range_dataset")
        if _number(low) and _number(high) and isinstance(dataset, str):
            ui_state[HIGHLIGHT_RANGE] = {"dataset": dataset, "min": float(low), "max": float(high)}
            changed = True
    return changed
