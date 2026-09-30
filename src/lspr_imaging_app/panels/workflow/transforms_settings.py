"""Image Tools stage - "Transforms" section: one row of rotate / flip /
crop / measure icons.

The stable app's Transforms controls (rotate tool, reset rotation,
rotation-fill color, crop tool, reset crop, flip H/V, measure tool -
`gui/main_window.py`'s `rotate_action`/`reset_rotation_action`/
`rotation_fill_dark_button`/`crop_action`/`reset_crop_action`/
`flip_*_action`/`measure_action`), all eight in one row, in four groups:
rotation | flip | crop | measure (maintainer's ordering, 2026-09-29 - flip
moved between rotation and crop, and Measure folded back into this same
row instead of a second one underneath it, now that there's nothing left
in that second row to justify its own space - Measure's floating
on-canvas controls, `measure_controls.py`, carry the fields/apply the
stable app put in a second row of this same widget).

Icon choices, per the maintainer's spec (2026-09-28):

- **Rotate tool**: same vendored tabler `rotate-clockwise-2` glyph as the
  stable app (`MainWindowIcons._make_rotate_icon`), amber `#fbbf24` when
  active - the same literal the source hardcodes.
- **Reset rotation**: the same glyph with a diagonal slash across it. The
  stable app uses Qt's stock `SP_BrowserReload` icon here, which reads as
  "reload page", not "undo the rotation"; the slash is the usual
  "off/none" convention (tabler's own `*-off` icons do the same).
- **Rotation fill**: a plain, unlabeled `QCheckBox` (changed 2026-09-30,
  maintainer report - the icon-toggle version used the same amber `#fbbf24`
  the Rotate tool button uses for "tool is active", so a filled amber square
  next to an active amber rotate icon read as ambiguous - unclear whether
  the color meant "dark fill is on" or "the rotate tool is on". A
  checkbox's own checked state needs no color convention to read correctly
  - same fix already applied to "Ignore ROI"/"Ignore mask" in
  `background_removal.py` for the same reason, see that file's module
  docstring. No visible text label - a `QCheckBox("Dark fill", ...)` read
  clearly but pushed the row 20px past the Workflow panel's 320px width
  budget; the tooltip carries the explanation instead, same as every other
  button in this icon-only row.
- **Crop tool**: same vendored tabler `crop` glyph as the stable app, sky
  blue `#38bdf8` when active (the source's literal).
- **Reset crop**: the crop glyph with the same diagonal slash as reset
  rotation (the stable app uses Qt's stock `SP_DialogResetButton`).
- **Flip H / flip V**: tabler `flip-horizontal` / `flip-vertical`, checkable,
  green `#22c55e` / teal `#2dd4bf` when on (the source's literals).
- **Measure tool**: same vendored tabler `ruler-measure` glyph as the
  stable app (`MainWindowIcons._make_measure_icon`), but blue `#38bdf8`
  when active rather than the source's green literal - last group in the
  row, see module docstring. Deliberately *not* matching the stable app's
  color here: the maintainer's explicit preference (2026-09-29, "similar
  like cropping rectangle... keep it for most of the tools") overrides the
  "match the stable app" default this file otherwise follows.

All of them are drawn smaller than the stable app's (28x28 button / 22px icon
vs. 36x36 / 28px) so the row costs less of the Workflow panel's fixed
width budget.

**Also embedded a second time in the Image panel's tool ribbon** (2026-09-30,
maintainer request - "you can duplicate transform tools and put them in
image tools of image panel"): `panels/image/panel.py` constructs a second
`TransformsSection` instance for its ribbon's "Image tools" tab
(`tool_ribbon.py`), passing the same `GeometryModule`/`ActiveToolModule`
this Workflow-panel instance uses. Two independent widget instances, one
shared backend each - the "several front doors, one backend" pattern this
whole rewrite already uses for `RoiToolbox`/`ActiveToolModule` elsewhere,
not a new one invented for this. Neither instance owns state, so the two
stay in sync for free: rotating from either row updates both (each row's
`_sync_tool_buttons`/`_refresh_from_settings` just reacts to the same
module signals), with no direct connection between the two widgets.

**The rotate and crop tool buttons drive `ActiveToolModule`** (2026-09-28) -
the shared "which canvas tool is on" state the Image panel reads. That makes
the tools mutually exclusive without the buttons knowing about each other:
switching one on switches the other off, and the buttons re-sync when the
active tool changes from elsewhere (e.g. a dataset being closed). Both tools
are real: rotate (`panels/image/rotate_line_tool.py`) and, since 2026-09-29,
crop (`panels/image/crop_tool.py` - a click-drag rectangle, not the old
app's `pg.RectROI`). Reset rotation, fill, reset crop and flip are real too -
they call `GeometryModule` directly.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, tabler_icon_svg, transparent_icon_button_stylesheet

from ...image_tools import ActiveToolModule, GeometryModule, ImageTool

logger = logging.getLogger(__name__)

_ACTIVE_COLOR = "#fbbf24"
_CROP_ACTIVE_COLOR = "#38bdf8"
_FLIP_H_ACTIVE_COLOR = "#22c55e"
_FLIP_V_ACTIVE_COLOR = "#2dd4bf"
_MEASURE_ACTIVE_COLOR = "#38bdf8"  # same blue as Crop's - maintainer's preferred tool color (2026-09-29, was green)
_BUTTON_SIZE = 28
_ROW_MARGIN = 4
# The row's total height - `_BUTTON_SIZE` plus its own top/bottom layout
# margin (`_ROW_MARGIN`) on each side. Exposed so `panels/image/tool_ribbon
# .py` can size its fixed-height stack to fit this section without
# guessing at or duplicating these two numbers (this row is embedded a
# second time there - see this module's docstring).
ROW_HEIGHT = _BUTTON_SIZE + 2 * _ROW_MARGIN
# Extra gap between the rotation | flip | crop | measure groups (on top of the 6px spacing).
_GROUP_GAP = 6
_ICON_SIZE = 22
# Rendered at 2x and scaled down by Qt, so the smaller icon stays crisp
# rather than being a 24px bitmap squeezed into 22.
_RENDER_SIZE = _ICON_SIZE * 2
_STROKE_WIDTH = 2.1


def _rotate_icon(color: str) -> QIcon:
    return load_tabler_icon("rotate-clockwise-2", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


def _slashed_icon(name: str, color: str) -> QIcon:
    """A vendored tabler glyph with a top-right -> bottom-left slash added
    (the maintainer's preferred reset-icon convention - tabler's own
    `*-off` icons use the opposite diagonal, top-left -> bottom-right, but
    that reads worse against these two particular glyphs). Built by
    inserting one extra `<path>` into the vendored SVG (rather than
    vendoring a second icon file) so the slashed and plain icons can never
    drift apart in stroke width or geometry."""
    svg = tabler_icon_svg(name, color=color, stroke_width=_STROKE_WIDTH)
    if not svg:
        return QIcon()
    svg = svg.replace("</svg>", '  <path d="M21 3l-18 18" />\n</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter, QRectF(0.0, 0.0, _RENDER_SIZE, _RENDER_SIZE))
    finally:
        painter.end()
    return QIcon(pixmap)


def _reset_rotation_icon(color: str) -> QIcon:
    return _slashed_icon("rotate-clockwise-2", color)


def _crop_icon(color: str) -> QIcon:
    return load_tabler_icon("crop", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


def _reset_crop_icon(color: str) -> QIcon:
    return _slashed_icon("crop", color)


def _flip_icon(name: str, color: str) -> QIcon:
    return load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


def _measure_icon(color: str) -> QIcon:
    return load_tabler_icon("ruler-measure", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)


def _icon_button(parent: QWidget, *, checkable: bool) -> QToolButton:
    button = QToolButton(parent)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
    button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
    return button


class TransformsSection(QWidget):
    """Rotate/crop/measure tool toggles, reset rotation/crop, rotation fill, flip H/V."""

    def __init__(self, geometry: GeometryModule, active_tool: ActiveToolModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry_module = geometry
        self._active_tool = active_tool

        self._rotate_button = _icon_button(self, checkable=True)
        self._rotate_button.setToolTip(
            "Manual rotation tool. Arrow keys adjust angle: default 0.1 deg, Ctrl = 1 deg, Shift = 5 deg."
        )
        self._rotate_button.toggled.connect(self._on_rotate_toggled)

        self._reset_button = _icon_button(self, checkable=False)
        self._reset_button.setToolTip("Reset image rotation to 0 degrees.")
        self._reset_button.clicked.connect(self._on_reset_clicked)

        # A plain checkbox, not an icon toggle (2026-09-30, maintainer
        # report - see module docstring's "Rotation fill" entry for why the
        # old amber-square icon was ambiguous). No text label - a first pass
        # with `QCheckBox("Dark fill", ...)` read clearly but pushed this
        # row's `minimumSizeHint()` 20px past the Workflow panel's 320px
        # width budget (`test_lspri_workflow_panel_width_budget.py`); the
        # tooltip carries the same explanation every other button in this
        # icon-only row already relies on instead. `clicked`, not `toggled` -
        # same reasoning as the tool buttons below: `_refresh_from_settings`
        # calls `setChecked()` on session restore/undo, which must not
        # re-trigger `_on_fill_clicked`.
        self._fill_checkbox = QCheckBox(self)
        self._fill_checkbox.clicked.connect(self._on_fill_clicked)

        self._crop_button = _icon_button(self, checkable=True)
        self._crop_button.setToolTip("Crop tool. Drag a box on the image to crop it.")
        self._crop_button.toggled.connect(self._on_crop_toggled)

        self._reset_crop_button = _icon_button(self, checkable=False)
        self._reset_crop_button.setToolTip("Remove the current crop and show the full rotated image.")
        self._reset_crop_button.clicked.connect(self._on_reset_crop_clicked)

        self._flip_h_button = _icon_button(self, checkable=True)
        self._flip_h_button.setToolTip("Flip the image horizontally.")
        self._flip_h_button.clicked.connect(self._on_flip_clicked)

        self._flip_v_button = _icon_button(self, checkable=True)
        self._flip_v_button.setToolTip("Flip the image vertically.")
        self._flip_v_button.clicked.connect(self._on_flip_clicked)

        self._measure_button = _icon_button(self, checkable=True)
        self._measure_button.setToolTip(
            "Measurement/calibration tool: place two points a known real-world distance apart to "
            "calibrate microns-per-pixel."
        )
        self._measure_button.toggled.connect(self._on_measure_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_ROW_MARGIN, _ROW_MARGIN, _ROW_MARGIN, _ROW_MARGIN)
        layout.setSpacing(6)
        layout.addWidget(self._rotate_button)
        layout.addWidget(self._reset_button)
        layout.addWidget(self._fill_checkbox)
        layout.addSpacing(_GROUP_GAP)
        layout.addWidget(self._flip_h_button)
        layout.addWidget(self._flip_v_button)
        layout.addSpacing(_GROUP_GAP)
        layout.addWidget(self._crop_button)
        layout.addWidget(self._reset_crop_button)
        layout.addSpacing(_GROUP_GAP)
        layout.addWidget(self._measure_button)
        layout.addStretch(1)

        self._refresh_rotate_icon()
        self._refresh_crop_icon()
        self._refresh_measure_icon()
        self._refresh_from_settings()
        self._geometry_module.geometry_changed.connect(self._refresh_from_settings)
        self._active_tool.active_tool_changed.connect(self._sync_tool_buttons)
        self._sync_tool_buttons(self._active_tool.active())

    def _on_rotate_toggled(self, checked: bool) -> None:
        self._refresh_rotate_icon()
        self._active_tool.set_active(ImageTool.ROTATE, checked)

    def _on_reset_clicked(self) -> None:
        self._geometry_module.set_rotation(0.0)

    def _on_fill_clicked(self, checked: bool) -> None:
        self._geometry_module.set_rotation_fill_dark(checked)

    def _on_crop_toggled(self, checked: bool) -> None:
        self._refresh_crop_icon()
        self._active_tool.set_active(ImageTool.CROP, checked)

    def _on_measure_toggled(self, checked: bool) -> None:
        self._refresh_measure_icon()
        self._active_tool.set_active(ImageTool.MEASURE, checked)

    def _sync_tool_buttons(self, tool: object) -> None:
        """Follow `ActiveToolModule` - setChecked re-enters the toggled
        handlers above, which is harmless (`set_active` is idempotent)."""
        self._rotate_button.setChecked(tool is ImageTool.ROTATE)
        self._crop_button.setChecked(tool is ImageTool.CROP)
        self._measure_button.setChecked(tool is ImageTool.MEASURE)

    def _on_reset_crop_clicked(self) -> None:
        self._geometry_module.clear_crop()

    def _on_flip_clicked(self, _checked: bool = False) -> None:
        self._geometry_module.set_flip(self._flip_h_button.isChecked(), self._flip_v_button.isChecked())

    def _refresh_crop_icon(self) -> None:
        color = _CROP_ACTIVE_COLOR if self._crop_button.isChecked() else get_active_theme().text_primary
        self._crop_button.setIcon(_crop_icon(color))

    def _refresh_rotate_icon(self) -> None:
        color = _ACTIVE_COLOR if self._rotate_button.isChecked() else get_active_theme().text_primary
        self._rotate_button.setIcon(_rotate_icon(color))

    def _refresh_measure_icon(self) -> None:
        color = _MEASURE_ACTIVE_COLOR if self._measure_button.isChecked() else get_active_theme().text_primary
        self._measure_button.setIcon(_measure_icon(color))

    def _refresh_from_settings(self, _change: object = None) -> None:
        """Re-sync the reset/fill buttons from the module - covers undo/
        redo and session restore, not just this widget's own clicks."""
        settings = self._geometry_module.settings()
        theme = get_active_theme()

        # Nothing to reset at 0 deg; dimming the button says so, and
        # `set_rotation` would be a no-op there anyway.
        at_zero = settings.rotation_angle_deg == 0.0
        self._reset_button.setEnabled(not at_zero)
        self._reset_button.setIcon(_reset_rotation_icon(theme.text_dim if at_zero else theme.text_primary))

        dark = bool(settings.rotation_fill_dark)
        self._fill_checkbox.setChecked(dark)
        if dark:
            tooltip = (
                "Rotation fill: dark (0). New corner pixels created by rotation are set to 0 intensity "
                "instead of copying the nearest edge pixel. Uncheck for edge-stretch fill."
            )
        else:
            tooltip = (
                "Rotation fill: edge-stretch. New corner pixels created by rotation copy the nearest "
                "edge pixel. Check for dark (0 intensity) fill."
            )
        self._fill_checkbox.setToolTip(tooltip)

        # Same idea as reset rotation: nothing to reset without a crop.
        has_crop = bool(settings.crop.enabled)
        self._reset_crop_button.setEnabled(has_crop)
        self._reset_crop_button.setIcon(_reset_crop_icon(theme.text_primary if has_crop else theme.text_dim))

        for button, name, active_color, is_on in (
            (self._flip_h_button, "flip-horizontal", _FLIP_H_ACTIVE_COLOR, bool(settings.flip_horizontal)),
            (self._flip_v_button, "flip-vertical", _FLIP_V_ACTIVE_COLOR, bool(settings.flip_vertical)),
        ):
            button.setChecked(is_on)
            button.setIcon(_flip_icon(name, active_color if is_on else theme.text_primary))
