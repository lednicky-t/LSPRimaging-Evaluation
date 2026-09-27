"""``ElidingLabel`` - a status/message ``QLabel`` that can never force the
Workflow panel wider than its fixed budget, no matter what text it's given.

**Why ``setWordWrap(True)`` alone isn't enough.** Word-wrap only breaks at
whitespace: a Windows path ("C:\\Users\\...\\long_folder_name.ome.zarr") or
an ``OSError`` message with a path baked into it has no break point at all,
so Qt just keeps reporting the label's ``minimumSizeHint()`` at the text's
full unwrapped width regardless of the widget's actual constrained width -
confirmed by measurement: a word-wrapped label holding a real export
destination path measured ``minimumSizeHint()`` of 768px against a 320px
budget. That oversized minimum size hint propagates straight up through the
section's layout, the scroll area's content widget, and out past the fixed-
width dock - the exact "controls outside the Workflow panel" symptom seen
twice now (2026-09-27): once via ``DatasetSummarySection``'s "Source
folder" field, and again via ``DatasetExportSection``'s status label after
an export finishes and reports its full destination path. Both are the same
underlying gotcha, not two unrelated bugs.

Use this in place of a plain ``QLabel`` for any label whose text comes from
outside this module's control - a filesystem path, an exception message, a
progress string from a worker - where nothing here can bound its length or
guarantee it will contain spaces to wrap at. Keep plain ``QLabel`` +
``setWordWrap(True)`` for text this module composes itself from known-short,
naturally-spaced pieces (stat counts, "Cube N, WL X" readouts) - wrapping
works fine there and elision would throw away information for no reason.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

# The Workflow panel is a real fixed-width dock (340px, see
# panels/dock_container.py's `fixed_width` + the 320px layout budget in
# tests/integration/test_lspri_workflow_panel_width_budget.py). This is
# narrower than that budget on purpose - contents margins and the section's
# own indentation already eat into the 320px before a status label ever
# gets it, so eliding against the full budget would still overflow once
# those are accounted for.
_MAX_TEXT_WIDTH_PX = 290


class ElidingLabel(QLabel):
    """A ``QLabel`` whose displayed text is always elided to fit the
    Workflow panel's width budget - the full text is preserved as a
    tooltip, so nothing is actually lost, just not all shown inline."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if text:
            self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - overriding Qt's own camelCase API
        self.setToolTip(text)
        elided = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, _MAX_TEXT_WIDTH_PX)
        super().setText(elided)
