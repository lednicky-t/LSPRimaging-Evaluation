"""Dataset stage - the "Dark background:" row and the "Ignored" summary.

Sits under the reference-frame row (``reference_frame_row.py``), same style: a
title with two small icon buttons, then a dim status line.

- **Pick** (moon icon): make the wavelength shown now (the Image panel's
  wavelength) the dark background - the same "store what is shown" gesture as
  "Set reference". **Clear** (x icon): no dark frame.
- The status line says which wavelength is the dark and that it is **unscaled**
  (exposure scaling is decided later). 0 nm is picked automatically when the
  dataset has it.
- The ignored summary only counts; ignoring itself is done from the Image
  panel's slider right-click menus.

Display and gestures only: all state lives in ``FrameRolesModule``.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ...selection import SelectionModule
from ...selection.frame_roles_module import FrameRolesModule
from ..dock_container import _render_tabler_icon


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class FrameRolesRows(QWidget):
    def __init__(self, frame_roles: FrameRolesModule, selection: SelectionModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._roles = frame_roles
        self._selection = selection
        self._message: str | None = None  # last refused pick, shown until the next change
        theme = get_active_theme()

        label = QLabel("Dark background:", self)
        self._pick_button = self._icon_button(
            "Pick: use the wavelength shown now as the dark background (the LED-off frame; subtracted later, unscaled)."
        )
        self._pick_button.clicked.connect(self._on_pick)
        self._clear_button = self._icon_button("Clear: no dark background.")
        self._clear_button.clicked.connect(lambda _checked=False: self._roles.clear_dark())

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(label)
        title_row.addWidget(self._pick_button)
        title_row.addWidget(self._clear_button)
        title_row.addStretch(1)

        self._dark_label = QLabel(self)
        self._dark_label.setWordWrap(True)
        self._ignored_label = QLabel(self)
        self._ignored_label.setWordWrap(True)
        for status in (self._dark_label, self._ignored_label):
            status.setStyleSheet(f"color: {theme.text_dim};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addLayout(title_row)
        layout.addWidget(self._dark_label)
        layout.addWidget(self._ignored_label)

        self._roles.roles_changed.connect(self._on_roles_changed)
        self._refresh()

    def _icon_button(self, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setFixedSize(24, 24)
        button.setIconSize(QSize(18, 18))
        button.setStyleSheet(transparent_icon_button_stylesheet())
        button.setToolTip(tooltip)
        return button

    def _on_pick(self) -> None:
        try:
            self._roles.set_dark(self._selection.current_wavelength())
        except ValueError as exc:
            self._message = str(exc)
            self._refresh()

    def _on_roles_changed(self) -> None:
        self._message = None
        self._refresh()

    def _refresh(self) -> None:
        theme = get_active_theme()
        dark = self._roles.dark_wavelength()
        self._pick_button.setIcon(_render_tabler_icon("moon", theme.accent_gold if dark is not None else theme.text_primary))
        self._clear_button.setIcon(_render_tabler_icon("x", theme.text_primary))
        self._clear_button.setEnabled(dark is not None)
        if self._message:
            self._dark_label.setText(f"[Dark: {self._message}]")
        elif dark is None:
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
