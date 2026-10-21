"""Image Tools stage - "Transforms" section: one row of rotate / flip /
crop / measure icons.

The stable app's Transforms controls (rotate tool, reset rotation,
crop tool, reset crop, flip H/V, measure tool -
`gui/main_window.py`'s `rotate_action`/`reset_rotation_action`/
`crop_action`/`reset_crop_action`/
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
- **Rotation fill**: there is no such control. Pixels created by rotation
  have no measurement behind them and are always NaN (removed 2026-10-03,
  TASK_rotation_fill_handling); the former dark/edge-stretch checkbox is gone.
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

**Every cluster is now its own captioned group**: "Rotation" (rotate tool +
reset - "2 rotation icons"), "Flip" (H + V), "Crop" (crop
tool + reset - "2 crop icons"), "Calibrate" (Measure). Measure was split
out first (2026-10-02, maintainer request: "first free icons be Rotate,
then Flip, and Crop, last icon is in Calibrate section" - Rotate/Flip/Crop
were initially left as one plain, uncaptioned row, "free icons", since
that request read as contrasting a *remaining* ungrouped cluster against
Calibrate); a follow-up request the same day ("you did not make rotation
section containing 3 rotation icons, and flip section containing flipping
icons, and crop section containing 2 crop icons") asked for the other
three to be captioned the same way, completing the pattern - every cluster
now sits in its own `ribbon_group.labeled_icon_group`-wrapped group behind
a `vertical_separator`, matching the Mask tab's own State/Visibility/
Manual-edit/PNG captioned-group convention exactly. `ribbon_group.py`
(`panels/`, not `panels/image/`) is where that pattern lives, shared with
`panels/image/panel.py` - a direct import from there would have been a
real circular import (`panel.py` imports this module), see that file's own
docstring. `_cluster()` (this file) wraps a multi-button group's children
in a plain row before handing it to `labeled_icon_group`, which otherwise
takes one widget (Calibrate's own bare `_measure_button` needs no such
wrapper).

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
app's `pg.RectROI`). Reset rotation, reset crop and flip are real too -
they call `GeometryModule` directly.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, tabler_icon_svg, transparent_icon_button_stylesheet

from ...image_tools import ActiveToolModule, GeometryModule, ImageTool
from ..ribbon_group import labeled_icon_group, ribbon_separator
from ..ribbon_icon_size import CAPTIONED_GROUP_HEIGHT as _CALIBRATE_GROUP_HEIGHT
from .general_group import BUTTON_SIZE as _BUTTON_SIZE  # the ribbon's one icon-button size
from .general_group import ICON_SIZE as _ICON_SIZE

logger = logging.getLogger(__name__)

_ACTIVE_COLOR = "#fbbf24"
_CROP_ACTIVE_COLOR = "#38bdf8"
_FLIP_H_ACTIVE_COLOR = "#22c55e"
_FLIP_V_ACTIVE_COLOR = "#2dd4bf"
_MEASURE_ACTIVE_COLOR = "#38bdf8"  # same blue as Crop's - maintainer's preferred tool color (2026-09-29, was green)
# Auto crop: green when all three dependencies are met, amber while any is missing (maintainer, 2026-10-21).
_AUTO_CROP_READY_COLOR = "#22c55e"
_AUTO_CROP_WAITING_COLOR = "#f59e0b"
# Its dependencies, in the order the maintainer works through them: (key, label, where it is done, icon). The key
# is what `navigate_requested` carries for the two that are a trip to another ribbon tab; "level" runs Auto rotate
# here instead, and takes that button's own glyph (icon ``None``). The others reuse a vendored icon the matching
# tool already uses, so this adds no icon file (`packages/lspr_ui/ICONS.md`).
_AUTO_CROP_STEPS = (
    ("level", "Level the array", "Auto rotate (Rotation group)", None),
    ("rois", "Place the ROIs", "ROIs tab, Array group", "grid-dots"),
    ("chromatic", "Fit the chromatic correction", "Chromatic tab", "color-picker"),
)
# The "Calibrate" group (Measure, captioned - see module docstring) is
# taller than a plain icon button: icon row + 2px spacing + 12px
# single-line 9px-font caption, the same height `ribbon_group.py`'s other
# captioned groups land on (`ribbon_icon_size.CAPTIONED_GROUP_HEIGHT`).
# The row's total height is that tallest child. No margin round the row
# (2026-10-19): it used to have 4 px on every side, which put this tab's icons
# and captions 4 px lower (and 4 px further right) than every other tab's, and
# made this tab, and so the whole ribbon, 8 px taller than any content needs.
# Exposed so `panels/image/tool_ribbon.py` can size its fixed-height stack to
# fit this section without guessing at or duplicating these numbers.
ROW_HEIGHT = max(_BUTTON_SIZE, _CALIBRATE_GROUP_HEIGHT)
# Extra gap between the rotation | flip | crop | measure groups (on top of the 6px spacing).
_GROUP_GAP = 6
# Rendered at 2x and scaled down by Qt, so the smaller icon stays crisp
# rather than being a double-size bitmap squeezed into the icon size.
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
    return _glyph_with_paths(name, color, '<path d="M21 3l-18 18" />')


def _auto_rotate_icon(color: str) -> QIcon:
    """The rotate glyph with a small "A" (auto) inside its arc - same insert-a-path approach as `_slashed_icon`
    (maintainer's request 2026-10-20: "icon of rotation and A in the middle")."""
    return _glyph_with_paths("rotate-clockwise-2", color, '<path d="M9.6 15.2l2.4 -6.4l2.4 6.4m-3.5 -2.3h2.2" />')


class _AutoCropState(NamedTuple):
    """What the owner last told this widget about Auto crop's dependencies (`set_auto_crop_state`)."""

    levelled: bool | None
    rois: bool
    chromatic: bool
    level_note: str


def _auto_crop_icon(color: str) -> QIcon:
    """The crop glyph with the same small "A" (auto) inside it that `_auto_rotate_icon` puts in the rotate arc,
    so the two automatic tools read as a pair (2026-10-21)."""
    return _glyph_with_paths("crop", color, '<path d="M9.6 15.2l2.4 -6.4l2.4 6.4m-3.5 -2.3h2.2" />')


def _glyph_with_paths(name: str, color: str, extra_paths: str) -> QIcon:
    """A vendored tabler glyph with extra SVG paths drawn in the same stroke (colour and width)."""
    svg = tabler_icon_svg(name, color=color, stroke_width=_STROKE_WIDTH)
    if not svg:
        return QIcon()
    svg = svg.replace("</svg>", f"  {extra_paths}\n</svg>")
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


def _cluster(parent: QWidget, *widgets: QWidget) -> QWidget:
    """A plain row of widgets, with no caption of its own - the content
    `labeled_icon_group` wraps for Rotation/Flip/Crop below, each a
    multi-button cluster rather than the single widget `labeled_icon_group`
    otherwise takes directly (as Calibrate's bare `_measure_button` does)."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    for widget in widgets:
        layout.addWidget(widget)
    return row


class TransformsSection(QWidget):
    """Rotate/crop/measure tool toggles, auto rotate, auto crop, reset rotation/crop, flip H/V.

    **Auto crop's two states** (2026-10-21): it depends on three things the user does elsewhere - the array
    levelled, the ROIs placed, the chromatic correction fitted - which the owner measures and pushes in through
    `set_auto_crop_state`. All three met: the icon is green and a click crops (`auto_crop_requested`). Any
    missing: the icon is amber and a click opens a pick-up menu of **only the missing steps**, each row leading to
    the tool that does it ("Level the array" runs Auto rotate here; the other two emit `navigate_requested`, which
    the Image panel turns into showing that ribbon tab). The tooltip always lists all three with a check mark or a
    cross, and the measured tilt when there is one.
    """

    # Auto rotate clicked: the owner (Image panel) runs the array detection (`ArrayActions.auto_rotate`), which
    # lives with the ROI tools; this widget owns no state and never computes. Auto crop is the same arrangement
    # (`ArrayActions.auto_crop` crops to the ROI lattice; it neither detects nor rotates).
    auto_rotate_requested = pyqtSignal()
    auto_crop_requested = pyqtSignal()
    # A row of Auto crop's pick-up menu that is not an action of this ribbon tab: "rois" / "chromatic", which the
    # Image panel turns into showing that ribbon tab.
    navigate_requested = pyqtSignal(str)

    def __init__(self, geometry: GeometryModule, active_tool: ActiveToolModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry_module = geometry
        self._active_tool = active_tool

        self._rotate_button = _icon_button(self, checkable=True)
        self._rotate_button.setToolTip(
            "Manual rotation tool. Arrow keys adjust angle: default 0.1 deg, Ctrl = 1 deg, Shift = 5 deg."
        )
        self._rotate_button.toggled.connect(self._on_rotate_toggled)

        self._auto_rotate_button = _icon_button(self, checkable=False)
        self._auto_rotate_button.setToolTip(
            "Auto rotate: find the spot array (as ROIs > Array > Detect does, on the reference wavelength) and "
            "rotate the image by its tilt, however small, so the rows run level. No ROIs are placed; existing "
            "ROIs follow the rotation."
        )
        self._auto_rotate_button.setIcon(_auto_rotate_icon(get_active_theme().text_primary))
        self._auto_rotate_button.clicked.connect(self.auto_rotate_requested)

        self._reset_button = _icon_button(self, checkable=False)
        self._reset_button.setToolTip("Reset image rotation to 0 degrees.")
        self._reset_button.clicked.connect(self._on_reset_clicked)

        self._crop_button = _icon_button(self, checkable=True)
        self._crop_button.setToolTip("Crop tool. Drag a box on the image to crop it.")
        self._crop_button.toggled.connect(self._on_crop_toggled)

        self._auto_crop_button = _icon_button(self, checkable=False)
        self._auto_crop_button.clicked.connect(self._on_auto_crop_clicked)
        # Rebuilt from the state the owner pushes in (`set_auto_crop_state`); holds only the missing steps.
        self._auto_crop_menu = QMenu(self)
        self._auto_crop_state = _AutoCropState(None, False, False, "")
        self._refresh_auto_crop()

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

        # Every cluster is now a captioned group (2026-10-02, maintainer
        # request: "rotation section containing 3 rotation icons, and flip
        # section containing flipping icons, and crop section containing 2
        # crop icons" - "Calibrate" already was one, from the previous
        # 2026-10-02 session). `_cluster` wraps each cluster's buttons in a
        # plain row before handing it to `labeled_icon_group`, since that
        # takes one content widget, not several.
        rotation_group, self._rotation_label = labeled_icon_group(
            self, _cluster(self, self._rotate_button, self._auto_rotate_button, self._reset_button), "Rotation"
        )
        flip_group, self._flip_label = labeled_icon_group(
            self, _cluster(self, self._flip_h_button, self._flip_v_button), "Flip"
        )
        crop_group, self._crop_label = labeled_icon_group(
            self, _cluster(self, self._crop_button, self._auto_crop_button, self._reset_crop_button), "Crop"
        )
        calibrate_group, self._calibrate_label = labeled_icon_group(self, self._measure_button, "Calibrate")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_GROUP_GAP)
        layout.addWidget(rotation_group)
        layout.addWidget(ribbon_separator(self))
        layout.addWidget(flip_group)
        layout.addWidget(ribbon_separator(self))
        layout.addWidget(crop_group)
        layout.addWidget(ribbon_separator(self))
        layout.addWidget(calibrate_group)
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

    # -- Auto crop: dependency state, tooltip checklist, pick-up menu ---------

    def set_auto_crop_state(
        self, *, levelled: bool | None, rois: bool, chromatic: bool, level_note: str = ""
    ) -> None:
        """What the owner measured (`ArrayActions.auto_crop_readiness`): whether the array is level, whether there
        are ROIs, and whether the chromatic correction is fitted. ``level_note`` is that row's reason in words
        (the measured tilt, or why there is none) - this widget only renders what it is given."""
        state = _AutoCropState(levelled, bool(rois), bool(chromatic), level_note)
        if state == self._auto_crop_state:
            return
        self._auto_crop_state = state
        self._refresh_auto_crop()

    def _auto_crop_met(self) -> dict[str, bool]:
        state = self._auto_crop_state
        return {"level": bool(state.levelled), "rois": state.rois, "chromatic": state.chromatic}

    def _refresh_auto_crop(self) -> None:
        """Green when every dependency is met (a click crops), amber otherwise (a click opens the menu)."""
        met = self._auto_crop_met()
        ready = all(met.values())
        color = get_active_theme().text_primary
        self._auto_crop_button.setIcon(
            _auto_crop_icon(_AUTO_CROP_READY_COLOR if ready else _AUTO_CROP_WAITING_COLOR)
        )
        self._auto_crop_button.setToolTip(self._auto_crop_tooltip(met, ready))
        if self._auto_crop_menu.isVisible():
            self._auto_crop_menu.hide()  # never clear the actions out from under an open popup
        self._auto_crop_menu.clear()
        for key, label, where, icon in _AUTO_CROP_STEPS:
            if met[key]:
                continue
            action = self._auto_crop_menu.addAction(f"{label}  ({where})")
            action.setIcon(
                _auto_rotate_icon(color) if icon is None
                else load_tabler_icon(icon, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
            )
            # `triggered` carries a `checked` flag these signals do not take, so both slots drop it explicitly.
            if key == "level":
                action.triggered.connect(lambda _checked=False: self.auto_rotate_requested.emit())
            else:
                action.triggered.connect(lambda _checked=False, k=key: self.navigate_requested.emit(k))

    def _auto_crop_tooltip(self, met: dict[str, bool], ready: bool) -> str:
        note = self._auto_crop_state.level_note
        lines = [
            "Auto crop: crop to the ROI array - half a lattice constant of border beyond the outermost ROI "
            "centres, so an export chunk size of one pitch (or a multiple of it) lines up with the array.",
            "",
        ]
        for key, label, where, _icon in _AUTO_CROP_STEPS:
            mark = "✓" if met[key] else "✗"
            detail = f" - {note}" if key == "level" and note else ""
            lines.append(f"  {mark} {label}{detail}" if met[key] else f"  {mark} {label}{detail}  ->  {where}")
        lines.append("")
        lines.append("Click to crop." if ready else "Click to see what is still missing.")
        return "\n".join(lines)

    def _on_auto_crop_clicked(self) -> None:
        if all(self._auto_crop_met().values()):
            self.auto_crop_requested.emit()
            return
        # Amber: guide instead of cropping on half the information (maintainer's choice, 2026-10-21).
        self._auto_crop_menu.popup(self._auto_crop_button.mapToGlobal(self._auto_crop_button.rect().bottomLeft()))

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
        """Re-sync the reset buttons from the module - covers undo/
        redo and session restore, not just this widget's own clicks."""
        settings = self._geometry_module.settings()
        theme = get_active_theme()

        # Nothing to reset at 0 deg; dimming the button says so, and
        # `set_rotation` would be a no-op there anyway.
        at_zero = settings.rotation_angle_deg == 0.0
        self._reset_button.setEnabled(not at_zero)
        self._reset_button.setIcon(_reset_rotation_icon(theme.text_dim if at_zero else theme.text_primary))

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
