"""Histogram plot settings dialog - opened from the plot's corner gear icon
(maintainer's spec, 2026-09-29: "implement in this plot plot setting, you
can find similar ones in stable app for sensogram/spectra... a settings
icon in top right corner").

Modeled on the stable app's `gui/plot_style_settings_dialog.py` (a `QDialog`
opened from a corner gear icon), but **live-apply rather than Ok/Apply/
Cancel**: every control here already applied immediately when it lived in
the plot's toolbar row before this dialog existed, so keeping that
immediacy is less surprising than introducing a "Cancel reverts everything"
model this one dialog alone would have. Non-modal (`.show()`, not `.exec()`)
so the plot - and the Highlight region on it - stays interactive while the
dialog is open.

Deliberately small today (axis mode, scale, bin size, one line-width
control for all curves): the maintainer's own framing was "line graphical
settings, like line width, and other things which will come later" - room
to grow, not a spec for a finished settings surface.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QPushButton, QSpinBox, QVBoxLayout, QWidget

# Combo index <-> `HistogramPanel._y_mode` string, shared with `panel.py` and
# the double-click-to-cycle handler in `plot.py` - one ordered list so all
# three stay in sync by construction rather than three separate 0/1/2
# mappings that could drift.
Y_MODES = ("percent", "counts", "normalized")
_Y_MODE_LABELS = {"percent": "Percent of total", "counts": "Counts", "normalized": "Normalized (peak = 1)"}

# Non-uniform bin-size step (maintainer's spec, 2026-09-30): a single
# up/down click should feel proportional to the current value - 1 DN at a
# time near the small end would take forever to reach a useful bin size
# near the large end, and a coarse step near the small end would overshoot
# past the values that matter most there. Each entry is (bracket ceiling,
# step size used once the current value has *reached* that ceiling) - e.g.
# stepping up from 10 itself already uses the next bracket's step (5, to
# 15), matching the maintainer's own phrasing ("1..10 by one, then by 5
# until 50": 10 is the last value stepped *to* by 1, not stepped *from*).
# The last entry's step applies to everything at or above the second-to-
# last ceiling too.
_BIN_STEP_TABLE = ((10, 1), (50, 5), (100, 10), (250, 25), (500, 50))
_BIN_STEP_ABOVE_TABLE = 50


class _SteppedBinSizeSpinBox(QSpinBox):
    """`QSpinBox` whose arrow/wheel step size follows `_BIN_STEP_TABLE`
    instead of a single fixed `singleStep` - see the table's own comment
    for why. Typing a value directly is unaffected; this only changes what
    one click of the up/down arrows (or one wheel notch) adds or
    subtracts."""

    def stepBy(self, steps: int) -> None:
        step = self._step_for(self.value())
        self.setValue(self.value() + steps * step)

    @staticmethod
    def _step_for(value: int) -> int:
        for ceiling, step in _BIN_STEP_TABLE:
            if value < ceiling:
                return step
        return _BIN_STEP_ABOVE_TABLE


class HistogramPlotSettingsDialog(QDialog):
    """Axis, binning, and line-style controls for the Histogram plot."""

    def __init__(
        self,
        *,
        y_mode: str,
        log_y: bool,
        bin_width: int,
        line_width: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Histogram settings")
        self.setModal(False)

        self.axis_mode_combo = QComboBox(self)
        self.axis_mode_combo.addItems([_Y_MODE_LABELS[mode] for mode in Y_MODES])
        self.axis_mode_combo.setCurrentIndex(Y_MODES.index(y_mode))

        self.scale_combo = QComboBox(self)
        self.scale_combo.addItems(["Linear", "Log"])
        self.scale_combo.setCurrentIndex(1 if log_y else 0)

        self.bin_spin = _SteppedBinSizeSpinBox(self)
        self.bin_spin.setRange(1, 8192)
        self.bin_spin.setSuffix(" DN")
        self.bin_spin.setValue(bin_width)

        self.line_width_spin = QDoubleSpinBox(self)
        self.line_width_spin.setRange(0.5, 10.0)
        self.line_width_spin.setSingleStep(0.5)
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setValue(line_width)

        form = QFormLayout()
        form.addRow("Y-axis:", self.axis_mode_combo)
        form.addRow("Scale:", self.scale_combo)
        form.addRow("Bin size:", self.bin_spin)
        form.addRow("Line width:", self.line_width_spin)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(close_button)
