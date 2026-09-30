"""``GuidedValueSpinBox`` - ported verbatim from the stable app's
``gui/widgets.py`` for the wavelength jump field in the rewrite's Image
panel (see ``docs/image_area_slider_redesign.md``, "Wavelength jump field").
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDoubleSpinBox, QWidget


class GuidedValueSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox for jumping to one of a fixed, non-contiguous set of
    real values (e.g. dataset wavelengths) via a QCompleter attached to its
    line edit. The suffix (" nm") is hidden while the field has focus and
    restored once it doesn't: QCompleter matches its popup against the raw
    line-edit text, and a persistent suffix would make "5" as typed compare
    as "5 nm" against a completion entry like "550.00 nm" - never a real
    prefix match - so the popup would never filter correctly with the
    suffix left in place. Purely a display trick: stored value/decimals/
    stepping are unaffected either way."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._display_suffix = ""

    def setSuffix(self, suffix: str) -> None:  # type: ignore[override]
        self._display_suffix = suffix
        if not self.hasFocus():
            super().setSuffix(suffix)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().setSuffix("")
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        super().setSuffix(self._display_suffix)
