"""Dataset stage - the reference-frame indicator row.

Replaces the stable app's standalone "Reference" section (`gui/
layout_builder.py`'s `reference_section`) with a compact, non-collapsible
row instead - maintainer's explicit call, 2026-09-25: the feature doesn't
need its own accordion entry. Sits in the Dataset section's top-level
content, alongside the folder row (`dataset_folder_row.py`) - both are
"always visible regardless of which nested section is open" controls, not
inside any one of them.

An indicator only (2026-10-22): the Set / Find reference buttons that used to sit here live in the Image panel's
slider ribbon (`image/frame_tools.py`), so this row just shows which frame is the reference.

Backed by the new `ReferenceFrameModule` + the existing `SelectionModule`
(see `reference_frame_module.py`'s docstring).
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...selection import CubeDisplayModule, ReferenceFrameModule, SelectionModule

logger = logging.getLogger(__name__)


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

        self._status_label = QLabel("[Ref.frame: -]", self)
        self._status_label.setStyleSheet(f"color: {theme.text_dim};")
        # Word-wrap, not just a bounded number format (2026-09-25) - a
        # dataset with a 4+ digit spectral-cube index still overflows even
        # at 1-decimal wavelength precision (measured ~384px for "Cube
        # 9999, WL 505.3"). Wrapping is the robust fix - it grows down
        # instead of sideways regardless of how wide the numbers get,
        # rather than chasing another "is this text short enough" bound.
        self._status_label.setWordWrap(True)
        self._status_label.setToolTip(
            "The reference frame. Set or find it with the Reference buttons in the Image panel's slider ribbon."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(self._status_label)

        self._reference_frame_module.reference_frame_changed.connect(self._refresh)
        self._selection_module.cube_changed.connect(self._on_selection_changed)
        self._selection_module.wavelength_changed.connect(self._on_selection_changed)
        if cube_display is not None:
            # The cube unit (number / start time) is app-wide; re-read the label when it changes.
            cube_display.mode_changed.connect(lambda _mode: self._refresh())
        self._refresh()

    def _on_selection_changed(self, _value: object) -> None:
        # The reference is one fixed frame; nothing to redraw when the view moves.
        return

    def _refresh(self) -> None:
        frame = self._reference_frame_module.resolve(self._selection_module.current_cube())
        cube, wavelength = frame if frame is not None else (None, None)
        if cube is None:
            self._status_label.setText("[Ref.frame: -]")
        else:
            # Bounded precision (2026-09-25) - `:g` on a wavelength with
            # float imprecision (e.g. 505.333333...) measured ~384px wide,
            # over budget; one decimal keeps the worst case short.
            cube_text = self._cube_display.label_for(cube) if self._cube_display is not None else str(cube)
            self._status_label.setText(f"[Ref.frame: Cube {cube_text}, WL {wavelength:.1f}]")
