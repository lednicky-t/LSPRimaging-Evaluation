"""Add / Subtract for the Mask tab's Threshold and Local contrast tools (2026-10-29; their buttons were disabled until then).

Widget-free, like `mask_highlight_editor.py` (the Histogram selection's Add / Subtract, which this follows): it asks
`MaskEditPreview.request_candidate` for the pixels the tool selects on the frame on screen - the very pixels the preview tints, from
the same cache when the preview already made them - and merges them into the mask at the scope `MaskScopeModule` says
(`MaskModule.apply_candidate`: Add ORs them in, Subtract takes them out).

**Two things differ from the Histogram selection** because the candidate takes 0.2-4 s to make: it is computed on a worker thread, so
`apply` returns at once and the merge happens when the pixels arrive (the caller disables its buttons meanwhile via ``done``); and
the merge is dropped, with a message, if the frame on screen changed meanwhile (the pixels belong to the frame they were made for;
the old app did the same).

**Area selection**: pixels outside it are never touched, as for Morphology and the Histogram selection - the candidate is cut to the
selection (raw space, `ImagePanel.selection_raw_mask`) before it is merged.

The mask in effect at the frame is resolved when the pixels arrive, not when the button is pressed, so a stroke made in between is
kept.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from ..dataset import DatasetModule
from ..image_tools import ChromaticModule, MaskModule, MaskScopeModule
from .mask_edit_common import resolve_mask_edit_base
from .mask_edit_preview import KIND_LABELS, KIND_NOUNS, KIND_ROI_FEATURES, MaskEditPreview

if TYPE_CHECKING:
    # Deferred, as in mask_highlight_editor.py: a top-level import would close an import cycle.
    from .image.panel import ImagePanel


class MaskCandidateEditor:
    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        preview: MaskEditPreview,
        status: Callable[[str], None],
    ) -> None:
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._image_panel = image_panel
        self._mask_scope = mask_scope
        self._preview = preview
        self._status = status
        self._last_frame: tuple[int, float] | None = None
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)

    def is_ready(self) -> bool:
        return self._last_frame is not None

    def frame(self) -> tuple[int, float] | None:
        """The frame on screen (what Apply would merge into)."""
        return self._last_frame

    def _on_image_rendered(self, _image: object, cube_index: int, wavelength_nm: float) -> None:
        self._last_frame = (cube_index, wavelength_nm)

    def _on_image_cleared(self) -> None:
        self._last_frame = None

    def apply(self, kind: str, *, subtract: bool, done: Callable[[], None]) -> None:
        """Start Add (``subtract=False``) or Subtract. ``done`` is called once, on the GUI thread, when the merge was made or
        given up."""
        frame = self._last_frame
        raw_shape = self._dataset.raw_plane_shape()
        if frame is None or raw_shape is None:
            done()
            return
        if kind != KIND_ROI_FEATURES:  # the features are already found (the Process button); nothing is computed for Apply
            self._status(f"Computing the {KIND_NOUNS[kind]} mask...")

        def finish(candidate: np.ndarray | None) -> None:
            try:
                self._finish(kind, frame, raw_shape, subtract, candidate)
            finally:
                done()

        self._preview.request_candidate(kind, frame, finish)

    def _finish(
        self, kind: str, frame: tuple[int, float], raw_shape: tuple[int, int], subtract: bool, candidate: np.ndarray | None
    ) -> None:
        label = KIND_LABELS[kind]
        noun = KIND_NOUNS[kind]
        if candidate is None:
            if kind == KIND_ROI_FEATURES:
                self._status("ROI features: nothing to add for this image. Press Process first (a change of the ROIs or the image clears the result).")
            else:
                self._status(f"{label} mask: no pixels could be computed (see the log).")
            return
        if self._last_frame != frame:
            self._status(f"{label} mask not applied: the image changed before it finished.")
            return
        if candidate.shape != tuple(raw_shape):
            self._status(f"{label} mask not applied: the image size changed.")
            return
        region = self._image_panel.selection_raw_mask(raw_shape)
        if region is not None:
            candidate = candidate & region
        base_mask = resolve_mask_edit_base(self._mask, self._chromatic, frame, raw_shape)
        self._mask.apply_candidate(
            base_mask, candidate, target_frame=frame, scope=self._mask_scope.scope().value, subtract=subtract
        )
        count = int(np.count_nonzero(candidate))
        verb = f"Removed {count:,} {noun} pixels from" if subtract else f"Added {count:,} {noun} pixels to"
        self._status(f"{verb} the mask.")
