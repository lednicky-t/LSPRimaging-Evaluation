"""Image Tools stage (sketch §7 "Image Tools", §10).

Four independent sub-modules - Geometry, Mask, Chromatic, Background - each
owning one narrow slice of ``PreprocessingSettings``, matching the
maintainer's "crop/rotate/mask/CC/background all belong to the Image Tools
stage, each independent and well-documented" framing. There is no single
"ImageToolsModule" god class wrapping the four; other modules depend on
whichever sub-module they actually need.
"""

from .background import BackgroundModule
from .chromatic import ChromaticModule
from .geometry import GeometryModule
from .mask import MaskModule

__all__ = ["GeometryModule", "MaskModule", "ChromaticModule", "BackgroundModule"]
