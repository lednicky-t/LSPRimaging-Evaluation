"""Selection / Navigation module (sketch §7 "Selection / Navigation").

The one genuinely cross-cutting piece of shared state (current spectral
cube/wavelength, current ROI selection) - an intentional, acknowledged
exception to "nothing is shared" (AGENTS.md, "Module boundaries"), not
license to add more shared state elsewhere.
"""

from .module import SelectionModule

__all__ = ["SelectionModule"]
