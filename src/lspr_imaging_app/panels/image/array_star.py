"""Array* (2026-11-25): build a regular array from the selected ROI(s), with a live preview on the canvas.

Screen purpose: the ROIs tab's "Array*" section is one button. It opens a panel (`ArrayStarPanel`) that sits **inside the app window**, over the image, just under
the button, like a context menu that stays open (it is a child widget of the Image panel, not a separate window, since 2026-11-25). While it is open the copies the
settings would make are drawn as ghost outlines on the image, redrawn at every change; **Apply** makes them (`RoiToolbox.stamp_array`, one undo step) and **Cancel**
or the Array* button closes it and makes nothing. It does not block the image: pan, zoom, change the wavelength or the selection and the preview follows.

This file wires the pieces; the maths is `roi/array_layout.py`, the ROI command is `RoiToolbox.stamp_array`, the drawing of the ghosts is `array_star_ghosts.py`.
No ROI state lives here. Placing the nodes is a few array operations, so it runs on the GUI thread, once per ~30 ms at most (changes are coalesced).

Why a new section beside the old Array group: the old one (`array_controls.py`) finds arrays in the image; this one stamps them from numbers. Both stay until
the maintainer decides which to keep.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...image_tools.chromatic.module import ChromaticModule
from ...image_tools.geometry.module import GeometryModule
from ...roi import RoiToolbox
from ...roi.array_layout import ArraySpec, array_layout
from ...selection import SelectionModule
from ...storage.ui_state_keys import ARRAY_STAR_KEYS, read
from ..ui_state import UiStateStore
from .array_star_ghosts import ghost_outlines, preview_alphas
from .array_star_panel import ArrayStarPanel
from .array_star_pictograms import pictogram_icon
from .general_group import style_general_icon_button
from .sample_circles_item import SampleCirclesItem

GHOST_Z = 25
"""Above the ROI circles (0) and the tuning crosses (30 is above this: crosses stay readable over ghosts)."""
GHOST_WIDTH = 1.5
PROBLEM_COLOR = "#f59e0b"
DEBOUNCE_MS = 30


class PopupFrame(QFrame):
    """The frame the panel sits in: the window colour (what the fields have) and a thin border, painted here so no style-sheet cascade can change it."""

    def paintEvent(self, event) -> None:
        theme = get_active_theme()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.window_bg))
        painter.setPen(QColor(theme.control_border))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class ArrayStarController(QObject):
    status = pyqtSignal(str)  # one line for the status bar
    opened_changed = pyqtSignal(bool)

    def __init__(
        self,
        *,
        plot: pg.PlotItem,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        chromatic: ChromaticModule,
        geometry: GeometryModule,
        current_frame: Callable[[], tuple[int, float]],
        host: QWidget,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._toolbox = toolbox
        self._selection = selection
        self._chromatic = chromatic
        self._current_frame = current_frame
        self._store: UiStateStore | None = None

        self.button = QToolButton(host)
        self.button.setCheckable(True)
        self.button.setText("Array*")
        self.button.setToolTip(
            "Array*: repeat the selected ROI(s) as a regular array (rectangular or triangular), with a live preview on the image. "
            "Select the ROI(s) first; Apply makes the copies as one undo step."
        )
        style_general_icon_button(self.button)
        self.button.toggled.connect(self._on_button_toggled)

        self._host = host
        self._open = False
        self.popup = PopupFrame(host)  # a child of the Image panel: in the app window, over the image, not a window of its own
        self.popup.setObjectName("ArrayStarPopup")
        self.popup.hide()
        self.panel = ArrayStarPanel(geometry, self.popup)
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        host.installEventFilter(self)  # keep the panel inside the window when it is resized
        self.panel.cancel_requested.connect(self.close)
        self.panel.apply_requested.connect(self.apply)
        self.panel.spec_changed.connect(self._on_spec_changed)
        self.panel.advanced_toggled.connect(lambda _opened: self._fit_popup())
        self.panel.opacity_changed.connect(self._on_opacity)

        self._ghosts = SampleCirclesItem(GHOST_WIDTH)
        self._ghosts.setZValue(GHOST_Z)
        self._apply_opacity(self.panel.opacity())
        plot.addItem(self._ghosts)
        self._ghosts.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._recompute)
        selection.roi_selection_changed.connect(self._schedule)
        toolbox.geometry_changed.connect(self._schedule)
        self._seed_count = 0
        self._problem = True
        self.refresh_theme()

    # -- open / close -------------------------------------------------------------------------------------------------

    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        if not self._open:
            self._open = True
            self.popup.show()
        self._fit_popup()  # (not just `_position`: the first size must come from an up-to-date layout, or the rows are squeezed together)
        self.popup.raise_()
        self._set_button_checked(True)
        self._ghosts.setVisible(True)
        self._recompute()
        self.opened_changed.emit(True)

    def close(self) -> None:
        """Close without making anything (Cancel, or the Array* button again)."""
        if not self._open:
            return
        self._open = False
        self._timer.stop()
        self.popup.hide()
        self._ghosts.clear()
        self._ghosts.setVisible(False)
        self._set_button_checked(False)
        self.opened_changed.emit(False)

    def _on_opacity(self, opacity: float) -> None:
        """The wedge on the panel's top row: how strongly the preview of the new ROIs is drawn on the image."""
        self._apply_opacity(opacity)
        self._save()

    def _apply_opacity(self, opacity: float) -> None:
        """The wedge position (0 - 1, the middle = the usual look) as the ghosts' outline and fill opacity (`preview_alphas`)."""
        self._ghosts.set_style(*preview_alphas(opacity))

    def opacity(self) -> float:
        """The wedge position now (0 - 1; 0.5 = the usual look)."""
        return self.panel.opacity()

    def _fit_popup(self) -> None:
        """Grow or shrink the panel to its content (opening Advanced adds rows) and keep it where it belongs."""
        # Qt re-lays-out a widget in a later event; the size is needed now, so update the inner layout first, then the frame's.
        for layout in (self.panel.layout(), self.popup.layout()):
            if layout is not None:
                layout.invalidate()
                layout.activate()
        self._position()
        QTimer.singleShot(0, self._settle_popup)  # again once the event loop has polished the widgets (fonts and paddings change the row heights)

    def _settle_popup(self) -> None:
        if self._open:
            for layout in (self.panel.layout(), self.popup.layout()):
                if layout is not None:
                    layout.invalidate()
                    layout.activate()
            self._position()

    def _position(self) -> None:
        """Just under the Array* button, kept inside the Image panel."""
        self.popup.adjustSize()
        spot = self.button.mapTo(self._host, QPoint(0, self.button.height() + 4))
        x = max(4, min(spot.x(), self._host.width() - self.popup.width() - 4))
        self.popup.move(x, spot.y())

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        # `getattr`: Qt can still deliver an event while Python is tearing this object down (a host closing at the end of a test).
        if getattr(self, "_open", False) and watched is self._host and event is not None and event.type() == QEvent.Type.Resize:
            self._position()
        return False

    def _on_button_toggled(self, checked: bool) -> None:
        if checked:
            self.open()
        else:
            self.close()

    def _set_button_checked(self, checked: bool) -> None:
        blocked = self.button.blockSignals(True)
        self.button.setChecked(checked)
        self.button.blockSignals(blocked)

    # -- the preview --------------------------------------------------------------------------------------------------

    def on_frame(self) -> None:
        """The frame on screen changed (wavelength, cube, chromatic shift): redraw the ghosts there."""
        if self.is_open():
            self._schedule()

    def _on_spec_changed(self) -> None:
        self._save()
        self._schedule()

    def _schedule(self, *_args: object) -> None:
        if self.is_open():
            self._timer.start()

    def ghost_count(self) -> int:
        """How many ghost outlines are drawn (tests)."""
        return self._ghosts.circle_count()

    def _recompute(self) -> None:
        self._timer.stop()
        if not self.is_open():
            return
        ids = sorted(self._selection.selected_roi_ids())
        spec = self.panel.spec()
        self._seed_count = len(ids)
        if not ids:
            self._show_problem("Select the ROI(s) to repeat first (click them on the image or in the table).", quiet=True)
            return
        taken = [roi for roi in self._toolbox.rois() if roi.area_roi_id in ids and roi.array_id]
        if taken:
            self._show_problem(f"ROI {taken[0].area_roi_id} already belongs to {taken[0].array_id}: select ROIs that are not in an array.")
            return
        frame = self._current_frame()
        try:
            clip = self._toolbox.copy_rois(ids, cube=frame[0])
            layout = array_layout(spec, [(item.geometry.center_x, item.geometry.center_y) for item in clip.rois])
        except (ValueError, KeyError) as error:
            self._show_problem(str(error).strip("'\""))
            return
        xs, ys = ghost_outlines(clip, layout, np.asarray(self._chromatic.affine_for(frame), dtype=np.float64))
        self._ghosts.set_circles(xs, ys, [get_active_theme().accent_blue] * len(xs))
        total = layout.count * len(clip)
        self.panel.set_count(f"{layout.rows} × {layout.cols}" + (f" × {len(clip)} ROIs" if len(clip) > 1 else "") + f" = {total} ROIs ({total - len(clip)} new)")
        self.panel.set_message("", problem=False)
        self.panel.set_apply_enabled(total > len(clip))
        self._problem = False

    def _show_problem(self, text: str, *, quiet: bool = False) -> None:
        """Nothing can be made right now: no ghosts (a stale picture would mislead), the reason in the pop-out, Apply off."""
        self._ghosts.clear()
        self.panel.set_message(text, problem=not quiet)
        self.panel.set_count("")
        self.panel.set_apply_enabled(False)
        self._problem = True

    # -- apply --------------------------------------------------------------------------------------------------------

    def apply(self) -> None:
        """Make the array (one undo step), say what was made, close the window."""
        self._recompute()  # what is made is what the last change showed, never an older picture
        if self._problem:
            self.status.emit(f"Array*: nothing made. {self.panel.message()}".strip())
            return
        ids = sorted(self._selection.selected_roi_ids())
        spec = self.panel.spec()
        try:
            new = self._toolbox.stamp_array(ids, spec, cube=self._current_frame()[0])
        except (ValueError, KeyError) as error:
            text = str(error).strip("'\"")
            self.panel.set_message(text, problem=True)
            self.status.emit(f"Array*: nothing made. {text}")
            return
        self.status.emit(f"Array*: added {len(new)} ROIs next to the {len(ids)} selected (Ctrl+Z undoes it).")
        self.close()

    # -- remembering --------------------------------------------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put the settings back as last left, then save each later change."""
        values = {name: read(store, key) for name, key in ARRAY_STAR_KEYS.items()}
        try:
            spec = ArraySpec(
                kind=str(values["kind"]), rows=int(values["rows"]), cols=int(values["cols"]),  # type: ignore[arg-type]
                pitch_x_px=float(values["pitch_x"]), pitch_y_px=float(values["pitch_y"]),  # type: ignore[arg-type]
                start_x=float(values["start_x"]), start_y=float(values["start_y"]), angle_deg=float(values["angle"]),  # type: ignore[arg-type]
                pitch_factor_x=float(values["step_x"]), pitch_factor_y=float(values["step_y"]),  # type: ignore[arg-type]
                row_offset=float(values["row_offset"]), regular_cell=bool(values["regular"]),  # type: ignore[arg-type]
                number_by=str(values["number_by"]), number_start_x=float(values["number_x"]), number_start_y=float(values["number_y"]),  # type: ignore[arg-type]
            )
            self.panel.set_spec(spec)
        except ValueError:
            pass  # an unknown lattice type or start point in the file: keep the defaults
        self.panel.set_advanced_open(bool(values["advanced"]))
        self.panel.set_opacity(float(values["opacity"]))  # type: ignore[arg-type]
        self._apply_opacity(self.panel.opacity())
        self.panel.advanced_toggled.connect(lambda _opened: self._save())
        self._store = store

    def _save(self) -> None:
        if self._store is None:
            return
        spec = self.panel.spec()
        values = {
            "kind": spec.kind, "rows": spec.rows, "cols": spec.cols, "pitch_x": spec.pitch_x_px, "pitch_y": spec.pitch_y_px,
            "start_x": spec.start_x, "start_y": spec.start_y, "angle": spec.angle_deg, "step_x": spec.pitch_factor_x,
            "step_y": spec.pitch_factor_y, "row_offset": spec.row_offset, "regular": spec.regular_cell,
            "advanced": self.panel.is_advanced_open(), "number_by": spec.number_by, "number_x": spec.number_start_x,
            "number_y": spec.number_start_y, "opacity": self.panel.opacity(),
        }
        for name, value in values.items():
            self._store.set(ARRAY_STAR_KEYS[name].key, value)

    # -- look ---------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        style_general_icon_button(self.button)
        self.button.setIcon(pictogram_icon("type_rectangular"))
        self.popup.update()
        self.panel.refresh_theme()
