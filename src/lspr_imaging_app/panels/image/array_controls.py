"""Image panel, ROIs tab, "Array" group: find, refine or place a regular array of disk + ring ROIs.

Display only (CLAUDE.md, `panels/`): this widget holds the controls and turns clicks into signals; the
panel starts `roi/array_task.ArrayAction`, and `RoiToolbox.place_array` / `refine_rois` store the result.
Design record: `docs/roi_array_section_audit_2026-10-08.md`.

Row (the ribbon is a fixed-height row): mode (Auto / Semi / Manual), Run, Refine, gear (settings menu).
- **Auto**: nothing to enter. **Semi**: any of rows, columns, disk diameter, pitch typed in the settings
  menu is checked against the result (0 = "auto" = not given). **Manual**: everything typed (rows,
  columns, disk diameter, pitch, rotation, anchor); "Snap to image" then moves every node onto its spot.
- **Refine** re-fits position and size of the selected ROIs.

All lengths are diameters / pitches. Length fields show px or um (one app-wide unit, the same as the scale
bar's toggle: the small text button behind each field) and always keep the value in **pixels**,
resolution 0.1 px. Every control is remembered across restarts (`bind_ui_state`).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...image_tools.geometry.module import GeometryModule
from ...roi.array_detection import ArrayPrior
from ...roi.array_pipeline import ArraySettings
from ...roi.edge_size import EdgeSizeParams
from ...roi.ring_size import RingParams
from ...storage.ui_state_keys import ARRAY_KEYS, read
from ..ui_state import UiStateStore
from .general_group import ICON_SIZE, style_general_icon_button

MODES = (("auto", "Auto"), ("semi", "Semi"), ("manual", "Manual"))
_MODE_TIPS = {
    "auto": "Auto: find the array, its spacing, the disk size and the rings from the image alone.",
    "semi": "Semi: like Auto, but values typed in the settings menu (rows, columns, diameter, pitch) are checked against the result.",
    "manual": "Manual: place the array from the numbers typed in the settings menu (optionally snapping to the image).",
}
_EDGE_LABELS = {
    "plateau_fraction": "Plateau, fraction of contrast",
    "plateau_sigma": "Plateau, noise sigmas",
    "plateau_combined": "Plateau, larger of both",
    "half_max": "Half maximum",
    "max_gradient": "Steepest edge",
}
_INNER_LABELS = {"measured": "Measured (clear of the disk)", "ratio": "Ratio of sample diameter"}
_THICKNESS_LABELS = {"equal_area": "Equal area to the disk", "thickness": "Fixed width", "outer_ratio": "Ratio of inner diameter"}
_SIZE_LABELS = {"uniform": "One value for the array", "individual": "Each ROI its own"}

OVERLAP_INFO = (
    "Reference rings may overlap each other. A ring never counts pixels of sample disks: the analysis removes "
    "every sample disk from every ring. Sample disks are masked by your mask only, never by a ring."
)


class LengthField(QWidget):
    """A length in pixels shown as px or um (the app-wide unit), edited with 0.1 px resolution.

    ``value_px()`` is always pixels; ``0`` means "not given" when ``auto_text`` is set."""

    value_changed = pyqtSignal(float)

    def __init__(self, geometry: GeometryModule, *, auto_text: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._px = 0.0
        self._updating = False
        self._spin = QDoubleSpinBox(self)
        self._spin.setRange(0.0, 1_000_000.0)
        self._spin.setKeyboardTracking(False)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        if auto_text is not None:
            self._spin.setSpecialValueText(auto_text)
        self._unit = QToolButton(self)
        self._unit.setCheckable(True)  # checked = micrometers
        self._unit.setAutoRaise(True)
        self._unit.setFixedWidth(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._spin, 1)
        layout.addWidget(self._unit)
        self._spin.valueChanged.connect(self._on_edited)
        self._unit.toggled.connect(self._on_unit_toggled)
        geometry.cosmetic_changed.connect(self._refresh)
        geometry.geometry_changed.connect(self._refresh)
        self._refresh()

    def value_px(self) -> float:
        return self._px

    def set_value_px(self, value: float) -> None:
        self._px = max(float(value), 0.0)
        self._refresh()

    def _factor(self) -> float:
        settings = self._geometry.settings()
        if settings.display_units == "um" and self._geometry.can_display_micrometers():
            return float(self._geometry.microns_per_pixel_scalar())
        return 1.0

    def _refresh(self, *_args: object) -> None:
        um = self._factor() != 1.0
        self._updating = True
        try:
            self._spin.setDecimals(2 if um else 1)
            self._spin.setSingleStep(0.1 * self._factor())
            self._spin.setValue(self._px * self._factor())
            self._unit.setChecked(um)
            self._unit.setText("µm" if um else "px")
            self._unit.setEnabled(self._geometry.can_display_micrometers())
            self._unit.setToolTip(
                ("Micrometers (click for pixels)" if um else "Pixels (click for micrometers)")
                if self._geometry.can_display_micrometers()
                else "Micrometers need a calibration (Image tools > Measure)"
            )
        finally:
            self._updating = False

    def _on_edited(self, shown: float) -> None:
        if self._updating:
            return
        self._px = float(shown) / self._factor()
        self.value_changed.emit(self._px)

    def _on_unit_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        try:
            self._geometry.set_display_units("um" if checked else "px")
        except ValueError:  # the calibration was lost meanwhile
            pass
        self._refresh()


class _Popover(QWidget):
    """The settings menu: every option of the Array tools. Holds widget state only."""

    changed = pyqtSignal()

    def __init__(self, geometry: GeometryModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        def section(title: str) -> QFormLayout:
            if layout.count():
                rule = QFrame(self)
                rule.setFrameShape(QFrame.Shape.HLine)
                layout.addWidget(rule)
            label = QLabel(title, self)
            label.setStyleSheet("font-weight: 600;")
            layout.addWidget(label)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(4)
            layout.addLayout(form)
            return form

        def combo(labels: dict[str, str], tip: str) -> QComboBox:
            box = QComboBox(self)
            for key, text in labels.items():
                box.addItem(text, key)
            box.setToolTip(tip)
            box.currentIndexChanged.connect(lambda _i: self.changed.emit())
            return box

        def spin(minimum: int, maximum: int, tip: str, special: str | None = None, suffix: str = "") -> QSpinBox:
            box = QSpinBox(self)
            box.setRange(minimum, maximum)
            if special:
                box.setSpecialValueText(special)
            box.setSuffix(suffix)
            box.setToolTip(tip)
            box.valueChanged.connect(lambda _v: self.changed.emit())
            return box

        def dspin(minimum: float, maximum: float, decimals: int, step: float, tip: str, suffix: str = "") -> QDoubleSpinBox:
            box = QDoubleSpinBox(self)
            box.setRange(minimum, maximum)
            box.setDecimals(decimals)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setToolTip(tip)
            box.valueChanged.connect(lambda _v: self.changed.emit())
            return box

        def length(tip: str, auto_text: str | None = None) -> LengthField:
            field = LengthField(geometry, auto_text=auto_text, parent=self)
            field.setToolTip(tip)
            field.value_changed.connect(lambda _v: self.changed.emit())
            return field

        def check(text: str, tip: str) -> QCheckBox:
            box = QCheckBox(text, self)
            box.setToolTip(tip)
            box.toggled.connect(lambda _c: self.changed.emit())
            return box

        form = section("Array (Semi: checked against the result · Manual: used as typed)")
        self.rows = spin(0, 1000, "Number of rows. 0 = not given.", "auto")
        self.cols = spin(0, 1000, "Number of columns. 0 = not given.", "auto")
        self.diameter = length("Sample disk diameter. Auto: measured from the image.", "auto")
        self.pitch_x = length("Centre-to-centre distance along a row. 0 = not given.", "auto")
        self.pitch_y = length("Centre-to-centre distance along a column. 0 = not given.", "auto")
        self.rotation = dspin(-90.0, 90.0, 2, 0.1, "Manual: rotation of the array (degrees, clockwise on screen).", " °")
        self.anchor_x = length("Manual: x of the first disk (row 1, column 1).")
        self.anchor_y = length("Manual: y of the first disk (row 1, column 1).")
        self.snap = check("Snap to the image", "Manual: move every node onto its spot and measure the sizes.")
        self.bright = check("Spots are brighter than the background", "Off: spots are darker than the background (the usual case).")
        form.addRow("Rows", self.rows)
        form.addRow("Columns", self.cols)
        form.addRow("Disk diameter", self.diameter)
        form.addRow("Pitch along row", self.pitch_x)
        form.addRow("Pitch along column", self.pitch_y)
        form.addRow("Rotation", self.rotation)
        form.addRow("First disk x", self.anchor_x)
        form.addRow("First disk y", self.anchor_y)
        form.addRow("", self.snap)
        form.addRow("", self.bright)

        form = section("Sample disk size")
        self.edge_model = combo(_EDGE_LABELS, "How the disk edge is defined on a blurred edge (to be compared; one will stay).")
        self.edge_fraction = dspin(1.0, 50.0, 1, 1.0, "Plateau models: pixels within this fraction of the contrast count as spot.", " %")
        self.edge_sigma = dspin(1.0, 10.0, 1, 0.5, "Plateau models: pixels within this many noise sigmas count as spot.", " σ")
        self.size_mode = combo(_SIZE_LABELS, "One value = the smallest measured (outliers ignored); or each ROI keeps its own.")
        form.addRow("Edge model", self.edge_model)
        form.addRow("Fraction", self.edge_fraction)
        form.addRow("Noise", self.edge_sigma)
        form.addRow("Sizes", self.size_mode)

        form = section("Reference ring")
        self.inner_mode = combo(_INNER_LABELS, "Inner diameter: measured so no disk pixel is inside the ring, or a ratio of the sample diameter.")
        self.ring_fraction = dspin(1.0, 50.0, 1, 1.0, "Measured: background is within this fraction of the contrast.", " %")
        self.ring_margin = length("Measured: extra gap added to the measured inner diameter.")
        self.inner_ratio = dspin(1.01, 5.0, 2, 0.05, "Ratio: inner diameter = ratio x sample diameter.", " ×")
        self.thickness_mode = combo(_THICKNESS_LABELS, "How the outer diameter follows from the inner one.")
        self.thickness = length("Fixed width: ring width (outer - inner) / 2.")
        self.outer_ratio = dspin(1.01, 5.0, 2, 0.05, "Ratio: outer diameter = ratio x inner diameter.", " ×")
        self.ring_size_mode = combo(_SIZE_LABELS, "One value = the robust maximum inner diameter (clear of every disk); or each ROI its own.")
        form.addRow("Inner diameter", self.inner_mode)
        form.addRow("Background within", self.ring_fraction)
        form.addRow("Extra gap", self.ring_margin)
        form.addRow("Inner ratio", self.inner_ratio)
        form.addRow("Outer diameter", self.thickness_mode)
        form.addRow("Ring width", self.thickness)
        form.addRow("Outer ratio", self.outer_ratio)
        form.addRow("Sizes", self.ring_size_mode)

        info = QLabel(OVERLAP_INFO, self)
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {get_active_theme().text_muted};")
        layout.addWidget(info)


class ArrayControls(QWidget):
    run_requested = pyqtSignal(str)  # the mode: "auto" | "semi" | "manual"
    refine_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self, geometry: GeometryModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._selected = 0
        self._mode = QComboBox(self)
        for key, text in MODES:
            self._mode.addItem(text, key)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._run = QToolButton(self)
        style_general_icon_button(self._run)
        self._run.clicked.connect(self._on_run_clicked)
        self._refine = QToolButton(self)
        style_general_icon_button(self._refine)
        self._refine.clicked.connect(self.refine_requested.emit)
        self._gear = QToolButton(self)
        style_general_icon_button(self._gear)
        self._gear.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._gear.setToolTip("Array settings")
        self._pop = _Popover(geometry, self)
        self._pop.changed.connect(self.changed.emit)
        menu = QMenu(self._gear)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self._pop)
        menu.addAction(action)
        self._gear.setMenu(menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for widget in (self._mode, self._run, self._refine, self._gear):
            layout.addWidget(widget)
        self.refresh_theme()
        self._on_mode_changed()

    # -- state ------------------------------------------------------------------------

    def mode(self) -> str:
        return str(self._mode.currentData())

    def popover(self) -> _Popover:
        return self._pop

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._refresh_buttons()

    def set_selection_count(self, count: int) -> None:
        self._selected = int(count)
        self._refresh_buttons()

    def set_anchor_px(self, x: float, y: float) -> None:
        self._pop.anchor_x.set_value_px(x)
        self._pop.anchor_y.set_value_px(y)

    def settings(self) -> ArraySettings:
        """The numbers as typed. In Auto mode the Array fields are ignored (no priors)."""
        p = self._pop
        manual_or_semi = self.mode() != "auto"
        prior = ArrayPrior(
            diameter_px=(p.diameter.value_px() or None) if manual_or_semi else None,
            rows=(p.rows.value() or None) if manual_or_semi else None,
            cols=(p.cols.value() or None) if manual_or_semi else None,
            pitch_x_px=(p.pitch_x.value_px() or None) if manual_or_semi else None,
            pitch_y_px=(p.pitch_y.value_px() or None) if manual_or_semi else None,
        )
        return ArraySettings(
            prior=prior,
            bright=p.bright.isChecked(),
            edge=EdgeSizeParams(
                model=str(p.edge_model.currentData()),
                fraction=p.edge_fraction.value() / 100.0,
                sigma_k=p.edge_sigma.value(),
            ),
            size_mode=str(p.size_mode.currentData()),
            ring=RingParams(
                inner_mode=str(p.inner_mode.currentData()),
                background_fraction=p.ring_fraction.value() / 100.0,
                margin_px=p.ring_margin.value_px(),
                inner_ratio=p.inner_ratio.value(),
                thickness_mode=str(p.thickness_mode.currentData()),
                thickness_px=p.thickness.value_px(),
                outer_ratio=p.outer_ratio.value(),
            ),
            ring_size_mode=str(p.ring_size_mode.currentData()),
            anchor_xy=(p.anchor_x.value_px(), p.anchor_y.value_px()),
            rotation_deg=p.rotation.value(),
            snap_to_image=p.snap.isChecked(),
        )

    def grid(self) -> tuple[int, int, float, float] | None:
        """(rows, columns, pitch along row, pitch along column) for a manual placement, or ``None`` if one is missing."""
        p = self._pop
        rows, cols, px, py = p.rows.value(), p.cols.value(), p.pitch_x.value_px(), p.pitch_y.value_px()
        if rows < 1 or cols < 1 or px <= 0 or py <= 0 or p.diameter.value_px() <= 0:
            return None
        return rows, cols, px, py

    # -- buttons ----------------------------------------------------------------------

    def _on_mode_changed(self) -> None:
        mode = self.mode()
        self._mode.setToolTip(_MODE_TIPS[mode])
        self._refresh_buttons()
        self.changed.emit()

    def _on_run_clicked(self) -> None:
        if self._running:
            self.cancel_requested.emit()
        else:
            self.run_requested.emit(self.mode())

    def _refresh_buttons(self) -> None:
        theme = get_active_theme()
        mode = self.mode()
        if self._running:
            self._run.setIcon(load_tabler_icon("x", color=theme.accent_blue, size=ICON_SIZE * 2, stroke_width=2.1))
            self._run.setToolTip("Cancel the running array action")
        elif mode == "manual":
            self._run.setIcon(load_tabler_icon("layout-grid", color=theme.accent_blue, size=ICON_SIZE * 2, stroke_width=2.1))
            self._run.setToolTip(
                "Place the array from the typed numbers (replaces the selected ROIs).\n"
                "Needs rows, columns, pitch and disk diameter (Array settings)."
            )
        else:
            self._run.setIcon(load_tabler_icon("wand", color=theme.accent_blue, size=ICON_SIZE * 2, stroke_width=2.1))
            self._run.setToolTip(
                "Find the array in the image at the reference wavelength, measure the sample disks and reference rings, "
                "and replace the selected ROIs with it (all ROIs stay if none are selected).\n" + OVERLAP_INFO
            )
        self._refine.setIcon(
            load_tabler_icon("refresh", color=theme.accent_blue if self._selected and not self._running else theme.text_dim, size=ICON_SIZE * 2, stroke_width=2.1)
        )
        self._refine.setEnabled(bool(self._selected) and not self._running)
        self._refine.setToolTip(
            "Refine the selected ROIs: move each onto its spot and re-measure the sample disk and the reference ring."
            if self._selected
            else "Refine: select the ROIs of an array first."
        )
        self._gear.setIcon(load_tabler_icon("settings", color=theme.text_dim, size=ICON_SIZE * 2, stroke_width=2.1))
        self._mode.setEnabled(not self._running)

    def refresh_theme(self) -> None:
        for button in (self._run, self._refine, self._gear):
            style_general_icon_button(button)
        self._refresh_buttons()

    # -- remembering -------------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put every control back as last left (read from `store`), then save each later change. Lengths are
        stored in pixels whatever unit is shown."""
        p = self._pop
        spec: dict[str, tuple[Callable[[], object], Callable[[object], None]]] = {
            "mode": (lambda: self.mode(), lambda v: self._mode.setCurrentIndex(max(self._mode.findData(v), 0))),
            "rows": (p.rows.value, lambda v: p.rows.setValue(int(v))),
            "cols": (p.cols.value, lambda v: p.cols.setValue(int(v))),
            "diameter": (p.diameter.value_px, lambda v: p.diameter.set_value_px(float(v))),
            "pitch_x": (p.pitch_x.value_px, lambda v: p.pitch_x.set_value_px(float(v))),
            "pitch_y": (p.pitch_y.value_px, lambda v: p.pitch_y.set_value_px(float(v))),
            "rotation": (p.rotation.value, lambda v: p.rotation.setValue(float(v))),
            "anchor_x": (p.anchor_x.value_px, lambda v: p.anchor_x.set_value_px(float(v))),
            "anchor_y": (p.anchor_y.value_px, lambda v: p.anchor_y.set_value_px(float(v))),
            "snap": (p.snap.isChecked, lambda v: p.snap.setChecked(bool(v))),
            "bright": (p.bright.isChecked, lambda v: p.bright.setChecked(bool(v))),
            "edge_model": (lambda: p.edge_model.currentData(), lambda v: p.edge_model.setCurrentIndex(max(p.edge_model.findData(v), 0))),
            "edge_fraction": (p.edge_fraction.value, lambda v: p.edge_fraction.setValue(float(v))),
            "edge_sigma": (p.edge_sigma.value, lambda v: p.edge_sigma.setValue(float(v))),
            "size_mode": (lambda: p.size_mode.currentData(), lambda v: p.size_mode.setCurrentIndex(max(p.size_mode.findData(v), 0))),
            "inner_mode": (lambda: p.inner_mode.currentData(), lambda v: p.inner_mode.setCurrentIndex(max(p.inner_mode.findData(v), 0))),
            "ring_fraction": (p.ring_fraction.value, lambda v: p.ring_fraction.setValue(float(v))),
            "ring_margin": (p.ring_margin.value_px, lambda v: p.ring_margin.set_value_px(float(v))),
            "inner_ratio": (p.inner_ratio.value, lambda v: p.inner_ratio.setValue(float(v))),
            "thickness_mode": (lambda: p.thickness_mode.currentData(), lambda v: p.thickness_mode.setCurrentIndex(max(p.thickness_mode.findData(v), 0))),
            "thickness": (p.thickness.value_px, lambda v: p.thickness.set_value_px(float(v))),
            "outer_ratio": (p.outer_ratio.value, lambda v: p.outer_ratio.setValue(float(v))),
            "ring_size_mode": (lambda: p.ring_size_mode.currentData(), lambda v: p.ring_size_mode.setCurrentIndex(max(p.ring_size_mode.findData(v), 0))),
        }
        assert set(spec) == set(ARRAY_KEYS), "every Array control needs a row in storage/ui_state_keys.py ARRAY_KEYS"
        for name, (_getter, setter) in spec.items():
            setter(read(store, ARRAY_KEYS[name]))
        self._pending_store = (store, {name: getter for name, (getter, _s) in spec.items()})
        self.changed.connect(self._save)  # only now: restoring must not save what it just read

    def _save(self) -> None:
        store, getters = getattr(self, "_pending_store", (None, {}))
        if store is None:
            return
        for name, getter in getters.items():
            store.set(ARRAY_KEYS[name].key, getter())
