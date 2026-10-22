"""Dataset stage - the dark-background and "Ignored" indicators.

Sits under the reference-frame row (``reference_frame_row.py``), same style: dim status lines only (2026-10-22). Picking
and clearing the dark background, and ignoring wavelengths or cubes, are done in the Image panel (the slider ribbon's
Dark background buttons, ``image/frame_tools.py``, and the slider right-click menus).

- The dark line says which wavelength is the dark and that it is **unscaled** (exposure scaling is decided later). 0 nm is
  picked automatically when the dataset has it.
- The ignored summary only counts.

Display only: all state lives in ``FrameRolesModule``.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...selection import SelectionModule
from ...selection.frame_roles_module import FrameRolesModule


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class FrameRolesRows(QWidget):
    def __init__(self, frame_roles: FrameRolesModule, selection: SelectionModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._roles = frame_roles
        self._selection = selection
        theme = get_active_theme()

        self._dark_label = QLabel(self)
        self._dark_label.setWordWrap(True)
        self._ignored_label = QLabel(self)
        self._ignored_label.setWordWrap(True)
        for status in (self._dark_label, self._ignored_label):
            status.setStyleSheet(f"color: {theme.text_dim};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(self._dark_label)
        layout.addWidget(self._ignored_label)

        self._roles.roles_changed.connect(self._on_roles_changed)
        self._refresh()

    def _on_roles_changed(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        dark = self._roles.dark_wavelength()
        if dark is None:
            self._dark_label.setText("[Dark: -]")
        else:
            self._dark_label.setText(f"[Dark: {dark:g} nm - unscaled]")
        wavelengths, cubes = self._roles.ignored_wavelengths(), self._roles.ignored_cubes()
        parts = [part for part in (
            _plural(len(wavelengths), "wavelength") if wavelengths else "",
            _plural(len(cubes), "cube") if cubes else "",
        ) if part]
        self._ignored_label.setText(f"[Ignored: {', '.join(parts) if parts else 'none'}]")
        tooltip = []
        if wavelengths:
            tooltip.append("Wavelengths (all cubes): " + ", ".join(f"{w:g} nm" for w in wavelengths))
        if cubes:
            tooltip.append("Cubes: " + ", ".join(str(c) for c in cubes))
        self._ignored_label.setToolTip("\n".join(tooltip) or "Right-click a slider in the Image panel to ignore a wavelength or a cube.")
