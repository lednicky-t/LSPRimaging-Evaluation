"""Session picker row - switch between named sessions for the loaded
dataset, or start a new one (2026-09-26).

**Create + switch only** - this pass's deliberate scope cut (see
`AGENTS.md`'s "Sessions" section): no rename, duplicate, or delete control
here yet, even though `storage/session_index.py`'s format already has
somewhere for a rename to land later.

Display only, same rule every other Workflow row here follows: it owns no
session state itself, it just shows `SessionCoordinator`'s
`sessions_changed` signal and calls straight into its `switch_to`/
`create_new` commands - the coordinator is the one place that actually
decides which session is active."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from ...storage.session_coordinator import SessionCoordinator
from ...storage.session_index import SessionRecord
from ..dock_container import _render_tabler_icon
from .free_standing import make_free_standing_icon_label

logger = logging.getLogger(__name__)


def _format_session_label(record: SessionRecord) -> str:
    """A session's id is a UTC timestamp (`storage/session_index.py`) -
    shown in the local timezone here, since that is what the maintainer
    actually reads off a clock. Falls back to the raw id if `created_at_utc`
    is somehow unparsable, rather than raising over a cosmetic label."""
    if record.label:
        return record.label
    try:
        created = datetime.fromisoformat(record.created_at_utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return "Session - " + created.astimezone().strftime("%b %d, %H:%M")
    except ValueError:
        return f"Session {record.session_id}"


class SessionPickerRow(QWidget):
    """A session combo box + "New Session" icon button. Both stay disabled
    until a dataset (and therefore at least one session) exists."""

    def __init__(self, coordinator: SessionCoordinator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        self._updating = False

        self._combo = QComboBox(self)
        self._combo.setEnabled(False)
        self._combo.setToolTip("Which session's ROIs, masks, and settings are active for this dataset.")
        self._combo.currentIndexChanged.connect(self._on_combo_changed)

        self._new_icon = make_free_standing_icon_label(
            _render_tabler_icon("square-rounded-plus", "#38bdf8"),
            "Start a new, blank session over this dataset.",
            parent=self,
        )
        self._new_icon.setEnabled(False)
        self._new_icon.clicked.connect(self._on_new_clicked)

        row = QHBoxLayout(self)
        # Matches DatasetFolderRow/ReferenceFrameRow's own left/right margin
        # (4px) - those are this row's siblings in the Dataset section's
        # top-level stack (panel.py's dataset_inner_layout), so a 0-margin
        # row here left the combo box starting 4px further left than the
        # folder field/reference-frame row above and below it.
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
        row.addWidget(self._combo, 1)
        row.addWidget(self._new_icon)

        coordinator.sessions_changed.connect(self._on_sessions_changed)

    def _on_sessions_changed(self, sessions: tuple[SessionRecord, ...], active_id: str | None) -> None:
        # Guard against re-entering _on_combo_changed while rebuilding the
        # combo's contents - `setCurrentIndex` below fires
        # `currentIndexChanged` just like a user click would.
        self._updating = True
        try:
            self._combo.clear()
            for record in sessions:
                self._combo.addItem(_format_session_label(record), record.session_id)
            if active_id is not None:
                index = self._combo.findData(active_id)
                if index >= 0:
                    self._combo.setCurrentIndex(index)
            self._combo.setEnabled(bool(sessions))
            self._new_icon.setEnabled(self._coordinator.home() is not None)
        finally:
            self._updating = False

    def _on_combo_changed(self, index: int) -> None:
        if self._updating or index < 0:
            return
        session_id = self._combo.itemData(index)
        if session_id is None or session_id == self._coordinator.active_session_id():
            return
        self._coordinator.switch_to(session_id)

    def _on_new_clicked(self) -> None:
        if self._coordinator.home() is None:
            return
        self._coordinator.create_new()
