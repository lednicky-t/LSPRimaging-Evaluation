"""Live preview of what the Mask tab's Feature map tool would add, and the candidate pixels of Threshold / Local contrast (2026-10-29,
reduced 2026-11-15).

History: on 2026-10-29 (maintainer: "mask editing tools are not showing the changes that would come with applying the tool") Threshold,
Local contrast and Morphology got a live tint of the pixels they would change. On 2026-11-15 those tools (with the Histogram selection
and Draw) moved into the selection toolbar and became *selection* tools: the selection is undoable, so there is no preview (the wand's
decision of 2026-11-15) and the three previews went. What remains here:

- **Feature map** (2026-11-04; was ROI features): the enabled kinds of the last result of `FeatureMapTask` (the map runs on its Process
  button, ~20-30 s, not on every change), shown while the frame on screen is the one it was made for. Nothing is computed here; Apply
  takes the same pixels (`request_candidate` answers at once from the task). Drawn as one more tint on the image
  (`panels/image/edit_preview_layer.py`).
- **`request_candidate`**: the raw-space pixels Threshold or Local contrast select at a frame with the numbers in `MaskSettings` now,
  cached and computed on a worker thread (`image_tools/mask_candidates.py`). The toolbar's "Select by value" asks it
  (`panels/image/selection_by_value.py`); it is kept here because that cache is this object's.

The tint is shown only while the eye toggle is on (one state shared by the Feature map panel, remembered), the Mask ribbon tab is the open
one, the Feature map tool is picked, and Play is not running.

Display state plus the request logic, widget-free like `mask_candidate_editor.py`; it changes no mask. Pixels are **raw-space** booleans
as authored at the displayed frame, the space `MaskModule` stores masks in; the layer maps them onto the screen with the same call the
mask overlay uses (`resolve_external_mask`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, MaskEditTool, MaskModule
from ..image_tools.mask import raster_tools
from ..image_tools.mask_candidates import MaskCandidates, Ready

if TYPE_CHECKING:
    from .feature_map_task import FeatureMapTask

Frame = tuple[int, float]

KIND_THRESHOLD = "relative"  # the `MaskSettings` / old-app names of the two image-based tools
KIND_LOCAL_CONTRAST = "local_contrast"
KIND_FEATURE_MAP = "feature_map"
KIND_LABELS = {KIND_THRESHOLD: "Threshold", KIND_LOCAL_CONTRAST: "Local contrast", KIND_FEATURE_MAP: "Feature map"}
KIND_NOUNS = {  # how a tool's pixels are called in the middle of a sentence ("Added 40 feature map pixels to the mask")
    KIND_THRESHOLD: "threshold", KIND_LOCAL_CONTRAST: "local contrast", KIND_FEATURE_MAP: "feature map",
}

_FRAME_DELAY_MS = 250  # after the last frame change (scrubbing a slider passes many frames)
_TOOL_KINDS = {MaskEditTool.FEATURE_MAP: KIND_FEATURE_MAP}


@dataclass(frozen=True)
class PreviewPixels:
    frame: Frame
    kind: str
    pixels: np.ndarray  # raw-space booleans, as authored at `frame`; read-only by convention (shared with the cache)


class MaskEditPreview(QObject):
    changed = pyqtSignal()  # the pixels to tint changed (a new set arrived, or the old one must go): the layer redraws
    shown_changed = pyqtSignal(bool)  # the eye toggle (the Feature map panel mirrors it)
    status = pyqtSignal(str)  # one line for the tool-info row

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        candidates: MaskCandidates | None = None,
        parent: QObject | None = None,
        *,
        features: FeatureMapTask | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._features = features  # the Feature map tool's result (the enabled kinds' pixels are previewed and merged as they are)
        self._candidates = candidates if candidates is not None else MaskCandidates(self)
        self._shown = True
        self._tab_active = False
        self._playing = False
        self._tool = MaskEditTool.FEATURE_MAP
        self._frame: Frame | None = None
        self._result: PreviewPixels | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

        dataset.dataset_loaded.connect(self._on_dataset_replaced)
        dataset.dataset_cleared.connect(self._on_dataset_replaced)
        if features is not None:
            features.result_changed.connect(self._on_features_changed)

    # -- inputs -------------------------------------------------------------------------------------------

    def shown(self) -> bool:
        return self._shown

    def set_shown(self, shown: bool) -> None:
        shown = bool(shown)
        if shown == self._shown:
            return
        self._shown = shown
        self.shown_changed.emit(shown)
        self._schedule(0)

    def set_tool(self, tool: MaskEditTool) -> None:
        if tool is self._tool:
            return
        self._tool = tool
        self._schedule(0)

    def set_tab_active(self, active: bool) -> None:
        """The Mask ribbon tab is the open one (the preview is a Mask-tab aid: it must not sit over the image while the ROIs are
        being edited)."""
        if bool(active) != self._tab_active:
            self._tab_active = bool(active)
            self._schedule(0)

    def set_playing(self, playing: bool) -> None:
        if bool(playing) != self._playing:
            self._playing = bool(playing)
            self._schedule(0 if playing else _FRAME_DELAY_MS)

    def set_frame(self, frame: Frame | None) -> None:
        """The frame on screen (O(1): called for every Play step)."""
        if frame == self._frame:
            return
        self._frame = frame
        if self._result is not None and self._result.frame != frame:
            self._result = None  # it described another frame
            self.changed.emit()
        if self._wanted() is not None:
            self._schedule(_FRAME_DELAY_MS)

    def frame(self) -> Frame | None:
        return self._frame

    def invalidate(self) -> None:
        """The dataset was replaced or cleared: nothing computed belongs to the new one."""
        self._candidates.invalidate()
        self._frame = None
        if self._result is not None:
            self._result = None
            self.changed.emit()

    def shutdown(self) -> None:
        self._candidates.shutdown()

    # -- outputs ------------------------------------------------------------------------------------------

    def current(self) -> PreviewPixels | None:
        """The pixels to tint now, or ``None`` (nothing wanted, not ready yet, or they describe another frame)."""
        result = self._result
        if result is None or result.frame != self._frame or self._wanted() != result.kind:
            return None
        return result

    # -- the candidate shared with Add / Subtract and "Select by value" -----------------------------------

    def request_candidate(self, kind: str, frame: Frame, on_ready: Ready, *, preview: bool = False) -> None:
        """The raw-space pixels Threshold (`KIND_THRESHOLD`) or Local contrast (`KIND_LOCAL_CONTRAST`) select at `frame` with the
        numbers in `MaskSettings` now (the Feature map's, `KIND_FEATURE_MAP`, are the task's result). Cached; computed on a worker
        thread otherwise; ``None`` when it failed or went stale."""
        if kind == KIND_FEATURE_MAP:  # nothing to compute: the map ran on its own button, the task holds what it found
            on_ready(None if self._features is None else self._features.pixels_for(frame))
            return
        settings = self._mask.settings()
        cube, wavelength = int(frame[0]), float(frame[1])
        dataset = self._dataset
        if kind == KIND_THRESHOLD:
            sigma, fraction = float(settings.relative_profile_sigma_px), float(settings.relative_threshold_fraction)
            key = (kind, cube, wavelength, sigma, fraction)

            def compute() -> np.ndarray:
                plane = dataset.load_plane_uncached(cube, wavelength)
                return raster_tools.create_relative_contrast_mask(plane, sigma_px=sigma, threshold_fraction=fraction)

        elif kind == KIND_LOCAL_CONTRAST:
            sigma, z_threshold = float(settings.local_contrast_sigma_px), float(settings.local_contrast_z_threshold)
            key = (kind, cube, wavelength, sigma, z_threshold)

            def compute() -> np.ndarray:
                plane = dataset.load_plane_uncached(cube, wavelength)
                return raster_tools.create_local_contrast_mask(plane, sigma_px=sigma, z_threshold=z_threshold)

        else:
            raise ValueError(f"no candidate for tool kind {kind!r}")
        self._candidates.request(key, compute, on_ready, preview=preview)

    # -- reacting -----------------------------------------------------------------------------------------

    def _on_dataset_replaced(self, *_args: object) -> None:
        self.invalidate()

    def _on_features_changed(self) -> None:
        # A result arrived or went away, or the enabled kinds changed; only the Feature map tool shows it.
        if self._wanted() == KIND_FEATURE_MAP or (self._result is not None and self._result.kind == KIND_FEATURE_MAP):
            self._schedule(0)

    def _wanted(self) -> str | None:
        """Which kind of pixels would be shown now, or ``None`` for none."""
        if not self._shown or not self._tab_active or self._playing or self._frame is None:
            return None
        return _TOOL_KINDS.get(self._tool)

    def _hide_result(self) -> None:
        if self._result is not None:
            self._result = None
            self.changed.emit()

    def _schedule(self, delay_ms: int | None) -> None:
        """Look at what is wanted after `delay_ms` (``None`` = right now, ``0`` = next turn of the event loop)."""
        if delay_ms is None:
            self._refresh()
            return
        self._timer.start(delay_ms)

    def _refresh(self) -> None:
        self._timer.stop()
        kind = self._wanted()
        if kind is None:
            self._hide_result()
            return
        frame = self._frame
        assert frame is not None
        before = self._result
        # Only the Feature map is previewed: answered at once, and no status line ("no result yet" is not a failure).
        pixels = None if self._features is None else self._features.pixels_for(frame)
        self._result = None if pixels is None else PreviewPixels(frame, kind, pixels)
        if before is not None or self._result is not None:
            self.changed.emit()
