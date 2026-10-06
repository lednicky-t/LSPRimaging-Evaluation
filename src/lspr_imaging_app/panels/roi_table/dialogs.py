"""Small dialogs the ROI/Group table opens."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout, QWidget


class ShiftDialog(QDialog):
    """Ask how far to shift the selected ROIs, in the display unit."""

    def __init__(self, count: int, unit_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        noun = "ROI" if count == 1 else f"{count} ROIs"
        self.setWindowTitle("Shift position")
        self._dx = self._spin(unit_label)
        self._dy = self._spin(unit_label)
        form = QFormLayout()
        form.addRow("Shift x by", self._dx)
        form.addRow("Shift y by", self._dy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Move {noun} by the same amount, keeping their arrangement.", self))
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(unit_label: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-100000.0, 100000.0)
        spin.setDecimals(2)
        spin.setSingleStep(1.0)
        spin.setSuffix(f" {unit_label}")
        return spin

    def shift(self) -> tuple[float, float]:
        """``(dx, dy)`` in the display unit the dialog was opened with."""
        return self._dx.value(), self._dy.value()
