"""Dataset stage - the "Define reference frame:" row.

Replaces the stable app's standalone "Reference" section (`gui/
layout_builder.py`'s `reference_section`) with a compact, non-collapsible
row instead - maintainer's explicit call, 2026-09-25: the feature doesn't
need its own accordion entry. Sits in the Dataset section's top-level
content, alongside the folder row (`dataset_folder_row.py`) - both are
"always visible regardless of which nested section is open" controls, not
inside any one of them.

Real icon toggle buttons this time (not free-standing labels - the source
uses checkable `QToolButton`s here, in a `QButtonGroup`, matching
`reference_auto_button`/`reference_manual_button` exactly): "Find reference"
(Auto) = tabler `focus-2`, "Set reference" (Manual) = tabler `pin` (renamed and
re-iconed 2026-10-11), both lime `#84cc16` when active
- same literal color the source hardcodes, no `lspr_ui` theme token for it.

Backed by the new `ReferenceFrameModule` + the existing `SelectionModule`
(see `reference_frame_module.py`'s docstring: Auto = best-contrast wavelength
of the viewed cube, found in the background by `ReferenceAutoFind`).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ...selection import CubeDisplayModule, ReferenceFrameModule, SelectionModule
from ...selection.reference_frame_module import MODE_AUTO, MODE_MANUAL
from ..dock_container import _render_tabler_icon

logger = logging.getLogger(__name__)

_ACTIVE_COLOR = "#84cc16"


class ReferenceFrameRow(QWidget):
    def __init__(
        self,
        reference_frame: ReferenceFrameModule,
        selection: SelectionModule,
        parent: QWidget | None = None,
        cube_display: CubeDisplayModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._reference_frame_module = reference_frame
        self._selection_module = selection
        self._cube_display = cube_display
        theme = get_active_theme()

        label = QLabel("Define reference frame:", self)

        self._auto_button = QToolButton(self)
        self._auto_button.setCheckable(True)
        self._auto_button.setAutoRaise(True)
        self._auto_button.setFixedSize(24, 24)
        self._auto_button.setIconSize(QSize(18, 18))
        self._auto_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._auto_button.setToolTip(
            "Find reference: for the cube being viewed, pick the wavelength with the best contrast (kept until you "
            "change cube; it does not follow the wavelength slider)."
        )
        self._auto_button.clicked.connect(lambda: self._reference_frame_module.set_mode(MODE_AUTO))

        self._manual_button = QToolButton(self)
        self._manual_button.setCheckable(True)
        self._manual_button.setAutoRaise(True)
        self._manual_button.setFixedSize(24, 24)
        self._manual_button.setIconSize(QSize(18, 18))
        self._manual_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._manual_button.setToolTip("Set reference: store the current spectral cube and wavelength as the reference frame.")
        self._manual_button.clicked.connect(self._on_manual_clicked)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._auto_button)
        self._mode_group.addButton(self._manual_button)

        self._status_label = QLabel("[Ref.frame: -]", self)
        self._status_label.setStyleSheet(f"color: {theme.text_dim};")
        # Word-wrap, not just a bounded number format (2026-09-25) - a
        # dataset with a 4+ digit spectral-cube index still overflows even
        # at 1-decimal wavelength precision (measured ~384px for "Cube
        # 9999, WL 505.3"). Wrapping is the robust fix - it grows down
        # instead of sideways regardless of how wide the numbers get,
        # rather than chasing another "is this text short enough" bound.
        self._status_label.setWordWrap(True)

        # Title + both icons share one row (2026-09-27, maintainer's
        # explicit call) - re-measured for real at the Workflow panel's
        # fixed 340px width and it fits within the 320px budget (see
        # test_lspri_workflow_panel_width_budget.py), unlike an earlier
        # 2026-09-25 measurement that put the label alone at ~276px; that
        # number doesn't reproduce against the actual themed widget.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(label)
        title_row.addWidget(self._auto_button)
        title_row.addWidget(self._manual_button)
        title_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addLayout(title_row)
        layout.addWidget(self._status_label)

        self._reference_frame_module.reference_frame_changed.connect(self._refresh)
        self._selection_module.cube_changed.connect(self._on_selection_changed)
        self._selection_module.wavelength_changed.connect(self._on_selection_changed)
        if cube_display is not None:
            # The cube unit (number / start time) is app-wide; re-read the label when it changes.
            cube_display.mode_changed.connect(lambda _mode: self._refresh())
        self._refresh()

    def _on_manual_clicked(self) -> None:
        self._reference_frame_module.set_manual_frame(
            self._selection_module.current_cube(), self._selection_module.current_wavelength()
        )

    def _on_selection_changed(self, _value: object) -> None:
        # Only the cube matters, and only in Auto (Find reference is per
        # cube); Set reference shows the stored snapshot whatever is viewed.
        if self._reference_frame_module.mode() == MODE_AUTO:
            self._refresh()

    def _refresh(self) -> None:
        mode = self._reference_frame_module.mode()
        self._auto_button.setChecked(mode == MODE_AUTO)
        self._manual_button.setChecked(mode == MODE_MANUAL)
        self._auto_button.setIcon(_render_tabler_icon("focus-2", _ACTIVE_COLOR if mode == MODE_AUTO else get_active_theme().text_primary))
        self._manual_button.setIcon(
            _render_tabler_icon("pin", _ACTIVE_COLOR if mode == MODE_MANUAL else get_active_theme().text_primary)
        )
        frame = self._reference_frame_module.resolve(self._selection_module.current_cube())
        cube, wavelength = frame if frame is not None else (None, None)
        if cube is None:
            pending = mode == MODE_AUTO and bool(self._selection_module.current_cube() is not None)
            self._status_label.setText("[Ref.frame: finding...]" if pending else "[Ref.frame: -]")
        else:
            # Bounded precision (2026-09-25) - `:g` on a wavelength with
            # float imprecision (e.g. 505.333333...) measured ~384px wide,
            # over budget; one decimal keeps the worst case short.
            cube_text = self._cube_display.label_for(cube) if self._cube_display is not None else str(cube)
            self._status_label.setText(f"[Ref.frame: Cube {cube_text}, WL {wavelength:.1f}]")
