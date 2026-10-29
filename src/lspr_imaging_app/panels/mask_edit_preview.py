"""Live preview of what a Mask-tab edit tool would select (2026-10-29, maintainer request: "mask editing tools are not showing the
changes that would come with applying the tool (pixels of selection)").

Histogram selection already had one (the Histogram highlight tint). This is the same for the other three tools, drawn as one more tint
on the image (`panels/image/edit_preview_layer.py`):

- **Threshold** and **Local contrast**: the pixels the tool selects with the numbers in the ribbon now - exactly the candidate
  that Add / Subtract merge into the mask (one computation serves both, `request_candidate`). It follows the frame on screen and
  re-computes a short moment after a number or the frame changes; the maths takes 0.2-4 s, so it runs on a worker thread
  (`image_tools/mask_candidates.py`) and the tint appears when it is ready.
- **Morphology**: the pixels the operation under the mouse would change (the ring a dilate / close adds, the rim an erode / open
  strips), with the radius in the ribbon. Hover, no click; a click still applies at once.
- **ROI features** (2026-10-29): the pixels of the last result of `RoiFeatureTask` (the finder runs on the Process button, 3-16 s,
  not on every change), shown while the frame on screen is the one it was made for. Nothing is computed here; Apply takes the same
  pixels (`request_candidate` answers at once from the task).

Shown only while the eye toggle is on (one state shared by the three panels, remembered), the Mask tab is the open ribbon tab,
and Play is not running (a frame every few tens of ms would only start work nobody sees).

This object is display state plus the request logic, widget-free like `mask_highlight_editor.py`; it changes no mask. Pixels are
**raw-space** booleans as authored at the displayed frame, the space `MaskModule` stores masks in; the layer maps them onto the
screen with the same call the mask overlay uses (`resolve_external_mask`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, MaskEditTool, MaskModule
from ..image_tools.mask import raster_tools
from ..image_tools.mask_candidates import MaskCandidates, Ready
from .mask_edit_common import resolve_mask_edit_base

if TYPE_CHECKING:
    from ..image_tools.roi_feature_task import RoiFeatureTask

logger = logging.getLogger(__name__)

Frame = tuple[int, float]

KIND_THRESHOLD = "relative"  # the `MaskSettings` / old-app names of the two image-based tools
KIND_LOCAL_CONTRAST = "local_contrast"
KIND_MORPHOLOGY = "morphology"
KIND_ROI_FEATURES = "roi_features"
KIND_LABELS = {
    KIND_THRESHOLD: "Threshold", KIND_LOCAL_CONTRAST: "Local contrast", KIND_MORPHOLOGY: "Morphology", KIND_ROI_FEATURES: "ROI features",
}
KIND_NOUNS = {  # how a tool's pixels are called in the middle of a sentence ("Added 40 threshold pixels to the mask")
    KIND_THRESHOLD: "threshold", KIND_LOCAL_CONTRAST: "local contrast", KIND_MORPHOLOGY: "morphology", KIND_ROI_FEATURES: "ROI feature",
}

_SETTINGS_DELAY_MS = 300  # after the last change of a number: a spin box stepped by the wheel is one request, not ten
_FRAME_DELAY_MS = 250  # after the last frame change (scrubbing a slider passes many frames)
_TOOL_KINDS = {
    MaskEditTool.THRESHOLD: KIND_THRESHOLD,
    MaskEditTool.LOCAL_CONTRAST: KIND_LOCAL_CONTRAST,
    MaskEditTool.ROI_FEATURES: KIND_ROI_FEATURES,
}


@dataclass(frozen=True)
class PreviewPixels:
    frame: Frame
    kind: str
    pixels: np.ndarray  # raw-space booleans, as authored at `frame`; read-only by convention (shared with the cache)


class MaskEditPreview(QObject):
    changed = pyqtSignal()  # the pixels to tint changed (a new set arrived, or the old one must go): the layer redraws
    shown_changed = pyqtSignal(bool)  # the eye toggle (the three tool panels mirror it)
    status = pyqtSignal(str)  # one line for the tool-info row ("Threshold preview: 1,204 px")

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        candidates: MaskCandidates | None = None,
        parent: QObject | None = None,
        *,
        features: RoiFeatureTask | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._features = features  # the ROI features tool's result (pixels it holds are previewed and merged as they are)
        self._candidates = candidates if candidates is not None else MaskCandidates(self)
        self._shown = True
        self._tab_active = False
        self._playing = False
        self._tool = MaskEditTool.HISTOGRAM_SELECTION
        self._hover: str | None = None  # the Morphology operation under the mouse
        self._frame: Frame | None = None
        self._raw_shape: tuple[int, int] | None = None
        self._result: PreviewPixels | None = None
        self._token = 0  # the newest request; an answer to an older one is dropped
        self._settings_key = self._relevant_settings()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

        mask.cosmetic_changed.connect(self._on_cosmetic_changed)
        mask.mask_changed.connect(self._on_mask_changed)
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
        self._hover = None
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

    def set_morphology_hover(self, operation: str | None) -> None:
        """The Morphology operation the mouse is over (``None`` = none): its changed pixels are shown while it is."""
        if operation == self._hover:
            return
        self._hover = operation
        self._schedule(None)  # at once: a hover is an answer to the mouse, not a typing burst

    def frame(self) -> Frame | None:
        return self._frame

    def invalidate(self) -> None:
        """The dataset was replaced or cleared: nothing computed belongs to the new one."""
        self._candidates.invalidate()
        self._raw_shape = None
        self._frame = None
        self._token += 1
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

    # -- the candidate shared with Add / Subtract ----------------------------------------------------------

    def request_candidate(self, kind: str, frame: Frame, on_ready: Ready, *, preview: bool = False) -> None:
        """The raw-space pixels Threshold (`KIND_THRESHOLD`) or Local contrast (`KIND_LOCAL_CONTRAST`) select at `frame` with the
        ribbon's numbers now. Cached; computed on a worker thread otherwise; ``None`` when it failed or went stale."""
        if kind == KIND_ROI_FEATURES:  # nothing to compute: the finder ran on its own button, the task holds what it found
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

    def _relevant_settings(self) -> tuple[float, ...]:
        s = self._mask.settings()
        return (
            s.relative_threshold_fraction,
            s.relative_profile_sigma_px,
            s.local_contrast_sigma_px,
            s.local_contrast_z_threshold,
            float(s.morphology_radius_px),
        )

    def _on_cosmetic_changed(self, _change: object) -> None:
        key = self._relevant_settings()
        if key != self._settings_key:
            self._settings_key = key
            if self._wanted() is not None:
                self._hide_result()
                self._schedule(_SETTINGS_DELAY_MS)

    def _on_mask_changed(self, _change: object) -> None:
        # Only Morphology's pixels depend on the mask (they are cut out of it); the candidates come from the raw image.
        if self._wanted() == KIND_MORPHOLOGY:
            self._schedule(0)

    def _on_dataset_replaced(self, *_args: object) -> None:
        self.invalidate()

    def _on_features_changed(self) -> None:
        # A result arrived or went away; only the ROI features tool shows it.
        if self._wanted() == KIND_ROI_FEATURES or (self._result is not None and self._result.kind == KIND_ROI_FEATURES):
            self._schedule(0)

    def _wanted(self) -> str | None:
        """Which kind of pixels would be shown now, or ``None`` for none."""
        if not self._shown or not self._tab_active or self._playing or self._frame is None:
            return None
        if self._tool in _TOOL_KINDS:
            return _TOOL_KINDS[self._tool]
        if self._tool is MaskEditTool.MORPHOLOGY and self._hover is not None:
            return KIND_MORPHOLOGY
        return None

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
        self._token += 1
        token = self._token
        if kind is None:
            self._hide_result()
            return
        frame = self._frame
        assert frame is not None
        before = self._result
        if kind == KIND_ROI_FEATURES:  # answered at once, and no status line: "no result yet" is not a failure
            pixels = None if self._features is None else self._features.pixels_for(frame)
            self._result = None if pixels is None else PreviewPixels(frame, kind, pixels)
            if before is not None or self._result is not None:
                self.changed.emit()
            return
        if before is not None and (before.frame != frame or before.kind != kind):
            self._result = None  # the old pixels describe something else; hide them while the new ones are made

        def ready(pixels: np.ndarray | None) -> None:
            if token != self._token:
                return  # something newer was asked for since
            self._result = None if pixels is None else PreviewPixels(frame, kind, pixels)
            self.changed.emit()
            if pixels is None:
                self.status.emit(f"{KIND_LABELS[kind]} preview: not available.")
            else:
                self.status.emit(f"{KIND_LABELS[kind]} preview: {int(np.count_nonzero(pixels)):,} px.")

        if kind == KIND_MORPHOLOGY:
            self._request_morphology(frame, ready)
        else:
            self.status.emit(f"Computing the {KIND_LABELS[kind].lower()} preview...")
            self.request_candidate(kind, frame, ready, preview=True)
        if before is not None and self._result is None:
            self.changed.emit()  # the stale pixels went away (a hit answers inside `request_candidate` and emits itself)

    def _request_morphology(self, frame: Frame, ready: Ready) -> None:
        operation = self._hover
        if operation is None:
            ready(None)
            return
        if self._raw_shape is None:
            self._raw_shape = self._dataset.raw_plane_shape()
        if self._raw_shape is None:
            ready(None)
            return
        radius = int(self._mask.settings().morphology_radius_px)
        base = resolve_mask_edit_base(self._mask, self._chromatic, frame, self._raw_shape)  # GUI thread: a dict lookup + a warp
        key = (KIND_MORPHOLOGY, frame, operation, radius)

        def compute() -> np.ndarray:
            return raster_tools.morphology_changed_pixels(base, operation, radius)

        self._candidates.request(key, compute, ready, preview=True, cache=False)
