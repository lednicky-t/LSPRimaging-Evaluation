"""The Mask tab's "Feature map" section: `FeatureMapEditPanel` (`mask_tab.py`).

**History.** The section was "Manual edit" (a tool picker over per-tool panels). 2026-11-15: the Histogram selection, Threshold, Local contrast,
Morphology and Draw panels moved into the selection toolbar beside the image (they make or modify a *selection* now, and the Mask tab's
Selection group turns a selection into mask pixels: `panels/image/selection_by_value.py`, `selection_modify.py`, `brush_settings.py`,
`mask_selection_editor.py`; record `docs/selection_lock_and_tool_move_2026-11-15.md`). 2026-11-16: the Eraser went too (maintainer's
request; a wand click plus the Selection group's Subtract does a similar job), the picker with it, so the Feature map is the one tool left and the section carries its name.

**Feature map** (2026-11-04, replaced the ROI features tool of 2026-10-29): not a live preview but a Process button
(~20-30 s, worker processes, progress and Cancel in the status bar), a settings popup with the search limits and the cubes, then
(2026-11-05) a kinds popup (one check box per kind) and a margin field that act on the finished map at once. No ROIs: the result stays
right when ROIs move.
**2026-11-23 (maintainer: "simplification / unification"): the map is a *selection*, not a mask.** When a run ends, the pixels of the enabled
kinds become a new selection (Replace; undoable, cut to the lock) and the Selection group's Add puts them in the mask, like any other selection.
So the tool has no eye (the selection's own outline is the picture), no Add button and no preview of its own. Changing the kinds or the margin
afterwards re-makes that selection, but only while it is still the one the tool made (any other change of the selection, an undo included,
ends that: the user's edit is never overwritten).
Record: `docs/roi_feature_detection_2026-10-29.md` sections 10-11 and `docs/feature_map_as_selection_2026-11-23.md`.

Every panel keeps this ribbon's established sizing: 28px-tall icon buttons/spinboxes, `transparent_icon_button_stylesheet()` chrome, no
inline text labels (tooltips only) - these panels live inside `ImageToolRibbon`'s fixed-height content row.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...image_tools import GeometryModule
from ...image_tools.mask import feature_map as fm
from ...image_tools.preprocess import resolve_external_mask
from ...selection import AreaSelectionModule
from ...selection.area_selection_module import REPLACE
from ..feature_map_task import DEFAULT_CUBES, FeatureMapResult, FeatureMapSummary, FeatureMapTask, choose_cubes
from ..mask_edit_common import action_button, disabled_safe_icon
from ..ribbon_fields import style_ribbon_number_field

SELECTION_LABEL = "Select feature map"  # the undo step's name

if TYPE_CHECKING:
    # Deferred (type checking only): a plain top-level import here would
    # close a real import cycle.
    from .panel import ImagePanel

_PLAIN_ICON_COLOR = "#f8fafc"  # GuiTheme.text_primary, like the other real, active actions of this ribbon
_STOP_COLOR = "#ef4444"  # the red stop of the Analysis, Tuning and Export tabs' Run / Stop buttons


class _FeatureMapPopover(QWidget):
    """The map's **search** settings, in the popup of the settings button (the ribbon row has no room): what decides which features are
    found. The widgets are the panel's own (it reads and remembers them). `numbers_changed` fires for a number (they need Process
    again: ~2 s with the kept slow part, a full run for another cube count). What only picks or grows the features already found, the
    kinds and the margin, is not here: those are on the ribbon row and act on the map at once (2026-11-05)."""

    numbers_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dirt_spin = QDoubleSpinBox(self)
        self.dirt_spin.setRange(0.5, 30.0)
        self.dirt_spin.setDecimals(1)
        self.dirt_spin.setSingleStep(0.5)
        self.dirt_spin.setSuffix(" %")
        self.dirt_spin.setToolTip("Dirt must take (or add) at least this share of the light. Lower finds fainter dirt.")
        self.structure_spin = QSpinBox(self)
        self.structure_spin.setRange(2, 90)
        self.structure_spin.setSuffix(" %")
        self.structure_spin.setToolTip(
            "Nanostructure must be missing (or extra) by at least this share of a disk's normal plasmon contrast. A notch is typically 50-70 %."
        )
        self.certainty_spin = QDoubleSpinBox(self)
        self.certainty_spin.setRange(3.0, 15.0)
        self.certainty_spin.setDecimals(1)
        self.certainty_spin.setSingleStep(0.5)
        self.certainty_spin.setToolTip(
            "How clearly a feature must stand out of the pixel-to-pixel scatter at its place (a score; 5 is clearly). Lower finds "
            "more, and more false alarms."
        )
        self.edge_spin = QSpinBox(self)
        self.edge_spin.setRange(0, 90)
        self.edge_spin.setSuffix(" %")
        self.edge_spin.setToolTip(
            "Where a feature ends: where it is still at least this share of its own strongest pixels. A dense particle in a faint "
            "shadow is then the particle, not the shadow. 0 lets features spread down to 60 % of the limits."
        )
        self.cubes_spin = QSpinBox(self)
        self.cubes_spin.setRange(1, 200)
        self.cubes_spin.setToolTip(
            "How many cubes, from the one on screen on, are averaged before looking (the features are static; more cubes, less noise, "
            "a longer first run: ~1 s per cube for reading)."
        )
        self.reset_button = QPushButton("Defaults", self)
        self.reset_button.setToolTip("Put the numbers, the kinds and the margin back to their starting values.")
        for spin in (self.dirt_spin, self.structure_spin, self.certainty_spin, self.edge_spin, self.cubes_spin):
            spin.setKeyboardTracking(False)
        self._set_defaults()
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(6)
        form.addRow("Dirt from", self.dirt_spin)
        form.addRow("Nanostructure from", self.structure_spin)
        form.addRow("Certainty", self.certainty_spin)
        form.addRow("Feature edge", self.edge_spin)
        form.addRow("Cubes to average", self.cubes_spin)
        form.addRow(self.reset_button)
        for spin in (self.dirt_spin, self.structure_spin, self.certainty_spin, self.edge_spin, self.cubes_spin):
            spin.valueChanged.connect(lambda _v: self.numbers_changed.emit())
        self.reset_button.clicked.connect(self._set_defaults)

    def _set_defaults(self) -> None:
        defaults = fm.FeatureMapSettings()
        self.dirt_spin.setValue(defaults.dirt_min * 100.0)
        self.structure_spin.setValue(int(round(defaults.structure_min * 100.0)))
        self.certainty_spin.setValue(defaults.certainty)
        self.edge_spin.setValue(int(round(defaults.core_share * 100.0)))
        self.cubes_spin.setValue(DEFAULT_CUBES)

    def settings(self) -> fm.FeatureMapSettings:
        """The search settings; the margin and the kinds are the defaults here (the panel sets them from its ribbon controls)."""
        return fm.FeatureMapSettings(
            dirt_min=self.dirt_spin.value() / 100.0,
            structure_min=self.structure_spin.value() / 100.0,
            certainty=float(self.certainty_spin.value()),
            core_share=self.edge_spin.value() / 100.0,
        )

    def cubes(self) -> int:
        return int(self.cubes_spin.value())


_KIND_TIPS = {
    fm.DIRT_DARK: (
        "Spots that take light by the same share at every wavelength (usually dirt: particles, specks, smudges). They are looked for "
        "anywhere in the image."
    ),
    fm.DIRT_BRIGHT: (
        "Spots that add light at every wavelength (rare; a bright rim next to a big dark smudge can be one). They are looked for "
        "anywhere in the image."
    ),
    fm.STRUCTURE_MISSING: (
        "A disk's resonance (its plasmon contrast) is weaker than a whole disk's: nanostructure is missing, e.g. a notch at the rim or "
        "a scratch across it. Looks brighter. Judged on the disks only."
    ),
    fm.STRUCTURE_EXTRA: (
        "A disk's resonance (its plasmon contrast) is stronger than a whole disk's: more, denser nanostructure. Looks darker. Judged on "
        "the disks only."
    ),
    fm.DIRT_FAINT: (
        "Light-blocking spots that dim gently (under 1.7 % of the light per pixel): soft shadows of dust out of focus, faint specks. "
        "Static and the same at every wavelength, they move no peak; off by default (masking costs good pixels)."
    ),
}


class _KindsPopover(QWidget):
    """Which kinds of feature Apply adds, one check box each, in the popup of the ribbon's kinds button (2026-11-05; they were in the
    settings popup). A kind only picks features the map already holds, so a click acts at once. `kinds_changed` fires on every click."""

    kinds_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Add to the mask:", self))
        self.kind_checks: dict[str, QCheckBox] = {}
        for kind in fm.KINDS:
            check = QCheckBox(fm.KIND_LABELS[kind], self)
            check.setToolTip(_KIND_TIPS[kind])
            self.kind_checks[kind] = check
            layout.addWidget(check)
        self.set_defaults()
        for check in self.kind_checks.values():
            check.toggled.connect(lambda _on: self.kinds_changed.emit())

    def set_defaults(self) -> None:
        defaults = fm.FeatureMapSettings().kinds
        for kind, check in self.kind_checks.items():
            check.setChecked(kind in defaults)

    def kinds(self) -> frozenset[str]:
        return frozenset(kind for kind, check in self.kind_checks.items() if check.isChecked())


class FeatureMapEditPanel(QWidget):
    """The Feature map tool: settings, Process, kinds, margin (2026-11-04; replaces the ROI features tool of 2026-10-29; the eye and the
    Apply button went 2026-11-23).

    Screen purpose: map every small feature of the image (light-blocking spots, light-adding spots, faint light-blocking spots, disks
    whose resonance is weaker or stronger: `feature_map.KIND_LABELS`, named for what is measured, not for a cause), without ROIs, and
    **select** the kinds wanted. The selection is then like any other: the Selection group's Add puts it in the mask, the toolbar's
    tools refine it, Ctrl+Z takes it back. A mask made this way stays right when ROIs are moved or changed.
    Layout (one row, like the other tools; the left half makes the map, the right half chooses what of it is selected):

        [settings v] [process]  [kinds v] [margin px]

    - **settings**: the *search* settings: the limits (dirt %, nanostructure %, certainty, feature edge) and the cubes to average
      (popup; its tooltip always shows the numbers). Remembered. A number changes which features are found, so it needs Process
      again (~2 s on the same image and cubes: the slow part is kept; a new cube count is a full run).
    - **process**: map the image on screen from the mean of the cubes (~20-40 s the first time, worker processes; progress in the
      status bar). When it ends, the pixels of the enabled kinds (grown by the margin) **replace the selection** (one undo step, cut to
      the lock; nothing found = the selection is left as it was). Changes no mask. A green play when idle; while it runs the same button
      is a red stop (like the Analysis and Tuning tabs' Run / Stop): a click stops the run and changes nothing.
    - **kinds**: one check box per kind of feature to select (popup). Remembered. Acts at once, on the map already made.
    - **margin**: how many pixels each feature is grown by (the optics blur its edge). The Background tab's "Excl. dilatation" look
      (`ribbon_fields.style_ribbon_number_field`, a bare "n px" field; its tooltip names it, like the Auto mask's feathering field).
      Remembered. Acts on the map already made, no new Process: the pixels are regrown off the GUI thread (~0.3 s,
      `FeatureMapTask.set_margin`) and the old ones stay until then.
    - **kinds and margin re-make the selection** while it is still the one this tool made (`_owns_selection`): any other change of the
      selection (a click with a tool, Deselect, an undo, a lock, a rotation) ends that, so a changed kind or margin never overwrites the
      user's work. Each re-make is one undo step.

    Important states: no image (Process disabled); running (the settings button is disabled); a run that ends while another image is on
    screen (nothing is selected; the status line says for which image the map was made); numbers changed since the run (Process's tooltip
    says so). Errors: in the status line (no dataset, fewer than 3 wavelengths, no disks found, a margin that could not be applied)."""

    def __init__(
        self,
        geometry: GeometryModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        area_selection: AreaSelectionModule,
        features: FeatureMapTask,
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._features = features
        self._dataset = dataset
        self._geometry = geometry
        self._selection = area_selection
        self._status = status
        self._frame: tuple[int, float] | None = None  # the frame on screen
        self._shape: tuple[int, int] | None = None  # its (displayed) pixel grid: the selection is made on it
        self._owns_selection = False  # the selection now is the one this tool made, untouched since
        self._made_from: dict | None = None  # the map (its `kind_cores`) that selection was made from: a new run is a new map
        self._making = False  # this tool is changing the selection (its own `selection_changed` is not "someone else's edit")

        # Run / Stop in one button, like the Analysis and Tuning tabs: a green play, a red stop while the map runs (a click stops it)
        self._process_button = action_button(self, "player-play", get_active_theme().accent_green, "")
        self._process_button.clicked.connect(self._on_process_clicked)
        self._shows_stop: bool | None = None  # which icon the button shows (set by `_refresh_state`)

        # The search settings and the kinds are in popups: the Mask ribbon row is already ~800 px wide.
        self._pop = _FeatureMapPopover(self)
        self._settings_button = self._popup_button("settings", self._pop)
        self._pop.numbers_changed.connect(self._refresh_state)
        self._kinds_pop = _KindsPopover(self)
        self._kinds_button = self._popup_button("checkbox", self._kinds_pop)
        self._kinds_pop.kinds_changed.connect(self._on_kinds_changed)

        # The margin: the Background tab's exclusion-dilatation field (frameless, "n px", `ribbon_fields.py`), no arrows.
        self._margin_spin = QSpinBox(self)
        self._margin_spin.setRange(0, 10)
        self._margin_spin.setValue(fm.FeatureMapSettings().grow_px)
        self._margin_spin.setSuffix(" px")
        self._margin_spin.setKeyboardTracking(False)
        style_ribbon_number_field(self._margin_spin, "10 px")
        self._margin_spin.setToolTip(
            "Margin: the mask is each feature found, grown by this many pixels (the optics blur its edge). It acts on the map already "
            "made: no new Process is needed."
        )
        self._margin_spin.valueChanged.connect(self._on_margin_changed)
        self._pop.reset_button.clicked.connect(self._reset_added)  # Defaults also puts the kinds and the margin back

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._settings_button)
        layout.addWidget(self._process_button)
        layout.addSpacing(4)  # the map is made to the left, what of it is selected is chosen to the right
        layout.addWidget(self._kinds_button)
        layout.addWidget(self._margin_spin)
        layout.addStretch(1)

        features.running_changed.connect(lambda _running: self._refresh_state())
        features.result_changed.connect(self._on_result_changed)
        features.completed.connect(self._on_completed)
        features.failed.connect(status)
        features.cancelled.connect(lambda: status("Feature map cancelled; nothing was changed."))
        area_selection.selection_changed.connect(self._on_selection_changed)
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)
        dataset.dataset_loaded.connect(lambda _ds: self._refresh_state())
        dataset.dataset_cleared.connect(self._refresh_state)
        features.set_kinds(self._kinds_pop.kinds())
        features.set_margin(self._margin_spin.value())
        self._refresh_state()

    def _popup_button(self, icon_name: str, content: QWidget) -> QToolButton:
        """An icon button the size and look of the Process button that opens `content` as a popup on a click."""
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setFixedSize(self._process_button.size())
        button.setIconSize(self._process_button.iconSize())
        button.setStyleSheet(self._process_button.styleSheet())
        button.setIcon(disabled_safe_icon(icon_name, _PLAIN_ICON_COLOR))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        action = QWidgetAction(menu)
        action.setDefaultWidget(content)
        menu.addAction(action)
        button.setMenu(menu)
        return button

    # -- remembered state ---------------------------------------------------------------------------------

    def bind_ui_state(self, store) -> None:
        """Remember the numbers, the margin and the kinds across restarts (`panels/ui_state.py`; the keys are those of before the margin
        and the kinds moved to the ribbon, so a saved choice carries over)."""
        store.bind("image/feature_map/dirt_percent", self._pop.dirt_spin)
        store.bind("image/feature_map/structure_percent", self._pop.structure_spin)
        store.bind("image/feature_map/certainty", self._pop.certainty_spin)
        store.bind("image/feature_map/edge_percent", self._pop.edge_spin)
        store.bind("image/feature_map/margin_px", self._margin_spin)
        store.bind("image/feature_map/cubes", self._pop.cubes_spin)
        for kind, check in self._kinds_pop.kind_checks.items():
            store.bind(f"image/feature_map/kind_{kind}", check)
        self._features.set_kinds(self._kinds_pop.kinds())
        self._features.set_margin(self._margin_spin.value())

    def refresh_theme(self) -> None:
        self._shows_stop = None  # the play icon takes the theme's green: redraw it
        self._refresh_state()

    def settings(self) -> fm.FeatureMapSettings:
        """The search settings of the popup, with the margin and the kinds of the ribbon controls."""
        return dataclasses.replace(self._pop.settings(), grow_px=int(self._margin_spin.value()), kinds=self._kinds_pop.kinds())

    def popover(self) -> _FeatureMapPopover:
        """The settings popup's widgets (tests set them like a user would)."""
        return self._pop

    def kinds_popover(self) -> _KindsPopover:
        """The kinds popup's check boxes (tests set them like a user would)."""
        return self._kinds_pop

    def margin_spin(self) -> QSpinBox:
        """The margin field of the ribbon row (tests set it like a user would)."""
        return self._margin_spin

    # -- the buttons ----------------------------------------------------------------------------------------

    def _on_process_clicked(self) -> None:
        """The Run / Stop button: start a run, or stop the one going on."""
        if self._features.is_running():
            self.stop()
        else:
            self.start()

    def stop(self) -> None:
        """Stop the run going on (the Stop face of the button; also callable by tests). Nothing is changed: the result of an earlier
        run, if any, stays; the selection and the masks are not touched."""
        if self._features.is_running():
            self._status("Stopping the feature map...")
            self._features.cancel()

    def start(self) -> None:
        """Map the image on screen with the settings shown (the button's action; also callable by tests)."""
        frame = self._frame
        if frame is None or self._features.is_running():
            return
        self._status("Mapping the features of this image... (progress and Cancel in the status bar)")
        self._features.start(frame, self.settings(), self._pop.cubes())

    # -- the selection ----------------------------------------------------------------------------------------

    def _make_selection(self, *, clear_if_empty: bool) -> tuple[int, bool] | None:
        """Replace the selection with the pixels of the enabled kinds of the finished map, for the image on screen. Returns ``(pixels
        selected, whether the selection changed)``, or ``None`` when there is nothing to make it from (no map for this image, a size
        that changed). The raw-space pixels go onto the displayed grid the way the mask overlay and the other selection tools do it
        (`resolve_external_mask`); `combine_region` cuts them to the lock and makes the undo step. Pixels identical to the selection
        now change nothing, so a second call for the same map adds no step. With no pixel to select (inside the lock) the selection is
        cleared only if `clear_if_empty` (the selection already is this tool's and follows the kinds), else left alone."""
        result = self._features.result()
        pixels = self._features.pixels_for(self._frame)
        if result is None or pixels is None or self._shape is None:
            return None
        region = resolve_external_mask(pixels, self._geometry.settings(), None)
        if region is None or region.shape != self._shape:
            return None
        fence = self._selection.lock_mask(region.shape)
        count = int(np.count_nonzero(region if fence is None else region & fence))
        if count == 0 and not clear_if_empty:
            return 0, False
        self._making = True
        try:
            changed = self._selection.combine_region(region, REPLACE, SELECTION_LABEL)
        finally:
            self._making = False
        self._owns_selection = True
        self._made_from = result.kind_cores
        return count, changed

    def _on_selection_changed(self) -> None:
        """The selection changed: unless this tool did it, the user took over (a tool, Deselect, an undo, a lock, a rotation)."""
        if not self._making:
            self._owns_selection = False

    def _on_result_changed(self) -> None:
        """The task's result changed. The same map with other kinds or another margin re-makes the selection if it is still the tool's
        (a new run, `kind_cores` of its own, is made by `_on_completed`; a margin still being regrown waits for its pixels)."""
        self._refresh_state()
        result = self._features.result()
        if not self._owns_selection or result is None or result.kind_cores is not self._made_from or self._features.is_regrowing():
            return
        made = self._make_selection(clear_if_empty=True)
        if made is not None:
            count, changed = made
            if changed:
                self._status(f"Feature map: the selection follows the kinds and the margin ({count:,} px).")

    # -- state ------------------------------------------------------------------------------------------------

    def _on_kinds_changed(self) -> None:
        self._features.set_kinds(self._kinds_pop.kinds())
        self._refresh_state()

    def _on_margin_changed(self, value: int) -> None:
        self._features.set_margin(value)  # the pixels follow off the GUI thread; `result_changed` (and then the selection) follows
        self._refresh_state()

    def _reset_added(self) -> None:
        """The popup's Defaults: the kinds and the margin back to their starting values too (they live on the ribbon row now)."""
        self._kinds_pop.set_defaults()
        self._margin_spin.setValue(fm.FeatureMapSettings().grow_px)

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        """An image was rendered (every step of Play): O(1); the buttons and tooltips are rebuilt only when an image appears."""
        first = self._frame is None
        self._frame = (int(cube_index), float(wavelength_nm))
        self._shape = (int(image.shape[0]), int(image.shape[1]))
        if first:
            self._refresh_state()

    def _on_image_cleared(self) -> None:
        self._frame = None
        self._shape = None
        self._refresh_state()

    def _numbers_changed_since(self, result: FeatureMapResult) -> bool:
        """Whether a *search* setting differs from the run's. The kinds and the margin act on the map at once, so they never make it stale.
        The cubes are compared as the cubes a run would average now (from the result's own cube) against the ones it averaged: asking
        for 12 on a dataset of 2 cubes is the same run as asking for 2 (before 2026-11-05 the number asked was compared with the number
        used, so the tooltip always called the settings changed on a dataset with fewer cubes than asked)."""
        now = dataclasses.replace(self.settings(), kinds=frozenset(), grow_px=0)
        then = dataclasses.replace(result.settings, kinds=frozenset(), grow_px=0)
        cubes_now = choose_cubes(self._dataset.spectral_cubes(), result.frame[0], self._pop.cubes())
        return now != then or cubes_now != result.cubes

    def _refresh_state(self, *_args: object) -> None:
        running = self._features.is_running()
        loaded = self._frame is not None
        result = self._features.result()
        self._process_button.setEnabled(running or loaded)  # while running it is the Stop button
        if self._shows_stop is not running:
            self._process_button.setIcon(
                disabled_safe_icon("player-stop", _STOP_COLOR) if running else disabled_safe_icon("player-play", get_active_theme().accent_green)
            )
            self._shows_stop = running
        self._settings_button.setEnabled(not running)
        s = self.settings()
        kinds = "; ".join(fm.KIND_LABELS[k] for k in fm.KINDS if k in s.kinds) or "no kind"  # ";": a label can hold a comma
        self._settings_button.setToolTip(
            f"Feature map settings (what decides which features are found): dirt from {s.dirt_min * 100:.1f} %, nanostructure from "
            f"{s.structure_min * 100:.0f} %, certainty {s.certainty:.1f}, feature edge {s.core_share * 100:.0f} %, "
            f"{self._pop.cubes()} cubes. Click to change; press Process after."
        )
        self._kinds_button.setToolTip(
            f"Kinds of feature selected: {kinds}. Click to choose; it acts on the map already made, no new Process needed (the selection "
            "follows while you have not changed it)."
        )
        process_tip = (
            "Map every small feature of this image (spots that block or add light, and disks whose resonance is weaker or stronger) "
            "from the mean of the cubes, without ROIs: moving ROIs later keeps the result. The features of the enabled kinds become the "
            "selection (Ctrl+Z takes it back; the Selection group's Add puts it in the mask). Progress and Cancel are in the status bar."
        )
        if running:
            process_tip = "Stop the feature map. Nothing is changed: an earlier result stays, the selection is not touched."
        elif not loaded:
            process_tip += "\nShow an image first."
        elif result is not None and self._numbers_changed_since(result):
            process_tip += "\nThe settings changed since the last run: press to update the result."
        self._process_button.setToolTip(process_tip)

    def _on_completed(self, summary: FeatureMapSummary) -> None:
        """A run ended: its enabled kinds become the selection (when the image on screen is the one it was made for)."""
        parts = [f"{fm.KIND_LABELS[k]} ({summary.counts[k]})" for k in fm.KINDS if summary.counts[k]]
        head = (
            f"Feature map ({summary.disks} disks, {summary.cubes} cubes, {summary.seconds:.0f} s"
            f"{', slow part reused' if summary.kept else ''}): {'; '.join(parts) if parts else 'nothing found'}. "
        )
        cube, wavelength = summary.frame
        made = self._make_selection(clear_if_empty=False)
        if made is None:
            self._status(
                f"{head}It was made for cube {cube}, {wavelength:g} nm, which is not on screen now, so nothing was selected: go back to "
                "that image and press Process again (about 2 s, the slow part is kept)."
            )
            return
        count, _changed = made
        kinds = "; ".join(fm.KIND_LABELS[k] for k in fm.KINDS if k in self._features.kinds()) or "no kind"
        if count == 0:
            inside = " inside the lock" if self._selection.has_lock() else ""
            self._status(f"{head}No pixel of the enabled kinds ({kinds}){inside}: the selection is unchanged.")
        else:
            self._status(f"{head}Selected {count:,} px of the enabled kinds ({kinds}). Selection > Add puts them in the mask.")
