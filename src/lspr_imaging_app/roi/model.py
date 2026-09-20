"""ROI dataclasses (sketch §7 "ROI Toolbox", §10).

TODO: adopt ``docs/roi_system_roadmap.md``'s ``Pair`` vocabulary
(sample/reference linkage) and geometry-type dispatcher here rather than
re-deriving a new model - this is a placeholder shape only, not final.
Current definitions (``AreaRoi``/``AreaRoiGroup``/``RoiArrayGroup``) live in
``lspr_imaging_app/domain/models.py`` on the ``develop``/``main`` branches.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AreaRoi:
    """A single ROI. TODO: port fields from ``domain/models.py`` and the
    roadmap's ``Pair`` vocabulary."""

    roi_id: int


@dataclass(frozen=True)
class AreaRoiGroup:
    """A named, colored group of ROIs. TODO: port fields from
    ``domain/models.py``."""

    group_id: int
    roi_ids: tuple[int, ...]


@dataclass(frozen=True)
class RoiArrayGroup:
    """A regular-array ROI-generation recipe. TODO: port fields from
    ``domain/models.py``."""
