"""Help -> Keyboard shortcuts (F1): every key and mouse gesture of the app in one searchable list (2026-10-30).

Design record: ``docs/keyboard_shortcut_registry_design_2026-10-30.md``. Read only: the rows come from ``shortcuts.py``, the same
table that binds the keys, so the list cannot say something the app does not do. A non-modal window, one instance kept by
``app_rewrite._wire_shortcuts_dialog``.

- A search box narrows the list (action text, keys, section and "where"); a search expands what it finds, and an empty
  search brings back the sections the user had collapsed.
- "Copy as text" puts the whole table on the clipboard (for notes or a bug report); "Close" or Esc closes it.
- The size and the collapsed sections are remembered (``ui_state``); the search text is not.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont, QGuiApplication, QHideEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme

from ..shortcuts import all_bindings, display, sections, shortcuts_as_text
from ..storage.ui_state_keys import SHORTCUTS_DIALOG_COLLAPSED, SHORTCUTS_DIALOG_SIZE
from .ui_state import UiStateStore

DEFAULT_SIZE = (760, 540)
_MIN_SIZE = (420, 260)
_COPIED_MS = 1500
_ACTION, _KEYS, _WHERE = 0, 1, 2


class ShortcutsDialog(QDialog):
    def __init__(self, ui_state: UiStateStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setModal(False)
        self._store = ui_state
        self._collapsed = self._saved_collapsed()
        self._filtering = False  # True while a search opens and closes sections itself

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search actions or keys")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search shortcuts")

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Action", "Keys", "Where"])
        self.tree.setRootIsDecorated(True)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_ACTION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_KEYS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_WHERE, QHeaderView.ResizeMode.ResizeToContents)

        # Shown in place of the tree when the search finds nothing.
        self.empty_label = QLabel(self)
        self.empty_label.setStyleSheet(f"color: {get_active_theme().text_dim};")
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()

        self.copy_button = QPushButton("Copy as text", self)
        self.close_button = QPushButton("Close", self)
        self.close_button.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_label, 1)
        layout.addLayout(buttons)

        self._section_items: dict[str, QTreeWidgetItem] = {}
        self._rows: list[tuple[QTreeWidgetItem, QTreeWidgetItem, str]] = []  # (section item, row item, search text)
        self._populate()

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(lambda: self.copy_button.setText("Copy as text"))

        self.search.textChanged.connect(self._apply_filter)
        self.copy_button.clicked.connect(self._copy_as_text)
        self.close_button.clicked.connect(self.close)
        self.tree.itemCollapsed.connect(self._on_section_toggled)
        self.tree.itemExpanded.connect(self._on_section_toggled)
        self.resize(*self._saved_size())

    # -- content ----------------------------------------------------------------------------------

    def _populate(self) -> None:
        bold = QFont(self.font())
        bold.setBold(True)
        for section in sections():
            parent_item = QTreeWidgetItem(self.tree, [section])
            parent_item.setFont(_ACTION, bold)
            self._section_items[section] = parent_item
        for item in all_bindings():
            parent_item = self._section_items[item.section]
            keys = display(item.id)
            row = QTreeWidgetItem(parent_item, [item.label, keys, item.scope])
            for column in (_ACTION, _KEYS, _WHERE):
                row.setToolTip(column, f"{item.label}\n{keys}\n{item.scope}")
            self._rows.append((parent_item, row, f"{item.label} {keys} {item.scope} {item.section}".lower()))
        for section, parent_item in self._section_items.items():
            parent_item.setExpanded(section not in self._collapsed)

    def row_count(self) -> int:
        """How many binding rows the list holds, shown or not (a test compares it with the table)."""
        return len(self._rows)

    def visible_row_count(self) -> int:
        return sum(1 for _section, row, _text in self._rows if not row.isHidden())

    # -- search -----------------------------------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        self._filtering = True
        shown_in: set[str] = set()
        for section_item, row, haystack in self._rows:
            match = not needle or needle in haystack
            row.setHidden(not match)
            if match:
                shown_in.add(section_item.text(_ACTION))
        for section, section_item in self._section_items.items():
            section_item.setHidden(section not in shown_in)
            if needle:
                section_item.setExpanded(section in shown_in)  # a search opens what it found (the saved state is not touched)
            else:
                section_item.setExpanded(section not in self._collapsed)
        self._filtering = False
        nothing = bool(needle) and not shown_in
        self.tree.setHidden(nothing)
        self.empty_label.setVisible(nothing)
        self.empty_label.setText(f"No shortcut matches '{text.strip()}'.")

    def _on_section_toggled(self, item: QTreeWidgetItem) -> None:
        if self._filtering or self.search.text().strip():  # opened or closed by a search, not by the user
            return
        section = item.text(_ACTION)
        if item.isExpanded():
            self._collapsed.discard(section)
        else:
            self._collapsed.add(section)
        if self._store is not None:
            self._store.set(SHORTCUTS_DIALOG_COLLAPSED, sorted(self._collapsed))

    # -- copy -------------------------------------------------------------------------------------

    def _copy_as_text(self) -> None:
        QGuiApplication.clipboard().setText(shortcuts_as_text())
        self.copy_button.setText("Copied")
        self._copied_timer.start(_COPIED_MS)

    # -- remembered state -------------------------------------------------------------------------

    def _saved_collapsed(self) -> set[str]:
        saved = self._store.get(SHORTCUTS_DIALOG_COLLAPSED) if self._store is not None else None
        if isinstance(saved, list) and all(isinstance(name, str) for name in saved):
            return set(saved)
        return set()

    def _saved_size(self) -> tuple[int, int]:
        saved = self._store.get(SHORTCUTS_DIALOG_SIZE) if self._store is not None else None
        if (
            isinstance(saved, list) and len(saved) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in saved)
            and saved[0] >= _MIN_SIZE[0] and saved[1] >= _MIN_SIZE[1]
        ):
            return int(saved[0]), int(saved[1])
        return DEFAULT_SIZE

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt naming
        if self._store is not None:
            self._store.set(SHORTCUTS_DIALOG_SIZE, [self.width(), self.height()])
        super().hideEvent(event)

    def reset_search(self) -> None:
        """Called when the dialog is opened again: the search text is not remembered."""
        self.search.clear()
        self.search.setFocus()
