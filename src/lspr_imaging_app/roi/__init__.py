"""ROI Toolbox (sketch §7 "ROI Toolbox", §10).

One backend, several front-door UI surfaces - not several independent
implementations of ROI/group logic. Follows ``docs/roi_system_roadmap.md``'s
``Pair`` vocabulary and geometry-type dispatcher; adopt that roadmap, don't
re-derive a new ROI model (AGENTS.md, "Module boundaries").
"""

from .toolbox import RoiToolbox

__all__ = ["RoiToolbox"]
