"""Dataset Manager -> Experiment plan: the refractive indices of the experiment's solutions (2026-11-12; editable since 2026-11-13).

Screen purpose: see which refractive index (RI) every step uses, and correct it when the file is wrong or incomplete, with every change marked and reversible.

Layout (one block on the page): a title; a line saying where the table came from and what you changed; the table; a line for a refused edit; the notes; the buttons.
- **The table** shows the *working table* (`plan/refractive_index.py`): one row per solution (RI, spread, temperature, N, the day it was measured, that measurement's age at the
  experiment day: amber above the Preferences limit, default 7 days; the steps it serves; a status). Under the rows of the table come one **placeholder row** for each step
  solution that has no row at all (its RI cell is empty: type a value to make it a row) and the imported rows you removed (greyed and struck out: *Revert row* brings one back).
- **Editing**: double-click a cell (or select it and press F2) to type. Solution, RI, ± Std, Temp, N, Measured and Steps are editable; Age and Status are not. Steps: the
  step numbers as written there (``1, 3-5, 9``): the row then serves exactly those steps (by hand); empty gives the row back to its name (the step's note starts with the name).
  A refused value (an RI outside 1.0 - 3.0, a name already used, a step already given to another row, a date that is not DD.MM.YYYY) changes nothing and says why under the table.
- **Marking**: a value you changed is bold and gold with the imported value in its tool tip; the Status column says ``edited``, ``added``, ``steps by hand`` or ``no RI``.
  Rows no step uses are dimmed (they stay in the table). Add row, Remove row, Revert row (an added row is removed) and Revert all are the buttons below; every change is
  one step of Edit -> Undo. Importing a file again replaces the table and the edits (the menu asks first).
Important states: no table (nothing imported, nothing typed: the table starts when you add a row); a table typed by hand (no file); a file in the folder that changed since the import.

Display and commands only: the module (`ExperimentPlanModule`) holds the table and validates; this view redraws only while visible (on `ri_changed` / `steps_changed`, and once
when shown) and computes nothing but the age.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lspr_ui import get_active_theme

from ...plan.refractive_index import HAND_SOURCE_NAME, RiEditError, age_days, step_numbers_text
from ...selection.experiment_plan_module import ExperimentPlanModule

COLUMNS = ("Solution", "RI", "± Std", "Temp (°C)", "N", "Measured", "Age (days)", "Steps", "Status")
COL_NAME, COL_RI, COL_STD, COL_TEMP, COL_N, COL_DATE, COL_AGE, COL_STEPS, COL_STATUS = range(len(COLUMNS))
FIELD_OF_COLUMN = {COL_NAME: "name", COL_RI: "ri", COL_STD: "std", COL_TEMP: "temp_c", COL_N: "n", COL_DATE: "measured_on"}
COLUMN_OF_FIELD = {**{field: column for column, field in FIELD_OF_COLUMN.items()}, "steps": COL_STEPS}

KIND_ROW, KIND_MISSING, KIND_REMOVED = "row", "missing", "removed"  # a row of the working table; a step solution with no row; an imported row the user removed

STEPS_TIP = "The steps this row serves. Type step numbers (1, 3-5, 9) to give it exactly those steps by hand; empty = the steps whose note starts with this name."


def _fmt(value: float | None, decimals: int) -> str:
    return "" if value is None else f"{value:.{decimals}f}"


@dataclass(frozen=True)
class _Entry:
    kind: str
    index: int | None = None  # KIND_ROW: the row's index in the working table
    name: str = ""  # KIND_MISSING: the solution; KIND_REMOVED: the imported name
    base: str | None = None  # KIND_REMOVED: the imported row's key


class RefractiveIndexView(QWidget):
    def __init__(self, plan: ExperimentPlanModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan = plan
        self._age_warning_days = 7
        self._stale = True
        self._refreshing = False
        self._in_commit = False
        self._needs_redraw = False  # a command ran from this table: the cells show what was typed until the table is drawn again from the module
        self._entries: list[_Entry] = []

        self.title_label = QLabel("Refractive indices", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        self.source_label = QLabel(self)
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.edits_label = QLabel(self)
        self.edits_label.setWordWrap(True)
        self.changed_label = QLabel(self)
        self.changed_label.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        self.notes_label = QLabel(self)
        self.notes_label.setWordWrap(True)

        self.add_button = QPushButton("Add row", self)
        self.add_button.setObjectName("ri_add_row")
        self.add_button.setToolTip("Add a blank row: type its name, then its RI. (A step solution with no row has a row of its own at the end of the table: type its RI there.)")
        self.remove_button = QPushButton("Remove row", self)
        self.remove_button.setObjectName("ri_remove_row")
        self.remove_button.setToolTip("Take the selected row out of the table. An imported row can be brought back with Revert row.")
        self.revert_button = QPushButton("Revert row", self)
        self.revert_button.setObjectName("ri_revert_row")
        self.revert_button.setToolTip("The selected row back as imported (an added row is removed; a removed row comes back).")
        self.revert_all_button = QPushButton("Revert all", self)
        self.revert_all_button.setObjectName("ri_revert_all")
        self.revert_all_button.setToolTip("Every edit undone: the table as imported.")
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for button in (self.add_button, self.remove_button, self.revert_button, self.revert_all_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(4)
        for widget in (self.title_label, self.source_label, self.edits_label, self.changed_label, self.table, self.error_label, self.notes_label):
            layout.addWidget(widget)
        layout.addLayout(buttons)

        plan.ri_changed.connect(self._mark_stale)
        plan.steps_changed.connect(lambda _reason: self._mark_stale())
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.add_button.clicked.connect(self._add_row)
        self.remove_button.clicked.connect(self._remove_row)
        self.revert_button.clicked.connect(self._revert_row)
        self.revert_all_button.clicked.connect(self._revert_all)

    def set_age_warning_days(self, days: int) -> None:
        self._age_warning_days = int(days)
        self._mark_stale()

    def showEvent(self, event: QShowEvent | None) -> None:  # Qt override: catch up once when shown
        super().showEvent(event)
        if self._stale:
            self.refresh()

    def _mark_stale(self) -> None:
        self._stale = True
        if self.isVisible() and not self._in_commit:  # a commit from this table redraws it afterwards (the table is not rebuilt inside its own signal)
            self.refresh()

    # -- drawing -----------------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._stale = False
        self._needs_redraw = False
        selected = self._selected_entry()
        self._refreshing = True
        try:
            self._draw()
        finally:
            self._refreshing = False
        self._reselect(selected)
        self._update_buttons()

    def _draw(self) -> None:
        theme = get_active_theme()
        gold, dim = QBrush(QColor(theme.accent_gold)), QBrush(QColor(theme.text_dim))
        table = self._plan.ri_table()
        self.changed_label.setVisible(self._plan.ri_file_changed())
        self.changed_label.setText(
            "RIs.txt in the dataset folder has changed since these values were imported. The imported values are still used; "
            "File -> Import -> Refractive Indices... takes the new ones" + (" (and replaces your edits)." if table is not None and table.has_edits() else ".")
        )
        self.changed_label.setStyleSheet(f"color: {theme.accent_gold};")
        usage = self._plan.ri_usage()
        rows = () if table is None else table.working()
        experiment_day = self._plan.experiment_day()

        entries = [_Entry(KIND_ROW, index) for index in range(len(rows))]
        entries += [_Entry(KIND_MISSING, name=name) for name, _steps in usage.unlisted]
        entries += [_Entry(KIND_REMOVED, name=row.name, base=row.base) for row in (() if table is None else table.removed_rows())]
        self._entries = entries

        if table is None:
            self.source_label.setText(
                "None imported. File -> Import -> Refractive Indices..., or put RIs.txt into the dataset folder (it is imported when the session opens), "
                "or type the values in the table."
            )
        else:
            wavelength = "not given (Edit -> Preferences)" if table.wavelength_nm is None else f"{table.wavelength_nm:g} nm"
            origin = (
                f"Entered by hand, started {table.imported_at_utc.replace('T', ' ').replace('Z', ' UTC')}. " if table.source_name == HAND_SOURCE_NAME
                else f"From {table.source_name}, imported {table.imported_at_utc.replace('T', ' ').replace('Z', ' UTC')}. "
            )
            self.source_label.setText(
                origin + f"Refractometer wavelength: {wavelength}. " + ("" if experiment_day is None else f"Experiment day: {experiment_day:%d.%m.%Y}.")
            )
        edited = table is not None and table.has_edits()
        self.edits_label.setVisible(edited)
        if table is not None and edited:
            self.edits_label.setText(
                f"You changed this table: {table.edit_summary()}. Changed values are marked and are the ones used in the calculations; the imported values are kept "
                "(Revert row / Revert all)."
            )
            self.edits_label.setStyleSheet(f"color: {theme.accent_gold};")

        self.table.setVisible(bool(entries))
        self.table.setRowCount(len(entries))
        for position, entry in enumerate(entries):
            if entry.kind == KIND_ROW:
                self._draw_row(position, entry, table, usage, experiment_day, gold, dim)
            elif entry.kind == KIND_MISSING:
                self._draw_missing(position, entry, usage, gold)
            else:
                self._draw_removed(position, entry, table, dim)
        self.table.setFixedHeight(
            self.table.horizontalHeader().height() + sum(self.table.rowHeight(i) for i in range(self.table.rowCount())) + 4
        )
        notes: list[str] = []
        if usage.missing:
            notes.append("No RI for: " + "; ".join(f"{name} (step {step_numbers_text(steps)})" for name, steps in usage.missing) + ".")
        if not entries and not usage.missing:
            notes.append("This dataset has no experiment steps, so no solution is matched yet.")
        self.notes_label.setText("\n".join(notes))
        self.notes_label.setStyleSheet(f"color: {theme.accent_gold if usage.missing else theme.text_dim};")

    def _item(self, text: str, editable: bool, right: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if editable else flags)
        if right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _draw_row(self, position, entry, table, usage, experiment_day, gold, dim) -> None:
        row = table.working()[entry.index]
        steps = usage.row_steps[entry.index] if entry.index < len(usage.row_steps) else ()
        status = table.status(row)
        changed = set(table.changed_fields(row))
        age = age_days(row.measured_on, experiment_day)
        notes = []
        if status:
            notes.append(status)
        if row.steps is not None:
            notes.append("steps by hand")
        if row.ri is None:
            notes.append("no RI")
        cells = {
            COL_NAME: row.name, COL_RI: _fmt(row.ri, 5), COL_STD: _fmt(row.std, 6), COL_TEMP: _fmt(row.temp_c, 1), COL_N: "" if row.n is None else str(row.n),
            COL_DATE: "" if row.measured_on is None else f"{row.measured_on:%d.%m.%Y}", COL_AGE: "-" if age is None else str(age),
            COL_STEPS: step_numbers_text(steps), COL_STATUS: ", ".join(dict.fromkeys(notes)),
        }
        imported = table.imported_text(row)
        for column in range(len(COLUMNS)):
            item = self._item(cells[column], editable=column not in (COL_AGE, COL_STATUS), right=column in (COL_RI, COL_STD, COL_TEMP, COL_N, COL_AGE))
            if not steps:
                item.setForeground(dim)  # nothing uses this row: dimmed, but still there
            field = next((name for name, col in COLUMN_OF_FIELD.items() if col == column), None)
            added_value = row.base is None and column in COLUMN_OF_FIELD.values() and bool(cells[column])
            if added_value or field in changed:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(gold)
                if row.base is None:
                    item.setToolTip("Added by you: it is not in the imported file.")
                elif field == "steps":
                    item.setToolTip("Assigned to these steps by hand" + (f" (as imported: {imported})." if imported else ".") + " Empty the cell to match by name again.")
                else:
                    item.setToolTip(f"Changed by you. As imported: {imported}.")
            elif column == COL_STEPS:
                item.setToolTip(STEPS_TIP)
            if column == COL_AGE and age is not None and (age > self._age_warning_days or age < 0):
                item.setForeground(gold)
                item.setToolTip(
                    f"Measured {age} days before the experiment (limit: {self._age_warning_days} days, Edit -> Preferences)."
                    if age >= 0 else f"Measured {-age} days after the experiment."
                )
            self.table.setItem(position, column, item)

    def _draw_missing(self, position, entry, usage, gold) -> None:
        steps = next((numbers for name, numbers in usage.unlisted if name == entry.name), ())
        cells = {COL_NAME: entry.name, COL_STEPS: step_numbers_text(steps), COL_STATUS: "no RI"}
        for column in range(len(COLUMNS)):
            item = self._item(cells.get(column, ""), editable=column == COL_RI, right=column in (COL_RI, COL_STD, COL_TEMP, COL_N, COL_AGE))
            item.setForeground(gold)
            if column == COL_RI:
                item.setToolTip(f"Type the RI of {entry.name} here: it becomes a row of the table, used by its steps.")
            self.table.setItem(position, column, item)

    def _draw_removed(self, position, entry, table, dim) -> None:
        was = next((row for row in table.imported_rows() if row.base == entry.base), None)
        cells = {COL_NAME: entry.name, COL_STATUS: "removed"}
        if was is not None:
            cells.update({COL_RI: _fmt(was.ri, 5), COL_STD: _fmt(was.std, 6), COL_TEMP: _fmt(was.temp_c, 1), COL_N: "" if was.n is None else str(was.n),
                          COL_DATE: "" if was.measured_on is None else f"{was.measured_on:%d.%m.%Y}"})
        for column in range(len(COLUMNS)):
            item = self._item(cells.get(column, ""), editable=False, right=column in (COL_RI, COL_STD, COL_TEMP, COL_N, COL_AGE))
            item.setForeground(dim)
            font = item.font()
            font.setStrikeOut(True)
            item.setFont(font)
            item.setToolTip("Removed by you from the table. Revert row brings it back.")
            self.table.setItem(position, column, item)

    # -- selection and buttons ---------------------------------------------------------------------------------------

    def _selected_entry(self) -> _Entry | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() is not None else []
        if not rows:
            return None
        position = rows[0].row()
        return self._entries[position] if 0 <= position < len(self._entries) else None

    def _reselect(self, key: _Entry | None) -> None:
        if key is None:
            return
        for position, entry in enumerate(self._entries):
            if entry == key:
                self.table.selectRow(position)
                return

    def _update_buttons(self) -> None:
        table = self._plan.ri_table()
        entry = self._selected_entry()
        self.remove_button.setEnabled(entry is not None and entry.kind == KIND_ROW)
        reversible = False
        if entry is not None and table is not None:
            if entry.kind == KIND_REMOVED:
                reversible = True
            elif entry.kind == KIND_ROW and 0 <= entry.index < len(table.working()):
                reversible = bool(table.status(table.working()[entry.index]))
        self.revert_button.setEnabled(reversible)
        self.revert_button.setText("Restore row" if entry is not None and entry.kind == KIND_REMOVED else "Revert row")
        self.revert_all_button.setEnabled(table is not None and table.has_edits())

    # -- commands ------------------------------------------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        self.error_label.setText(f"Not changed: {message}")
        self.error_label.setStyleSheet(f"color: {get_active_theme().accent_gold};")
        self.error_label.setVisible(True)

    def _run(self, action) -> bool:
        """Run a table command; a refused one says why under the table. Whatever happened, the table is drawn again from the module (the cell shows what is stored)."""
        self._in_commit = True
        try:
            action()
            self.error_label.setVisible(False)
            return True
        except RiEditError as exc:
            self._show_error(str(exc))
            return False
        finally:
            self._in_commit = False
            self._stale = self._needs_redraw = True
            QTimer.singleShot(0, self._redraw_after_command)

    def _redraw_after_command(self) -> None:
        if self._needs_redraw and self.isVisible():  # (not when something drew the table in between: Add row opens an editor that a second drawing would close)
            self.refresh()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing or not 0 <= item.row() < len(self._entries):
            return
        entry, column, text = self._entries[item.row()], item.column(), item.text()
        if entry.kind == KIND_ROW and column == COL_STEPS:
            self._run(lambda: self._plan.ri_set_steps(entry.index, text))
        elif entry.kind == KIND_ROW and column in FIELD_OF_COLUMN:
            self._run(lambda: self._plan.ri_edit(entry.index, FIELD_OF_COLUMN[column], text))
        elif entry.kind == KIND_MISSING and column == COL_RI:
            if text.strip():
                self._run(lambda: self._plan.ri_add_row_for(entry.name, text))
            else:
                self._stale = self._needs_redraw = True
                QTimer.singleShot(0, self._redraw_after_command)

    def _add_row(self) -> None:
        index = []
        if self._run(lambda: index.append(self._plan.ri_add_row())):
            self.refresh()
            position = next((p for p, entry in enumerate(self._entries) if entry.kind == KIND_ROW and entry.index == index[0]), None)
            if position is not None:
                self.table.setCurrentCell(position, COL_NAME)
                self.table.editItem(self.table.item(position, COL_NAME))

    def _remove_row(self) -> None:
        entry = self._selected_entry()
        if entry is not None and entry.kind == KIND_ROW:
            self._run(lambda: self._plan.ri_remove_row(entry.index))

    def _revert_row(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.kind == KIND_ROW:
            self._run(lambda: self._plan.ri_revert_row(entry.index))
        elif entry.kind == KIND_REMOVED and entry.base is not None:
            self._run(lambda: self._plan.ri_restore_removed(entry.base))

    def _revert_all(self) -> None:
        self._run(self._plan.ri_revert_all)
