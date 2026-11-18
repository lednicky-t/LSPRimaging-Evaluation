"""The "Rectangle size..." dialog of the ROI menu (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``).

Screen purpose: set the width, height and angle of the selected rectangle ROIs. A small modal dialog: three number boxes (width px, height px, angle degrees
clockwise on the screen) filled from the first selected rectangle, OK / Cancel. **Only the fields the user changed are applied**: with several rectangles of
different sizes selected, typing an angle turns them all and leaves every width and height as it was (a value means the same for every selection).
Validation is the toolbox's (`RoiToolbox.resize_rectangles`); its refusal reaches the status bar.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QWidget

from ...domain.roi_shapes import Rectangle
from ...roi.toolbox import MIN_SAMPLE_DIAMETER_PX

MAX_SIZE_PX = 10000.0


class RectangleSizeDialog(QDialog):
    def __init__(self, parent: QWidget, first: Rectangle, count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rectangle size" if count == 1 else f"Size of {count} rectangles")
        self._initial = (float(first.width_px), float(first.height_px), float(first.angle_deg))
        self.width_box = self._box(MIN_SAMPLE_DIAMETER_PX, MAX_SIZE_PX, self._initial[0], " px")
        self.height_box = self._box(MIN_SAMPLE_DIAMETER_PX, MAX_SIZE_PX, self._initial[1], " px")
        self.angle_box = self._box(-360.0, 360.0, self._initial[2], " deg")
        self.angle_box.setWrapping(True)
        self.angle_box.setToolTip("Turns the rectangle clockwise on the screen, about its centre.")
        form = QFormLayout(self)
        form.addRow("Width", self.width_box)
        form.addRow("Height", self.height_box)
        form.addRow("Angle (clockwise)", self.angle_box)
        if count > 1:
            form.addRow("", self._note("Only the values you change are applied; the others stay as they are."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _box(self, low: float, high: float, value: float, suffix: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setRange(low, high)
        box.setDecimals(2)
        box.setSuffix(suffix)
        box.setValue(min(max(value, low), high))
        return box

    def _note(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setWordWrap(True)
        return label

    def changed(self) -> dict[str, float]:
        """The fields whose value differs from what the dialog opened with, under the names `RoiToolbox.resize_rectangles` takes."""
        values = {"width_px": self.width_box.value(), "height_px": self.height_box.value(), "angle_deg": self.angle_box.value()}
        return {name: value for (name, value), start in zip(values.items(), self._initial, strict=True) if abs(value - start) > 1e-9}


def ask_rectangle_size(parent: QWidget, rectangles: Sequence[Rectangle]) -> dict[str, float] | None:
    """Show the dialog for ``rectangles`` and return the changed fields, or ``None`` when cancelled or nothing was changed."""
    if not rectangles:
        return None
    dialog = RectangleSizeDialog(parent, rectangles[0], len(rectangles))
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.changed() or None
