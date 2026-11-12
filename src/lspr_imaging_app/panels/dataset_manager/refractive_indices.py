"""Dataset Manager -> Experiment plan: the refractive indices of the experiment's solutions (2026-11-12).

Read-only view of `ExperimentPlanModule.ri_table()` seen through the steps (`ri_usage`): one row per solution a step
uses, with its RI, spread, temperature, the day it was measured and that measurement's age at the experiment day
(amber above the Preferences limit, default 7 days). Below the table: steps whose solution has no RI, rows of the
file no step uses, and a warning when RIs.txt in the dataset folder changed since its import. Display only: computes
nothing but the age, and redraws only while visible (on `ri_changed` / `steps_changed`, and once when shown).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QShowEvent
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...plan.refractive_index import age_days, step_numbers_text
from ...selection.experiment_plan_module import ExperimentPlanModule

COLUMNS = ("Solution", "RI", "± Std", "Temp (°C)", "N", "Measured", "Age (days)", "Steps")


def _fmt(value: float | None, decimals: int) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


class RefractiveIndexView(QWidget):
    def __init__(self, plan: ExperimentPlanModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan = plan
        self._age_warning_days = 7
        self._stale = True

        self.title_label = QLabel("Refractive indices", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        self.source_label = QLabel(self)
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.changed_label = QLabel(self)
        self.changed_label.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.notes_label = QLabel(self)
        self.notes_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(4)
        for widget in (self.title_label, self.source_label, self.changed_label, self.table, self.notes_label):
            layout.addWidget(widget)

        plan.ri_changed.connect(self._mark_stale)
        plan.steps_changed.connect(lambda _reason: self._mark_stale())

    def set_age_warning_days(self, days: int) -> None:
        self._age_warning_days = int(days)
        self._mark_stale()

    def showEvent(self, event: QShowEvent | None) -> None:  # Qt override: catch up once when shown
        super().showEvent(event)
        if self._stale:
            self.refresh()

    def _mark_stale(self) -> None:
        self._stale = True
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        self._stale = False
        theme = get_active_theme()
        table = self._plan.ri_table()
        self.changed_label.setVisible(self._plan.ri_file_changed())
        self.changed_label.setText(
            "RIs.txt in the dataset folder has changed since these values were imported. The imported values are still used; "
            "File -> Import -> Refractive Indices... takes the new ones."
        )
        self.changed_label.setStyleSheet(f"color: {theme.accent_gold};")
        if table is None:
            self.source_label.setText(
                "None imported. File -> Import -> Refractive Indices..., or put RIs.txt into the dataset folder "
                "(it is imported when the session opens)."
            )
            self.table.setRowCount(0)
            self.table.setVisible(False)
            self.notes_label.setText("")
            return
        wavelength = "not given (Edit -> Preferences)" if table.wavelength_nm is None else f"{table.wavelength_nm:g} nm"
        experiment_day = self._plan.experiment_day()
        self.source_label.setText(
            f"From {table.source_name}, imported {table.imported_at_utc.replace('T', ' ').replace('Z', ' UTC')}. "
            f"Refractometer wavelength: {wavelength}. "
            + ("" if experiment_day is None else f"Experiment day: {experiment_day:%d.%m.%Y}.")
        )
        usage = self._plan.ri_usage()
        self.table.setVisible(True)
        self.table.setRowCount(len(usage.used))
        amber = QBrush(QColor(theme.accent_gold))
        for row_index, (row, steps) in enumerate(usage.used):
            age = age_days(row.measured_on, experiment_day)
            cells = (
                row.name, _fmt(row.ri, 5), _fmt(row.std, 6), _fmt(row.temp_c, 1), "-" if row.n is None else str(row.n),
                "-" if row.measured_on is None else f"{row.measured_on:%d.%m.%Y}",
                "-" if age is None else str(age), step_numbers_text(steps),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column in (1, 2, 3, 4, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 6 and age is not None and (age > self._age_warning_days or age < 0):
                    item.setForeground(amber)
                    item.setToolTip(
                        f"Measured {age} days before the experiment (limit: {self._age_warning_days} days, Edit -> Preferences)."
                        if age >= 0 else f"Measured {-age} days after the experiment."
                    )
                self.table.setItem(row_index, column, item)
        self.table.setFixedHeight(
            self.table.horizontalHeader().height() + sum(self.table.rowHeight(i) for i in range(self.table.rowCount())) + 4
        )
        notes: list[str] = []
        if usage.missing:
            notes.append(
                "No RI for: " + "; ".join(f"{name} (step {step_numbers_text(steps)})" for name, steps in usage.missing) + "."
            )
        if usage.unused:
            notes.append(f"In the file but not used by any step: {', '.join(usage.unused)}.")
        if not usage.used and not usage.missing:
            notes.append("This dataset has no experiment steps, so no solution is matched yet.")
        self.notes_label.setText("\n".join(notes))
        self.notes_label.setStyleSheet(f"color: {theme.accent_gold if usage.missing else theme.text_dim};")
