"""Sensorgram plot widget - the pyqtgraph-facing half of the Sensorgram
panel (sketch §10 "sensorgram/panel.py + plot.py").
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import QWidget


class SensorgramPlot(QWidget):
    """Time-series metric plot. Its own redraw coalescing (§8) replaces
    today's shared "Live" toggle entirely."""

    def set_series(self, roi_id: int, timestamps: np.ndarray, values: np.ndarray) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def set_pump_plan_overlay(self, steps: object) -> None:
        """Proposed pump-plan-step overlay, from Dataset's acquisition
        metadata (sketch §7). Not yet implemented - scaffolding only."""
        raise NotImplementedError
