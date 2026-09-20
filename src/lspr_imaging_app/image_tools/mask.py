"""``MaskModule`` - algorithmic + raster file mask (sketch §7 "Image Tools", §10).

Owns algorithmic mask settings and the raster file-mask. Emits
``mask_changed`` (computational). Masks are authored and stored in raw pixel
space (AGENTS.md non-negotiable invariant) - this module calls
:meth:`~lspr_imaging_app.image_tools.chromatic.module.ChromaticModule.warp_mask`
to forward-transform into processed/wavelength space at each use site; it
never does the reverse.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented


class MaskModule(QObject):
    """Owns the algorithmic + file-based exclusion mask, in raw pixel space."""

    mask_changed = pyqtSignal()  # computational - TODO: payload shape (see change_events.py)

    @instrumented("MaskModule.set_algorithmic_mask_settings")
    def set_algorithmic_mask_settings(self, *args: object, **kwargs: object) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("MaskModule.set_file_mask")
    def set_file_mask(self, mask: object) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def raw_mask(self) -> object:
        """Return the current mask in raw pixel space. Not yet implemented -
        scaffolding only."""
        raise NotImplementedError
