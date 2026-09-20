"""Chromatic model dataclasses (sketch §7 "Chromatic", §10).

Placeholder shapes - TODO: mirror ``processing/chromatic.py``'s current
model/landmark representation when this module is actually built.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LandmarkObservation:
    """One observed landmark pairing used to fit a :class:`ChromaticModel`.
    TODO: port fields from ``processing/chromatic.py``."""


@dataclass(frozen=True)
class ChromaticModel:
    """A fitted chromatic-correction model. Confirmed (sketch §7, 2026-09-20):
    the fitted coefficients - not just the settings/landmarks that produced
    them - must be captured into a cell's provenance record whenever this
    model is used to compute a value, so an export is self-contained. TODO:
    port fields from ``processing/chromatic.py``."""
