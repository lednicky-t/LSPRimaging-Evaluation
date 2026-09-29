"""Selection / Navigation module (sketch §7 "Selection / Navigation").

The one genuinely cross-cutting piece of shared state (current spectral
cube/wavelength, current ROI selection) - an intentional, acknowledged
exception to "nothing is shared" (AGENTS.md, "Module boundaries"), not
license to add more shared state elsewhere.

``ReferenceFrameModule`` (2026-09-25) lives in this package too - not part
of the original sketch, added because "which frame is the reference"
needed a new, small owner (see that module's own docstring for why it
isn't `SelectionModule` itself or an existing module).

``HighlightRangeModule`` (2026-09-29) is the same pattern applied a second
time: the Histogram panel's intensity-range selection, needed independently
by Mask and ROI Toolbox - see its own docstring for why it is not
Histogram-specific pub/sub.
"""

from .highlight_range_module import HighlightRangeModule
from .module import SelectionModule
from .reference_frame_module import ReferenceFrameModule

__all__ = ["HighlightRangeModule", "SelectionModule", "ReferenceFrameModule"]
