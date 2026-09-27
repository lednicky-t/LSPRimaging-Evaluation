"""Shared "label above field" row builder for Workflow panel settings
forms (2026-09-25).

**Why not `QFormLayout`** (label left of field, one row): measured for
real (headless, at the Workflow panel's actual fixed 340px width - see
the width-budget check this module's docstring points to) that a
`QFormLayout` row whose label text is more than a couple words wide -
"Local contrast sigma", "Relative profile sigma", "Local reference
normalization" - pushes the whole form well past the available ~280-300px
a nested section actually has, well before the field itself gets any
room. Stacking the label above the field instead means the row's width is
just whichever of the two is wider, almost always the field - the same
"put it on its own row" fix the maintainer asked for directly.

See ``tests/integration/test_lspri_workflow_panel_width_budget.py`` (repo
root) for the automated check this exists to satisfy - run it (or the
LSPRi subset, which includes it) after adding any new Workflow-panel
row/section, not just when something looks visually wrong.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lspr_ui import get_active_theme


def stacked_field(label_text: str, field: QWidget, parent: QWidget | None = None) -> QWidget:
    """A small label directly above `field`, tightly spaced - the
    Workflow-panel-width-safe alternative to a `QFormLayout` row."""
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    label = QLabel(label_text, row)
    label.setStyleSheet(f"color: {get_active_theme().text_muted}; font-size: 11px;")
    layout.addWidget(label)
    layout.addWidget(field)
    return row
