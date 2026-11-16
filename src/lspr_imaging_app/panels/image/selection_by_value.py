"""The toolbar's three "select by value" tools (2026-11-15, maintainer: the Mask tab's Histogram selection / Threshold / Local contrast
tools become *selection* tools; the mask is a layer a selection is turned into). First one button with a method switch; the same day split
into **three tools of their own** (maintainer: "split the Histogram, Threshold, contrast ... into separated icons, using the previous icons").

Screen purpose: turn "which pixels look like this" into a selection, from any ribbon tab.
Main user actions: pick the tool (Histogram range / Threshold / Local contrast: armed like the wand, picking it again leaves it), then **click
the image** (anywhere: these are image-wide methods) to select what the method finds. Right-click the tool's button for its numbers (a pop-up
beside the button). **Right-drag on the image** with the tool armed changes them (`tool_drag.py`): sideways = the threshold (right = a bigger
region, so a lower number), up = the blur size (sigma). Shift / Alt / Shift+Alt held at the click, or the mode picker, say how the result
combines with the selection (Replace / Add / Subtract / Intersect).
Layout: three buttons of the selection toolbar's value section; the pop-up is `ByValueSettingsForm`, one form that shows the numbers of the
method whose button was right-clicked.
Important states: busy (Threshold and Local contrast take 0.2-4 s on a 1.2 MP plane: the tool tip says so and a second click waits); no
image; no Histogram range yet (that range is set on the Histogram panel, so the Histogram tool has no right-drag numbers here).
Validation: the spin boxes bound every number; a result with no pixel (inside the lock, if there is one) changes nothing and the
status bar says so.
Error messages (status bar): no image on screen; no range on the Histogram; the image changed before the result arrived; nothing found.

**The three methods** - the very pixels the Mask tab's tools used to add to the mask, now the selection:
- **Histogram range**: the finite pixels of the image on screen whose value is inside the range highlighted on the Histogram panel
  (`image_tools.preprocess.histogram_highlight_region`). Pointwise: instant, on the GUI thread (a comparison over the plane).
- **Threshold** (`relative`): pixels that deviate from their own blurred local background by more than the given share
  (`raster_tools.create_relative_contrast_mask`), computed on the **raw** plane of the frame on screen. **Two modes, Brighter and Darker**
  (2026-11-16, maintainer: until then it took both signs at once, which the icon never said): Brighter = more than that share *above* the
  background, Darker = more than that share *below* it. The wheel over the button flips them (the coloured part of the icon is over or
  under its line), the form has the same switch (`side_combo`), the choice is remembered (`selection/threshold_side`, a UI state, not a
  session field). Both signs at once is Local contrast (`both` in the maths, not offered here) or two clicks in Add mode.
- **Local contrast**: pixels more than `z` local standard deviations from their local mean
  (`raster_tools.create_local_contrast_mask`), also on the raw plane.
Threshold and Local contrast go through `MaskEditPreview.request_candidate` (a plain thread, cached by frame and numbers, GIL released
by scipy: `image_tools/mask_candidates.py`), then `resolve_external_mask` maps the raw-space pixels onto the displayed image like the
mask overlay does (exact without rotation, about one pixel at the edge under one).

**The lock** (maintainer's rule, 2026-11-15): the tool computes on the whole image and the result is cut to the lock afterwards
(`AreaSelectionModule.combine_region`), so a result inside a lock equals the unlocked result AND the lock, and a cached candidate stays
valid when the lock moves. **No preview, except for the Threshold** (the wand's decision of 2026-11-15: the selection is undoable, so the
answer to a wrong result is Ctrl+Z, not a preview; the maintainer reversed it for the Threshold on 2026-11-16: "make selection by
threshold live"). While the Threshold is armed the pixels a click would select are tinted on the image and follow its numbers, its side
and the frame (`MaskEditPreview.set_value_tool`, `EditPreviewLayer`; the tint is cut to the lock only, and is the tool's own region, not
how it would combine). The numbers are `MaskSettings` (saved with the session), the same fields the Mask tab's panels edited.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from ...applog.events import log_event
from ...image_tools import GeometryModule, MaskModule
from ...image_tools.mask import raster_tools
from ...image_tools.mask.model import MaskSettings
from ...image_tools.preprocess import histogram_highlight_region, resolve_external_mask
from ...selection import AreaSelectionMode, AreaSelectionModule, HighlightRangeModule
from ...selection.area_selection_module import how_for_keys
from ..mask_edit_preview import KIND_LOCAL_CONTRAST, KIND_THRESHOLD, MaskEditPreview
from ..mask_tool_settings import push_tool_settings
from ..ui_state import UiStateStore
from .tool_drag import DragAxis

if TYPE_CHECKING:
    from .panel import ImagePanel

logger = logging.getLogger(__name__)


class ValueMethod(enum.Enum):
    HISTOGRAM = "histogram"
    THRESHOLD = "threshold"
    LOCAL_CONTRAST = "local_contrast"


VALUE_METHOD_LABELS = {
    ValueMethod.HISTOGRAM: "Histogram range",
    ValueMethod.THRESHOLD: "Threshold (relative to the local background)",
    ValueMethod.LOCAL_CONTRAST: "Local contrast (local z-score)",
}
# The armed tool -> its method (the tools are `AreaSelectionMode` members so they arm and disarm like the wand).
MODE_METHODS = {
    AreaSelectionMode.HISTOGRAM: ValueMethod.HISTOGRAM,
    AreaSelectionMode.THRESHOLD: ValueMethod.THRESHOLD,
    AreaSelectionMode.CONTRAST: ValueMethod.LOCAL_CONTRAST,
}
_NOUN = {  # how a method is named in a sentence ("Threshold: selected 40 px")
    ValueMethod.HISTOGRAM: "Histogram range",
    ValueMethod.THRESHOLD: "Threshold",
    ValueMethod.LOCAL_CONTRAST: "Local contrast",
}
_KIND = {ValueMethod.THRESHOLD: KIND_THRESHOLD, ValueMethod.LOCAL_CONTRAST: KIND_LOCAL_CONTRAST}
# The Threshold's two modes (2026-11-16): which way from the local background it selects. Brighter first = the default (and the picture the
# button always had: the area over the line coloured).
THRESHOLD_SIDE_LABELS = ((raster_tools.SIDE_BRIGHTER, "Brighter than the background"), (raster_tools.SIDE_DARKER, "Darker than the background"))


class ByValueSettingsForm(QWidget):
    """The numbers of one method (`set_method`), shown in the pop-up of that method's button. The numbers are the mask tools' (`MaskSettings`),
    so the form follows them (also a session restore) and writes them back."""

    changed = pyqtSignal()  # the method shown changed
    threshold_side_changed = pyqtSignal()  # the Threshold's Brighter / Darker switch changed (the wheel over its button, or this form)

    def __init__(self, mask: MaskModule, highlight_range: HighlightRangeModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._range = highlight_range
        self._syncing = False
        self._method = ValueMethod.HISTOGRAM

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        self.title = QLabel(self)  # which method these numbers are for
        outer.addWidget(self.title)

        # Histogram range: nothing to set here, only to read.
        self._histogram_box = QWidget(self)
        histogram_layout = QVBoxLayout(self._histogram_box)
        histogram_layout.setContentsMargins(0, 0, 0, 0)
        self.range_label = QLabel(self._histogram_box)
        self.range_label.setWordWrap(True)
        histogram_layout.addWidget(self.range_label)
        self._histogram_layout = histogram_layout

        # Threshold
        self._threshold_box = QWidget(self)
        threshold_form = QFormLayout(self._threshold_box)
        threshold_form.setContentsMargins(0, 0, 0, 0)
        threshold_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.side_combo = QComboBox(self._threshold_box)
        for side, label in THRESHOLD_SIDE_LABELS:
            self.side_combo.addItem(label, side)
        self.side_combo.setToolTip(
            "Which way from the local background: Brighter = pixels above it by the threshold, Darker = pixels below it by the threshold. "
            "The wheel over the Threshold button on the toolbar switches it too."
        )
        self.threshold_spin = QDoubleSpinBox(self._threshold_box)
        self.threshold_spin.setRange(0.1, 500.0)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setSingleStep(1.0)
        self.threshold_spin.setSuffix(" %")
        self.threshold_spin.setToolTip(
            "Relative threshold: how far a pixel must be from its local background, in the direction picked above (share of the background). "
            "For Darker, 100 % means black: nothing is more than 100 % below a positive background, so a higher number selects nothing."
        )
        self.profile_sigma_spin = QSpinBox(self._threshold_box)
        self.profile_sigma_spin.setRange(3, 2000)
        self.profile_sigma_spin.setSuffix(" px")
        self.profile_sigma_spin.setKeyboardTracking(False)
        self.profile_sigma_spin.setToolTip("Profile sigma: the blur radius of the local background.")
        threshold_form.addRow("Select", self.side_combo)
        threshold_form.addRow("Threshold", self.threshold_spin)
        threshold_form.addRow("Sigma", self.profile_sigma_spin)

        # Local contrast
        self._contrast_box = QWidget(self)
        contrast_form = QFormLayout(self._contrast_box)
        contrast_form.setContentsMargins(0, 0, 0, 0)
        contrast_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.contrast_sigma_spin = QSpinBox(self._contrast_box)
        self.contrast_sigma_spin.setRange(1, 2000)
        self.contrast_sigma_spin.setSuffix(" px")
        self.contrast_sigma_spin.setKeyboardTracking(False)
        self.contrast_sigma_spin.setToolTip("Local contrast sigma: the radius of the local neighbourhood.")
        self.z_spin = QDoubleSpinBox(self._contrast_box)
        self.z_spin.setRange(0.1, 20.0)
        self.z_spin.setDecimals(1)
        self.z_spin.setSingleStep(0.1)
        self.z_spin.setToolTip("z-threshold: how many local standard deviations count as an outlier.")
        contrast_form.addRow("Sigma", self.contrast_sigma_spin)
        contrast_form.addRow("z", self.z_spin)

        for box in (self._histogram_box, self._threshold_box, self._contrast_box):
            outer.addWidget(box)

        self._sync_from_settings(mask.settings())
        self._refresh_range(highlight_range.current_range())
        for spin in (self.threshold_spin, self.profile_sigma_spin, self.contrast_sigma_spin, self.z_spin):
            spin.valueChanged.connect(self._on_spin_changed)
        self.side_combo.currentIndexChanged.connect(lambda _index: self.threshold_side_changed.emit())
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)
        highlight_range.range_changed.connect(self._refresh_range)
        self.set_method(ValueMethod.HISTOGRAM)

    def add_overlay_toggle(self, toggle: QWidget) -> None:
        """Put the show / hide button of the histogram highlight on the image (`HistogramOverlayToggle`) beside a caption, in the Histogram range's
        part of the form (so only that method shows it). `toggle` is made with this form as its parent."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(toggle)
        row.addWidget(QLabel("Show the range on the image", self._histogram_box), 1)
        self._histogram_layout.insertLayout(0, row)

    # -- the method ----------------------------------------------------------------------------------------

    def picked_method(self) -> ValueMethod:
        """The method whose numbers are shown (the last `set_method`)."""
        return self._method

    def method_label(self) -> str:
        return VALUE_METHOD_LABELS[self._method]

    def set_method(self, method: ValueMethod) -> None:
        """Show the numbers of `method` (a right-click on its button)."""
        self._method = method
        self.title.setText(f"<b>{VALUE_METHOD_LABELS[method]}</b>")
        self._histogram_box.setVisible(method is ValueMethod.HISTOGRAM)
        self._threshold_box.setVisible(method is ValueMethod.THRESHOLD)
        self._contrast_box.setVisible(method is ValueMethod.LOCAL_CONTRAST)
        self.changed.emit()

    # -- the Threshold's side ----------------------------------------------------------------------------------

    def threshold_side(self) -> str:
        """``"brighter"`` or ``"darker"``: which way from the local background the Threshold tool selects."""
        return str(self.side_combo.currentData())

    def threshold_side_label(self) -> str:
        return self.side_combo.currentText()

    def set_threshold_side(self, side: str) -> None:
        index = self.side_combo.findData(side)
        if index >= 0:
            self.side_combo.setCurrentIndex(index)

    def cycle_threshold_side(self, steps: int) -> str:
        """Move `steps` places along the two sides, wrapping round (the wheel over the Threshold button); returns the label now picked."""
        self.side_combo.setCurrentIndex((self.side_combo.currentIndex() + int(steps)) % self.side_combo.count())
        return self.threshold_side_label()

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the side across restarts (`panels/ui_state.py`). The numbers are `MaskSettings`, saved with the session; the side is not
        one of them (nothing stored in a session changed for it), so it is remembered like any other control of the panel."""
        store.bind("selection/threshold_side", self.side_combo)

    # -- the numbers (MaskSettings) ----------------------------------------------------------------------------

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self.threshold_spin.setValue(settings.relative_threshold_fraction * 100.0)
            self.profile_sigma_spin.setValue(int(round(settings.relative_profile_sigma_px)))
            self.contrast_sigma_spin.setValue(int(round(settings.local_contrast_sigma_px)))
            self.z_spin.setValue(settings.local_contrast_z_threshold)
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: object) -> None:
        self._sync_from_settings(self._mask.settings())

    def _on_spin_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        push_tool_settings(
            self._mask,
            relative_threshold_fraction=self.threshold_spin.value() / 100.0,
            relative_profile_sigma_px=float(self.profile_sigma_spin.value()),
            local_contrast_sigma_px=float(self.contrast_sigma_spin.value()),
            local_contrast_z_threshold=self.z_spin.value(),
        )

    def _refresh_range(self, range_: object) -> None:
        if range_ is None:
            self.range_label.setText("No range yet: drag the highlighted band on the Histogram panel.")
        else:
            low, high = range_  # type: ignore[misc]
            self.range_label.setText(f"Uses the range highlighted on the Histogram panel:\n{low:,.0f} to {high:,.0f}.")


class ByValueSelector(QObject):
    """Runs a method for a click on the image (the armed tool) and combines the result into the area selection. Widget-free apart from the form
    it owns; one computation at a time (a click made while one runs is ignored: the tool tip says it is working)."""

    busy_changed = pyqtSignal(bool)

    def __init__(
        self,
        mask: MaskModule,
        geometry: GeometryModule,
        highlight_range: HighlightRangeModule,
        image_panel: ImagePanel,
        preview: MaskEditPreview,
        area_selection: AreaSelectionModule,
        status: Callable[[str], None],
        parent: QObject | None = None,
        *,
        keys: Callable[[], tuple[bool, bool]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._geometry = geometry
        self._highlight_range = highlight_range
        self._preview = preview
        self._selection = area_selection
        self._status = status
        # (shift, alt) held now; tests replace it. The real answer is the keyboard state at the click.
        self._keys = keys or self._keyboard_keys
        self._image: np.ndarray | None = None
        self._frame: tuple[int, float] | None = None
        self._busy = False
        self.form = ByValueSettingsForm(mask, highlight_range)
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    @staticmethod
    def _keyboard_keys() -> tuple[bool, bool]:
        modifiers = QGuiApplication.keyboardModifiers()
        return bool(modifiers & Qt.KeyboardModifier.ShiftModifier), bool(modifiers & Qt.KeyboardModifier.AltModifier)

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._image = image
        self._frame = (cube_index, wavelength_nm)

    def _on_image_cleared(self) -> None:
        self._image = None
        self._frame = None

    def is_busy(self) -> bool:
        return self._busy

    def run_for_mode(self, mode: AreaSelectionMode, how: str | None = None) -> None:
        """Select with the method of the armed tool `mode` (`MODE_METHODS`)."""
        self.run(MODE_METHODS[mode], how)

    def drag_axes(self, method: ValueMethod) -> list[DragAxis]:
        """The numbers a right-drag changes for `method` (`tool_drag.py`): sideways the threshold (right = a bigger region = a lower number),
        up the blur size. The Histogram range has none here (its range is the Histogram panel's)."""
        form = self.form
        if method is ValueMethod.THRESHOLD:
            return [
                DragAxis("Threshold", form.threshold_spin.value, form.threshold_spin.setValue, form.threshold_spin.minimum(),
                         form.threshold_spin.maximum(), "x", positive=False, suffix=" %"),
                DragAxis("Sigma", form.profile_sigma_spin.value, form.profile_sigma_spin.setValue, form.profile_sigma_spin.minimum(),
                         form.profile_sigma_spin.maximum(), "y", integer=True, suffix=" px"),
            ]
        if method is ValueMethod.LOCAL_CONTRAST:
            return [
                DragAxis("z", form.z_spin.value, form.z_spin.setValue, form.z_spin.minimum(), form.z_spin.maximum(), "x", positive=False),
                DragAxis("Sigma", form.contrast_sigma_spin.value, form.contrast_sigma_spin.setValue, form.contrast_sigma_spin.minimum(),
                         form.contrast_sigma_spin.maximum(), "y", integer=True, suffix=" px"),
            ]
        return []

    def run(self, method: ValueMethod, how: str | None = None) -> None:
        """Select by `method`. `how` (Replace / Add / Subtract / Intersect) defaults to the mode picker's, overridden by the keys held now
        (Shift / Alt / Shift+Alt)."""
        if how is None:
            shift, alt = self._keys()
            how = how_for_keys(shift, alt, self._selection.combine_mode())
        if self._busy:
            return
        if self._image is None or self._frame is None:
            self._status("Select by value: no image on screen.")
            return
        if method is ValueMethod.HISTOGRAM:
            self._run_histogram(how)
        else:
            self._run_candidate(method, how)

    def _run_histogram(self, how: str) -> None:
        assert self._image is not None
        range_ = self._highlight_range.current_range()
        if range_ is None:
            self._status("Select by value: there is no range on the Histogram yet (drag its highlighted band).")
            return
        self._commit(histogram_highlight_region(self._image, *range_), how, ValueMethod.HISTOGRAM)

    def noun(self, method: ValueMethod) -> str:
        """How `method` is named in a sentence and on the undo step; the Threshold says which side it selects ("Threshold (darker)")."""
        if method is ValueMethod.THRESHOLD:
            return f"{_NOUN[method]} ({self.form.threshold_side()})"
        return _NOUN[method]

    def _run_candidate(self, method: ValueMethod, how: str) -> None:
        frame = self._frame
        assert frame is not None
        noun = self.noun(method)  # said now: a wheel turn while it computes must not rename what was computed
        side = self.form.threshold_side() if method is ValueMethod.THRESHOLD else raster_tools.SIDE_BOTH
        self._set_busy(True)
        self._status(f"{noun}: computing...")

        def finish(candidate: np.ndarray | None) -> None:
            self._set_busy(False)
            if candidate is None:
                self._status(f"{noun}: no pixels could be computed (see the log).")
                return
            if self._frame != frame or self._image is None:
                self._status(f"{noun}: not applied, the image changed before it finished.")
                return
            region = resolve_external_mask(candidate, self._geometry.settings(), None)
            if region is None or region.shape != self._image.shape:
                self._status(f"{noun}: not applied, the image size changed.")
                return
            self._commit(region, how, method, noun)

        self._preview.request_candidate(_KIND[method], frame, finish, side=side)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _commit(self, region: np.ndarray, how: str, method: ValueMethod, noun: str | None = None) -> None:
        """Combine `region` (displayed space) into the selection. A result with no pixel (inside the lock) changes nothing."""
        noun = noun if noun is not None else _NOUN[method]
        fence = self._selection.lock_mask(region.shape)
        count = int(np.count_nonzero(region if fence is None else region & fence))
        if count == 0:
            self._status(f"{noun}: nothing found{' inside the lock' if fence is not None else ''}; the selection is unchanged.")
            return
        changed = self._selection.combine_region(region, how, f"Select by {noun.lower()}")  # cut to the lock inside
        verb = {"add": "added", "subtract": "took away", "intersect": "kept the overlap of"}.get(how, "selected")
        log_event(logger, "selection.by_value", logging.DEBUG, method=method.value, label=noun, how=how, area_px=count, changed=changed)
        self._status(f"{noun}: {verb} {count:,} px." if changed else f"{noun}: the selection is unchanged.")
