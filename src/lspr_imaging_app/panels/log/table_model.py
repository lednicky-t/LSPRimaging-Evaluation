"""``LogTableModel`` - the Log tab's table (2026-10-14).

A plain list of `LogEntry` rows. The panel appends a batch per timer tick
(`append_entries`: one `beginInsertRows`, not one per record) and trims from
the top when the row limit is reached, so a long session never grows the view
without bound. Display only: it owns no filtering (the panel hands it the
already-filtered rows).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from lspr_ui import get_active_theme

from ...applog.entry import LogEntry
from ...applog.export import clock_text

COLUMNS = ("Time", "Level", "Logger", "Message", "ms")
COL_TIME, COL_LEVEL, COL_LOGGER, COL_MESSAGE, COL_MS = range(5)
_PACKAGE_PREFIX = "lspr_imaging_app."
MAX_ROWS = 20_000


def short_logger(name: str) -> str:
    return name[len(_PACKAGE_PREFIX):] if name.startswith(_PACKAGE_PREFIX) else name


class LogTableModel(QAbstractTableModel):
    def __init__(self, max_rows: int = MAX_ROWS, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[LogEntry] = []
        self._max_rows = max_rows

    # -- data ------------------------------------------------------------------------

    def entry_at(self, row: int) -> LogEntry | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def entries(self) -> list[LogEntry]:
        return list(self._rows)

    def set_entries(self, entries: list[LogEntry]) -> None:
        self.beginResetModel()
        self._rows = list(entries[-self._max_rows:])
        self.endResetModel()

    def append_entries(self, entries: list[LogEntry]) -> None:
        if not entries:
            return
        if len(entries) >= self._max_rows:
            self.set_entries(entries)
            return
        overflow = len(self._rows) + len(entries) - self._max_rows
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self._rows[:overflow]
            self.endRemoveRows()
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(entries) - 1)
        self._rows.extend(entries)
        self.endInsertRows()

    def clear(self) -> None:
        self.set_entries([])

    # -- Qt model interface ----------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt naming
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt naming
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        entry = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == COL_TIME:
                return clock_text(entry)
            if column == COL_LEVEL:
                return entry.level_name
            if column == COL_LOGGER:
                return short_logger(entry.logger)
            if column == COL_MESSAGE:
                return entry.message.replace("\r", "").replace("\n", " ⏎ ")
            return "" if entry.dur_ms is None else f"{entry.dur_ms:.1f}"
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(self._level_color(entry.level))
        if role == Qt.ItemDataRole.TextAlignmentRole and column == COL_MS:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and column in (COL_LOGGER, COL_MESSAGE):
            return entry.logger if column == COL_LOGGER else entry.message[:2000]
        return None

    @staticmethod
    def _level_color(level: int) -> str:
        theme = get_active_theme()
        if level >= logging.ERROR:
            return theme.accent_red
        if level >= logging.WARNING:
            return theme.accent_gold
        if level <= logging.DEBUG:
            return theme.text_dim
        return theme.text_primary

    def refresh_theme(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(COLUMNS) - 1))
