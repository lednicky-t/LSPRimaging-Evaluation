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


class HistogramPlotSettingsDialog(QDialog):
    """Axis, binning, and line-style controls for the Histogram plot."""

    def __init__(
        self,
        *,
        percent_mode: bool,
        log_y: bool,
        bin_width: int,
        line_width: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Histogram settings")
        self.setModal(False)

        self.axis_mode_combo = QComboBox(self)
        self.axis_mode_combo.addItems(["Percent of total", "Counts"])
        self.axis_mode_combo.setCurrentIndex(0 if percent_mode else 1)

        self.scale_combo = QComboBox(self)
        self.scale_combo.addItems(["Linear", "Log"])
        self.scale_combo.setCurrentIndex(1 if log_y else 0)

        self.bin_spin = QSpinBox(self)
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
