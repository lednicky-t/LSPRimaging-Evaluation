"""The ROI / Groups table's step columns and the Sensorgram's plan-bar steps are one selection (2026-11-26).

Selecting step columns by clicking their headers (`SubscriptHeader.column_clicked`) says which steps were chosen (`selected`); the app selects them on the plan bar. Selecting steps
on the bar comes back here as `set_selected` (the table's columns are selected without it being announced again) and `reveal` (scrolls the table to the step's column). The app
does the wiring (`app_rewrite._follow_plan_bar`); this file knows only columns and step references, never the bar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import QObject, pyqtSignal

from .column_selection import is_step_key
from .rows import COLUMN_COUNT
from .view import RoiTreeView


class StepLink(QObject):
    selected = pyqtSignal(object)
    """The user selected columns by clicking headers: a tuple of the references of the step columns among them, left to right (empty: no step column selected)."""

    def __init__(self, parent: QObject, tree: RoiTreeView, visible: Callable[[], bool]) -> None:
        super().__init__(parent)
        self._tree = tree
        self._visible = visible  # the table is on screen (its dock is not closed or behind a tab)
        tree.column_selection.changed.connect(lambda keys: self.selected.emit(tuple(k for k in keys if is_step_key(k))))

    def set_selected(self, refs: Iterable[object]) -> None:
        """Select exactly these steps' columns (the plan bar's selection), leaving a selected fixed or comparison column as it is. Not announced through `selected`."""
        self._tree.column_selection.set_step_keys(refs)

    def reveal(self, ref: object) -> None:
        """Scroll the table sideways to the column of the step `ref`, to the middle of the view. Display only: nothing while the table is hidden (it is not worth moving a view nobody sees; it opens where it
        was), when the step columns are off, or when that column is hidden."""
        if not self._visible():
            return
        model = self._tree.model()
        for position, column in enumerate(model.step_columns() if model is not None else ()):
            if column.kind == "step" and column.ref == ref:
                self._tree.scroll_column_into_view(COLUMN_COUNT + position)
                return
