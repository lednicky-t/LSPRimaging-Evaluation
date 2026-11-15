"""Names the Image panel and its builder modules share (2026-11-15; they were defined at the top of `panel.py` and are now needed by `canvas_build.py`,
`ribbon_build.py`, `navigation_build.py` and the panel, which a builder cannot import from the panel without a cycle).
"""

from __future__ import annotations

from ...image_tools import ImageTool

_REDRAW_COALESCE_MS = 100  # sketch §8 - the already-validated coalescing window
_CHUNK_GRID_COLOR = "#84cc16"  # the lime of the Export tab's grid icon (`export_tab._GRID_ON_COLOR`)
_SOURCE_GRID_COLOR = "#38bdf8"  # the blue of the View tab's grid icon (the loaded OME-Zarr's own chunks)
_CROP_OUTLINE_COLOR = "#38bdf8"  # the crop button's active blue

# Cube/Wavelength navigation rows (ported from the stable app's
# docs/image_area_slider_redesign.md). The colours of the slider marks (reference,
# dark, ...) are the user's pick or the theme's: `slider_colors.py`.


def _slider_axis_title_style(color: str) -> str:
    """Shared 11px/600 style for the Cube/λ slider-title labels - ported
    from the stable app's `layout_builder._slider_axis_title_style` so both
    titles use identical numbers instead of two hand-copied versions that
    can drift."""
    return f"color: {color}; font-size: 11px; font-weight: 600;"


MASK_TAB = "Mask"  # ribbon tab label; the edit preview (the pixels the picked tool would change) is drawn only while it is open
CHROMATIC_TAB = "Chromatic"  # ribbon tab label (full name "Chromatic Corrections" is its tooltip)
BACKGROUND_TAB = "Background"  # ribbon tab label; green while background removal is applied
ANALYSIS_TAB = "Analysis"  # the scan (moved here from the Workflow panel 2026-10-24); its "i" text is live (`info_html`)
EXPORT_TAB = "Export"  # OME-Zarr export (moved here from the Workflow panel 2026-10-20); its "i" text is live (`info_html`)
_PREVIEW_TOOLS = frozenset({ImageTool.ROTATE, ImageTool.CROP})
"""Tools that work on the *uncropped* image: while one is active the image is
rendered without its crop, the crop is drawn as an outline, and the ROI
overlay (cropped-space coordinates) is hidden. See the module docstring.
Crop joined 2026-09-29: the whole point of the tool is choosing a new crop
from the full available frame, not just the region an old crop already
kept."""
