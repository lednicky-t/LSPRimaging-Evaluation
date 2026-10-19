"""The "Scan" and "Auto mask" groups of the Mask tab (2026-10-17).

**Scan:** look through the dataset for dark, sharp objects that crossed the field while one
wavelength was captured (a particle, a hair, a bubble) and **mark** those frames with pink pointers on the Mask rows. It changes
no mask: turning pointers into masks is the separate "Auto mask" step. Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

Screen purpose: start a scan and set how it looks. Layout (one row of compact controls, captioned "Scan" by `mask_tab.py`):

    [scan] [settings v] [trash]

- **scan**: runs the scan on a worker thread (`TransientScanTask`); progress and Cancel are in the status bar's task indicator.
- **settings** (a popup, because the Mask ribbon row is already ~800 px wide; its tooltip always shows the current values):
  - *Sensitivity*: how deep an object must be, in noise sigma of its own frame (default 60: clean frames reach 44, the particles found
    so far are 100 and more).
  - *Check every n-th wavelength* (1/n): the neighbours of a hit are checked too, so n = 3 found the same events as 1 in half the time.
  - *Follow passages*: search the frames a passage predicts at a lower threshold (gap filling).
  - *Only the cubes in view*: scan the stretch the Cube slider is zoomed to; off = the whole dataset. A scan of a stretch replaces only
    the pointers inside it.
- **trash**: clear the list of pointers.

Display only (CLAUDE.md "panels/"): the controls hand their values to the task and the list; nothing is computed here. Every control
is remembered across restarts (`bind_ui_state`).

**Auto mask** (`MaskAutoControls`): the separate step that turns the pointers into masks. Screen purpose: make an individual mask at
every frame that has a particle pointer that is still standing (not dismissed), and nothing else. Layout: `[wand] [3 px]`.

- **wand**: for each such frame, the contour of its particle(s) grown by N px, merged with the mask already in effect there (the
  persistent mask warped into the frame, or the frame's own individual mask); all frames are one undo step (Ctrl+Z). Progress and Cancel
  are in the status bar. `global` pointers (a liquid front, a shadow) and `artefact` pointers are never masked.
- **px**: how far the contour is grown (the feathering). Remembered.
- The same step is offered for one cube or one frame in the right-click menu of a pointer (`MaskRows`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QMenu, QSpinBox, QToolButton, QWidget, QWidgetAction

from lspr_ui import make_compact_spinbox

from ...dataset import DatasetModule
from ...dataset import io as dataset_io
from ...thread_priority import lower_current_thread_priority
from ...image_tools import ChromaticModule, MaskModule
from ...image_tools.mask.transient_detection import DetectionParams
from ...image_tools.mask.transient_events import TransientEventsModule
from ...image_tools.mask.transient_events_state import EventRecord
from ...image_tools.mask.transient_masks import group_by_frame, maskable
from ...image_tools.mask.transient_scan import ScanParams
from ...image_tools.mask_auto_task import AutoMaskSummary, MaskAutoTask
from ...image_tools.mask_scan_task import ScanSummary, TransientScanTask
from ..mask_edit_common import action_button, disabled_safe_icon, plan_mask_edit_base
from ..ribbon_fields import style_ribbon_number_field
from .mask_rows import EVENT_COLOR, MASK_COLOR

_ICON_PLAIN = "#f8fafc"  # GuiTheme.text_primary, like the other real, active actions of this ribbon
_DEFAULT_SIGMA = DetectionParams().sigma_factor
_DEFAULT_STRIDE = ScanParams().stride


class _ScanPopover(QWidget):
    """The scan's settings, in the popup of the settings button. The widgets are the controls' own (`MaskScanControls` reads and
    remembers them); `changed` fires when any of them does."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sigma_spin = QSpinBox(self)
        self.sigma_spin.setRange(10, 500)
        self.sigma_spin.setValue(int(_DEFAULT_SIGMA))
        self.sigma_spin.setSuffix(" \u03c3")
        self.sigma_spin.setToolTip(
            "How deep an object must be, in noise sigma of its own frame. Clean frames reach 44 sigma at most; the particles found "
            "so far are 100 sigma and more. Lower finds fainter objects and more false alarms."
        )
        self.stride_spin = QSpinBox(self)
        self.stride_spin.setRange(1, 10)
        self.stride_spin.setValue(_DEFAULT_STRIDE)
        self.stride_spin.setPrefix("1/")
        self.stride_spin.setToolTip(
            "Check every n-th wavelength; the neighbours of a hit are checked too. 1/3 found the same events as 1/1 on the test "
            "dataset in about half the time. A passage lasts 3-15 frames, so a very large n can miss one."
        )
        self.follow_box = QCheckBox("Follow passages through faint frames", self)
        self.follow_box.setChecked(True)
        self.follow_box.setToolTip(
            "Where a particle is too faint for the threshold in some frames of its passage, look again at a weaker threshold, but only "
            "where the passage predicts it. The events it finds are marked as found by gap filling."
        )
        self.range_box = QCheckBox("Only the cubes in view", self)
        self.range_box.setToolTip(
            "Scan only the stretch the Cube slider is zoomed to (Ctrl + wheel over it). Off: the whole dataset. A scan of a stretch "
            "replaces only the pointers inside it."
        )
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(6)
        form.addRow("Sensitivity", self.sigma_spin)
        form.addRow("Check every", self.stride_spin)
        form.addRow(self.follow_box)
        form.addRow(self.range_box)
        self.sigma_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self.stride_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self.follow_box.toggled.connect(lambda _on: self.changed.emit())
        self.range_box.toggled.connect(lambda _on: self.changed.emit())


class MaskScanControls(QWidget):
    def __init__(
        self,
        dataset: DatasetModule,
        events: TransientEventsModule,
        scan: TransientScanTask,
        status: Callable[[str], None],
        parent: QWidget | None = None,
        *,
        visible_cubes: Callable[[], tuple[int, int] | None] | None = None,
    ) -> None:
        """``visible_cubes()``: the first and last cube number in view on the Cube slider, or ``None`` when it shows everything."""
        super().__init__(parent)
        self._dataset = dataset
        self._events = events
        self._scan = scan
        self._status = status
        self._visible_cubes = visible_cubes

        self._scan_button = action_button(
            self, "search", EVENT_COLOR,
            "Scan the dataset for dark objects that crossed the field (particles, hairs, bubbles). It only marks the frames with "
            "pink pointers on the Mask rows; it changes no mask. Progress and Cancel are in the status bar.",
        )
        self._scan_button.clicked.connect(self.start_scan)

        # The settings are in a popup: the Mask ribbon row is already ~800 px wide without them.
        self._pop = _ScanPopover(self)
        self._sigma_spin = self._pop.sigma_spin
        self._stride_spin = self._pop.stride_spin
        self._fill_button = self._pop.follow_box
        self._range_button = self._pop.range_box
        self._settings_button = QToolButton(self)
        self._settings_button.setAutoRaise(True)
        self._settings_button.setFixedSize(self._scan_button.size())
        self._settings_button.setIconSize(self._scan_button.iconSize())
        self._settings_button.setStyleSheet(self._scan_button.styleSheet())
        self._settings_button.setIcon(disabled_safe_icon("settings", _ICON_PLAIN))
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._settings_button)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self._pop)
        menu.addAction(action)
        self._settings_button.setMenu(menu)
        self._pop.changed.connect(self._refresh_summary)
        self._refresh_summary()

        self._clear_button = action_button(self, "trash", _ICON_PLAIN, "Clear all pointers of the scan (it changes no mask).")
        self._clear_button.clicked.connect(self._on_clear)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._scan_button)
        layout.addWidget(self._settings_button)
        layout.addWidget(self._clear_button)
        layout.addStretch(1)

        scan.running_changed.connect(lambda _running: self._refresh_enabled())
        scan.completed.connect(self._on_completed)
        scan.failed.connect(lambda message: self._status(f"Scan failed: {message}"))
        scan.cancelled.connect(lambda: self._status("Scan cancelled; the pointers were not changed."))
        events.events_changed.connect(lambda _change: self._refresh_enabled())
        dataset.dataset_loaded.connect(lambda _ds: self._refresh_enabled())
        dataset.dataset_cleared.connect(self._refresh_enabled)
        self._refresh_enabled()

    # -- remembered state ---------------------------------------------------------------------------------

    def bind_ui_state(self, store) -> None:
        """Remember the controls across restarts (`panels/ui_state.py`)."""
        store.bind("image/mask_scan/sigma", self._sigma_spin)
        store.bind("image/mask_scan/stride", self._stride_spin)
        store.bind("image/mask_scan/follow_passages", self._fill_button)
        store.bind("image/mask_scan/visible_only", self._range_button)

    # -- the controls --------------------------------------------------------------------------------------

    def scan_params(self) -> ScanParams:
        """The scan settings the controls hold now."""
        in_view = self._visible_cubes() if self._range_button.isChecked() and self._visible_cubes is not None else None
        return ScanParams(
            detection=DetectionParams(sigma_factor=float(self._sigma_spin.value())),
            stride=int(self._stride_spin.value()),
            fill_gaps=self._fill_button.isChecked(),
            cube_range=in_view,
        )

    def _refresh_summary(self) -> None:
        """The settings popup is closed most of the time: its button's tooltip always says what a scan would use."""
        self._settings_button.setToolTip(
            f"Scan settings: sensitivity {self._sigma_spin.value()} sigma, "
            + ("every wavelength" if self._stride_spin.value() == 1 else f"every {_ORDINALS[self._stride_spin.value()]} wavelength")
            + (", following passages through faint frames" if self._fill_button.isChecked() else "")
            + (", only the cubes in view" if self._range_button.isChecked() else ", the whole dataset")
            + ". Click to change."
        )

    def _refresh_enabled(self, *_args: object) -> None:
        loaded = bool(self._dataset.spectral_cubes())
        running = self._scan.is_running()
        self._scan_button.setEnabled(loaded and not running)
        self._settings_button.setEnabled(not running)
        for widget in (self._sigma_spin, self._stride_spin, self._fill_button, self._range_button):
            widget.setEnabled(not running)
        self._clear_button.setEnabled(self._events.has_events() and not running)
        self._scan_button.setToolTip(
            self._scan_button.toolTip().split("\n")[0]
            + ("" if loaded else "\nLoad a dataset first.")
            + ("\nA scan is running." if running else "")
        )

    def start_scan(self) -> None:
        """Run a scan with the settings shown (the button's action; also callable by tests)."""
        cubes = self._dataset.spectral_cubes()
        if not cubes:
            self._status("Load a dataset first: there is nothing to scan.")
            return
        if self._scan.is_running():
            return
        frame_index = {int(c): self._dataset.wavelengths_for_cube(c) for c in cubes}
        params = self.scan_params()
        self._scan.start(
            frame_index,
            self._dataset.load_plane_uncached,
            params,
            worker_init=_scan_worker_init,
        )
        where = "" if params.cube_range is None else f" in cubes {params.cube_range[0]}-{params.cube_range[1]}"
        self._status(f"Scanning{where} for dark objects that crossed the field... (progress and Cancel in the status bar)")

    def _on_clear(self) -> None:
        count = len(self._events.records())
        self._events.clear()
        self._status(f"{count} pointers cleared.")

    def _on_completed(self, summary: ScanSummary) -> None:
        self._status(
            f"Scan done: {summary.particles} particles in {summary.passages} passages, {summary.globals_} global events, "
            f"{summary.artefacts} artefacts ({summary.frames_screened} of {summary.frames_in_range} frames compared, "
            f"{summary.seconds:.0f} s). Pink ticks on the Mask rows mark them; nothing was masked."
        )


def _scan_worker_init() -> None:
    """Runs once in every worker thread of the scan: the fast TIFF decoder, and below-normal priority so the window keeps its turn
    for a core while twelve threads read and screen frames (`thread_priority.py`)."""
    dataset_io.use_fast_tiff_decoder(True)
    lower_current_thread_priority()


_DEFAULT_DILATE_PX = 3
_ORDINALS = {n: f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'}" for n in range(1, 11)}


class MaskAutoControls(QWidget):
    def __init__(
        self,
        dataset: DatasetModule,
        mask: MaskModule,
        chromatic: ChromaticModule,
        events: TransientEventsModule,
        task: MaskAutoTask,
        scan: TransientScanTask,
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._mask = mask
        self._chromatic = chromatic
        self._events = events
        self._task = task
        self._scan = scan
        self._status = status

        self._apply_button = action_button(self, "wand", MASK_COLOR, "")
        self._apply_button.clicked.connect(lambda: self.start())

        self._dilate_spin = make_compact_spinbox(QSpinBox(self))
        self._dilate_spin.setRange(0, 50)
        self._dilate_spin.setValue(_DEFAULT_DILATE_PX)
        self._dilate_spin.setSuffix(" px")
        self._dilate_spin.setToolTip(
            "How far the contour of a particle is grown before it is masked (the feathering). The contour itself is the half-depth "
            "edge the scan found: halfway down the particle's own dip."
        )
        style_ribbon_number_field(self._dilate_spin, "99 px")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._apply_button)
        layout.addWidget(self._dilate_spin)
        layout.addStretch(1)

        task.running_changed.connect(lambda _running: self._refresh_enabled())
        scan.running_changed.connect(lambda _running: self._refresh_enabled())
        task.completed.connect(self._on_completed)
        task.failed.connect(lambda message: self._status(message))
        task.cancelled.connect(lambda: self._status("Auto mask cancelled; no mask was changed."))
        events.events_changed.connect(lambda _change: self._refresh_enabled())
        dataset.dataset_loaded.connect(lambda _ds: self._refresh_enabled())
        dataset.dataset_cleared.connect(self._refresh_enabled)
        self._refresh_enabled()

    def bind_ui_state(self, store) -> None:
        """Remember the feathering across restarts (`panels/ui_state.py`)."""
        store.bind("image/mask_auto/dilate_px", self._dilate_spin)

    def dilate_px(self) -> int:
        return int(self._dilate_spin.value())

    def _refresh_enabled(self, *_args: object) -> None:
        records = maskable(self._events.records())
        frames = len(group_by_frame(records))
        busy = self._task.is_running() or self._scan.is_running()
        loaded = self._dataset.raw_plane_shape() is not None
        self._apply_button.setEnabled(bool(records) and loaded and not busy)
        self._dilate_spin.setEnabled(not self._task.is_running())
        if not records:
            tip = "Nothing to mask yet: scan first (the Scan group), or no particle pointer is left standing."
        else:
            tip = (
                f"Make individual masks from the {len(records)} particle pointers that are standing ({frames} frames): each contour "
                "grown by the px box, merged with the mask already in effect there. Global and artefact pointers are never masked. "
                "One undo step (Ctrl+Z); progress and Cancel are in the status bar."
            )
        if busy:
            tip += " A scan or an Auto mask run is going on."
        self._apply_button.setToolTip(tip)

    def start(self, records: Sequence[EventRecord] | None = None) -> None:
        """Make the masks of `records` (default: every pointer of the list) with the feathering shown (the wand's action; the
        pointer menus call it for one cube or one frame)."""
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            self._status("Load a dataset first: there is nothing to mask.")
            return
        if self._task.is_running() or self._scan.is_running():
            return
        chosen = list(self._events.records() if records is None else records)
        self._task.start(
            chosen, raw_shape, self.dilate_px(),
            lambda frame: plan_mask_edit_base(self._mask, self._chromatic, frame),
        )

    def _on_completed(self, summary: AutoMaskSummary) -> None:
        text = (
            f"Auto mask: {summary.frames_masked} masks from {summary.events_used} particle events "
            f"(+{summary.dilate_px} px), merged with the mask in effect. Ctrl+Z undoes all of them."
        )
        if summary.frames_unchanged:
            text += f" {summary.frames_unchanged} frames already had these masks."
        self._status(text)
