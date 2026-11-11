"""Dataset Manager -> "Sessions" page, and the session label shared with File -> Session.

**Create + switch only** (the deliberate scope of `CLAUDE.md`'s "Sessions" section): no rename, duplicate or delete.
Display only: it owns no session state, it shows `SessionCoordinator.sessions_changed` and calls the coordinator's
`switch_to` / `create_new` commands. Was the Workflow panel's session picker row until that panel was retired
(2026-11-11).
"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from ...storage.session_coordinator import SessionCoordinator
from ...storage.session_index import SessionRecord


def session_label(record: SessionRecord) -> str:
    """A session's id is a UTC timestamp (`storage/session_index.py`), shown here in the local timezone, since that is
    what one reads off a clock. Falls back to the raw id if `created_at_utc` is unparsable, rather than raising over a
    label."""
    if record.label:
        return record.label
    try:
        created = datetime.fromisoformat(record.created_at_utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return "Session - " + created.astimezone().strftime("%b %d, %H:%M")
    except ValueError:
        return f"Session {record.session_id}"


class SessionsPage(QWidget):
    """A list of the dataset's sessions (the active one in bold) + Open and New Session buttons. Double-click opens."""

    def __init__(self, coordinator: SessionCoordinator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._coordinator = coordinator

        note = QLabel(
            "A session holds the ROIs, masks, settings and analysis results made over this dataset. "
            "The dataset itself is never changed.",
            self,
        )
        note.setWordWrap(True)
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self.open_button = QPushButton("Open", self)
        self.open_button.setToolTip("Make the selected session the active one.")
        self.open_button.clicked.connect(self._open_selected)
        self.new_button = QPushButton("New Session", self)
        self.new_button.setToolTip("Start a new, blank session over this dataset.")
        self.new_button.clicked.connect(self._on_new_clicked)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.new_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(note)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)

        coordinator.sessions_changed.connect(self._on_sessions_changed)
        self._on_sessions_changed(coordinator.sessions(), coordinator.active_session_id())

    def _on_sessions_changed(self, sessions: tuple[SessionRecord, ...], active_id: str | None) -> None:
        self.list.clear()
        for record in sessions:
            active = record.session_id == active_id
            item = QListWidgetItem(session_label(record) + ("   (active)" if active else ""))
            item.setData(Qt.ItemDataRole.UserRole, record.session_id)
            if active:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
            if active:
                self.list.setCurrentItem(item)
        self.new_button.setEnabled(self._coordinator.home() is not None)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        item = self.list.currentItem()
        session_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self.open_button.setEnabled(session_id is not None and session_id != self._coordinator.active_session_id())

    def _open_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id is not None and session_id != self._coordinator.active_session_id():
            self._coordinator.switch_to(session_id)

    def _on_new_clicked(self) -> None:
        if self._coordinator.home() is not None:
            self._coordinator.create_new()
