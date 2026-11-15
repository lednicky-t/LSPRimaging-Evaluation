"""The "Manual edit" group's two per-tool control panels - `FeatureMapEditPanel` and `EraserEditPanel` - swapped by `MaskEditToolPicker`
via a `QStackedWidget` (`mask_tab.py`).

**2026-11-15: five panels left.** The Histogram selection, Threshold, Local contrast, Morphology and Draw panels (2026-10-02 / 2026-10-29)
moved into the selection toolbar beside the image: they make or modify a *selection* now, and the Mask tab's Selection group turns a
selection into mask pixels (`panels/image/selection_by_value.py`, `selection_modify.py`, `brush_settings.py`,
`mask_selection_editor.py`; record `docs/selection_lock_and_tool_move_2026-11-15.md`). What stays acts on the mask itself or is a
two-stage process:
- **Feature map** (2026-11-04, replaced the ROI features tool of 2026-10-29): `FeatureMapEditPanel`. Not a live preview but a Process button
  (~20-30 s, worker processes, progress and Cancel in the status bar), a settings popup with the search limits and the cubes, then
  (2026-11-05) a kinds popup (one check box per kind) and a margin field that act on the finished map at once, and an Apply button that adds
  the enabled kinds at the Persistent / Individual scope. No ROIs: the result stays right when ROIs move.
  Record: `docs/roi_feature_detection_2026-10-29.md` sections 10-11.
- **Eraser** (2026-10-29): `EraserEditPanel` is only a hint icon; the tool is a click on the image (`panels/mask_grain_eraser.py`, routed by
  `CanvasInteraction`), which removes the connected blob of mask pixels under it.

Every panel keeps this ribbon's established sizing: 28px-tall icon buttons/spinboxes, `transparent_icon_button_stylesheet()` chrome, no
inline text labels (tooltips only) - these panels live inside `ImageToolRibbon`'s fixed-height content row.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
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

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, MaskModule, MaskScopeModule
from ...image_tools.mask import feature_map as fm
from ..feature_map_task import DEFAULT_CUBES, FeatureMapResult, FeatureMapSummary, FeatureMapTask, choose_cubes
from ..mask_candidate_editor import MaskCandidateEditor
from ..mask_edit_common import ADD_COLOR, BUTTON_SIZE, ICON_SIZE, action_button, disabled_safe_icon
from ..mask_edit_preview import KIND_FEATURE_MAP, MaskEditPreview
from ..ribbon_fields import style_ribbon_number_field
from .edit_preview_toggle import EditPreviewToggle

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
    """The Feature map tool: eye, settings, Process, kinds, margin, Apply (2026-11-04; replaces the ROI features tool of 2026-10-29).

    Screen purpose: map every small feature of the image (light-blocking spots, light-adding spots, faint light-blocking spots, disks
    whose resonance is weaker or stronger: `feature_map.KIND_LABELS`, named for what is measured, not for a cause), without ROIs, look at it
    on the image, and add the kinds wanted to the mask. A mask made this way stays right when ROIs are moved or changed.
    Layout (one row, like the other tools; the left half makes the map, the right half chooses what of it is added):

        [eye] [settings v] [process]  [kinds v] [margin px] [apply +]

    - **eye**: show or hide the pixels Apply would add (the enabled kinds, grown by the margin) on the image.
    - **settings**: the *search* settings: the limits (dirt %, nanostructure %, certainty, feature edge) and the cubes to average
      (popup; its tooltip always shows the numbers). Remembered. A number changes which features are found, so it needs Process
      again (~2 s on the same image and cubes: the slow part is kept; a new cube count is a full run).
    - **process**: map the image on screen from the mean of the cubes (~20-40 s the first time, worker processes; progress in the
      status bar). Changes no mask. A green play when idle; while it runs the same button is a red stop (like the Analysis and Tuning
      tabs' Run / Stop): a click stops the run and changes nothing.
    - **kinds**: one check box per kind of feature to add (popup). Remembered. Acts at once, on the map already made.
    - **margin**: how many pixels each feature is grown by in the mask (the optics blur its edge). The Background tab's "Excl.
      dilatation" look (`ribbon_fields.style_ribbon_number_field`, a bare "n px" field; the row's one caption is "Manual edit", so
      its tooltip names it, like the Auto mask's feathering field). Remembered. Acts on the map already made, no new Process: the
      pixels are regrown off the GUI thread (~0.3 s, `FeatureMapTask.set_margin`) and the old ones stay on screen until then.
    - **apply**: add the pixels of the enabled kinds to the mask in effect at this frame, at the scope the State group says
      (Persistent or Individual), in one mask change, cut to the area selection if there is one. Like the other tools' Add it is not
      on the undo stack. Enabled only while the image on screen is the one the map was made for and no margin is being regrown.

    Important states: no image (Process disabled); running (the settings button is disabled; Apply too); a result for another frame
    (Apply disabled, its tooltip says which frame); a margin being regrown (Apply disabled for ~0.3 s); numbers changed since the run
    (Process's tooltip says so). Errors: in the status line (no dataset, fewer than 3 wavelengths, no disks found, a margin that
    could not be applied)."""

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        preview: MaskEditPreview,
        features: FeatureMapTask,
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._features = features
        self._dataset = dataset
        self._status = status
        self._editor = MaskCandidateEditor(mask, chromatic, dataset, image_panel, mask_scope, preview, status)

        self._toggle = EditPreviewToggle(preview, self)

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

        self._apply_button = action_button(self, "square-rounded-plus", ADD_COLOR, "")
        self._apply_button.clicked.connect(self.apply)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._settings_button)
        layout.addWidget(self._process_button)
        layout.addSpacing(4)  # the map is made to the left, what of it is added is chosen to the right
        layout.addWidget(self._kinds_button)
        layout.addWidget(self._margin_spin)
        layout.addWidget(self._apply_button)
        layout.addStretch(1)

        features.running_changed.connect(lambda _running: self._refresh_state())
        features.result_changed.connect(self._refresh_state)
        features.regrowing_changed.connect(lambda _regrowing: self._refresh_state())
        features.completed.connect(self._on_completed)
        features.failed.connect(status)
        features.cancelled.connect(lambda: status("Feature map cancelled; nothing was changed."))
        self._frame_state: tuple[bool, bool] = (False, False)  # (an image is shown, the result belongs to it) at the last full refresh
        image_panel.image_rendered.connect(self._on_frame_changed)
        image_panel.image_cleared.connect(self._on_frame_changed)
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
        self._toggle.refresh_theme()
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
        run, if any, stays; no mask is touched."""
        if self._features.is_running():
            self._status("Stopping the feature map...")
            self._features.cancel()

    def start(self) -> None:
        """Map the image on screen with the settings shown (the button's action; also callable by tests)."""
        frame = self._editor.frame()
        if frame is None or self._features.is_running():
            return
        self._status("Mapping the features of this image... (progress and Cancel in the status bar)")
        self._features.start(frame, self.settings(), self._pop.cubes())

    def apply(self) -> None:
        """Add the pixels of the enabled kinds to the mask (the button's action; also callable by tests). Waits for a margin that is
        being regrown: until its pixels are in, the ones the task holds are the earlier margin's."""
        if self._features.is_regrowing() or self._features.pixels_for(self._editor.frame()) is None:
            return
        self._editor.apply(KIND_FEATURE_MAP, subtract=False, done=self._refresh_state)

    # -- state ------------------------------------------------------------------------------------------------

    def _on_kinds_changed(self) -> None:
        self._features.set_kinds(self._kinds_pop.kinds())
        self._refresh_state()

    def _on_margin_changed(self, value: int) -> None:
        self._features.set_margin(value)  # the pixels follow off the GUI thread; `regrowing_changed` / `result_changed` refresh us
        self._refresh_state()

    def _reset_added(self) -> None:
        """The popup's Defaults: the kinds and the margin back to their starting values too (they live on the ribbon row now)."""
        self._kinds_pop.set_defaults()
        self._margin_spin.setValue(fm.FeatureMapSettings().grow_px)

    def _on_frame_changed(self, *_args: object) -> None:
        """An image was rendered (every step of Play): O(1) unless the answer to "is there an image, and is the result its own" changed
        - the tooltips and button states are rebuilt only then."""
        frame = self._editor.frame()
        if (frame is not None, self._features.pixels_for(frame) is not None) != self._frame_state:
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
        frame = self._editor.frame()
        running = self._features.is_running()
        loaded = frame is not None
        result = self._features.result()
        shown = self._features.pixels_for(frame)
        self._frame_state = (loaded, shown is not None)
        self._process_button.setEnabled(running or loaded)  # while running it is the Stop button
        if self._shows_stop is not running:
            self._process_button.setIcon(
                disabled_safe_icon("player-stop", _STOP_COLOR) if running else disabled_safe_icon("player-play", get_active_theme().accent_green)
            )
            self._shows_stop = running
        regrowing = self._features.is_regrowing()
        self._settings_button.setEnabled(not running)
        self._apply_button.setEnabled(shown is not None and bool(shown.any()) and not running and not regrowing)
        s = self.settings()
        kinds = "; ".join(fm.KIND_LABELS[k] for k in fm.KINDS if k in s.kinds) or "no kind"  # ";": a label can hold a comma
        self._settings_button.setToolTip(
            f"Feature map settings (what decides which features are found): dirt from {s.dirt_min * 100:.1f} %, nanostructure from "
            f"{s.structure_min * 100:.0f} %, certainty {s.certainty:.1f}, feature edge {s.core_share * 100:.0f} %, "
            f"{self._pop.cubes()} cubes. Click to change; press Process after."
        )
        self._kinds_button.setToolTip(
            f"Kinds of feature added to the mask: {kinds}. Click to choose; it acts on the map already made, no new Process needed."
        )
        process_tip = (
            "Map every small feature of this image (spots that block or add light, and disks whose resonance is weaker or stronger) "
            "from the mean of the cubes, without ROIs: moving ROIs later keeps the result. It changes no mask; progress and Cancel are "
            "in the status bar."
        )
        if running:
            process_tip = "Stop the feature map. Nothing is changed: an earlier result stays, no mask is touched."
        elif not loaded:
            process_tip += "\nShow an image first."
        elif result is not None and self._numbers_changed_since(result):
            process_tip += "\nThe settings changed since the last run: press to update the result."
        self._process_button.setToolTip(process_tip)
        if shown is not None and regrowing:
            self._apply_button.setToolTip("The new margin is being applied to the map: a moment, then this button is ready.")
        elif shown is not None:
            counts = "; ".join(f"{fm.KIND_LABELS[k]} ({result.counts[k]})" for k in fm.KINDS if k in self._features.kinds())
            self._apply_button.setToolTip(
                f"Add the {int(shown.sum()):,} pixels of the enabled kinds ({counts or 'none enabled'}), each feature grown by "
                f"{result.margin_px} px, to the mask, at the scope chosen under State (Persistent or Individual)."
                if shown.any() else "Nothing to add: no feature of the enabled kinds (see the kinds button)."
            )
        elif result is not None:
            cube, wavelength = result.frame
            self._apply_button.setToolTip(
                f"The map was made for cube {cube}, {wavelength:g} nm. Go back to that image to add it, or press Process here."
            )
        else:
            self._apply_button.setToolTip("Nothing to add yet: press Process first.")

    def _on_completed(self, summary: FeatureMapSummary) -> None:
        parts = [f"{fm.KIND_LABELS[k]} ({summary.counts[k]})" for k in fm.KINDS if summary.counts[k]]
        added = "; ".join(fm.KIND_LABELS[k] for k in fm.KINDS if k in self._features.kinds()) or "no kind"
        text = (
            f"Feature map ({summary.disks} disks, {summary.cubes} cubes, {summary.seconds:.0f} s"
            f"{', slow part reused' if summary.kept else ''}): {'; '.join(parts) if parts else 'nothing found'}. "
            f"The eye shows the {summary.pixels:,} px of the enabled kinds ({added}); the + button adds them to the mask."
        )
        self._status(text)


class EraserEditPanel(QWidget):
    """The Eraser's row (2026-10-29): nothing to set, so one passive "click on the image" icon whose tooltip says what the tool does.

    The tool itself is not here: while it is picked and the Mask tab is open, a left click on the image goes to
    `MaskGrainEraser.erase_at` (`CanvasInteraction`, which also shows a cross cursor). Screen purpose: remove one blob of the mask by
    clicking it. Main action: click a masked pixel. States: armed (this row is shown and the Mask tab is open) / not. Error messages
    (status line): no mask pixel under the click; the click is outside the area selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hint = QLabel(self)
        self._hint.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setToolTip(
            "Eraser: click a mask pixel on the image and the whole blob of connected mask pixels (a grain; touching at a corner "
            "counts) leaves the mask. It acts at the scope chosen under State (Persistent or Individual) and stays inside the area "
            "selection, if there is one."
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._hint)
        layout.addStretch(1)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        icon = load_tabler_icon("pointer", color=get_active_theme().text_dim, size=ICON_SIZE * 2, stroke_width=2.1)
        self._hint.setPixmap(icon.pixmap(ICON_SIZE, ICON_SIZE))
