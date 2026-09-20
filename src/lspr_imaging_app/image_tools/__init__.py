"""Image Tools stage (sketch §7 "Image Tools", §10).

Four independent sub-modules - Geometry, Mask, Chromatic, Background - each
owning its own settings dataclass (``GeometrySettings``/``MaskSettings``/
``ChromaticSettings``/``BackgroundSettings``), matching the maintainer's
"crop/rotate/mask/CC/background all belong to the Image Tools stage, each
independent and well-documented" framing. There is no single
"ImageToolsModule" god class wrapping the four; other modules depend on
whichever sub-module they actually need.

The old app's single ``PreprocessingSettings`` (domain/models.py on
``develop``/``main``) mixed all four concerns' fields into one dataclass;
it was decomposed across these four sub-modules' own ``model.py`` files
during the 2026-09-20 ``preprocess.py`` port - see the rewrite build log's
"preprocess.py scope-check" entry for the field-by-field reasoning.
"""

from .background import BackgroundModule
from .chromatic import ChromaticModule
from .geometry import GeometryModule
from .mask import MaskModule

__all__ = ["GeometryModule", "MaskModule", "ChromaticModule", "BackgroundModule"]
