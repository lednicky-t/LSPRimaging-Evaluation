"""Spectra plot widget - the pyqtgraph-facing half of the Spectra panel
(sketch §10 "spectra/panel.py + plot.py").

Split out from ``panel.py`` so the plotting/rendering surface is isolated
from event wiring, matching the sketch's own ``panel.py + plot.py`` split
for both Spectra and Sensorgram.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import QWidget


class SpectraPlot(QWidget):
    """Owns its own fit-curve display and range tools. Never computes a fit
    for "live preview" on the GUI thread (AGENTS.md non-negotiable
    invariant) - reads already-computed values only."""

    def set_spectrum(self, roi_id: int, wavelengths: np.ndarray, values: np.ndarray) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def set_fit_curve(self, roi_id: int, wavelengths: np.ndarray, fitted_values: np.ndarray) -> None:
        """Reads an already-computed fit; never computes one itself. Not
        yet implemented - scaffolding only."""
        raise NotImplementedError
