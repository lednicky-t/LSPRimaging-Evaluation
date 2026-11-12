"""``StepColumnControls``: everything the ROI / Groups table does with its step columns, apart from drawing them (split out of ``panel.py`` 2026-11-05).

The table panel shows rows and edits ROIs; the step columns are a second job (mean and SD of every ROI and group in each experiment step, ``selection/step_table_module.py``).
This object owns that job's controls and decisions, so the panel only has to give it a place in the toolbar:

- the **buttons** after the toolbar's separator: the columns switch, what the cells show (mean / SD / both), the Sensorgram's Subtract switch, the refresh button, the export button;
- **showing and hiding** the columns, and following the steps when they change (merged, added, removed, retitled);
- the **comparison columns** (S1 - B1 ...): the button that adds one, the operation menu of a column's header arrow, removing one, and remembering the list;
- **which rows are on screen** (every group header, because a group's number needs all its ROIs; the ROIs under open groups) and asking the module to calculate them;
- the **refresh button's state**: lit, with a count in its tool tip, when rows on screen have no numbers or numbers that are out of date;
- the **export** of the table as listed now;
- the **width of the step columns** (2026-11-06): by default each is as wide as the widest of its title and what its cells show, and follows that whenever it changes (numbers
  arrive, the display or the decimals change, a step is retitled, columns come and go: `fit_if_auto`). Dragging the edge of one step column gives them all that width and stops the
  fitting (a manual width, remembered); a double-click on a step column's edge, or "Fit step columns to content" in the header's menu, fits them again;
- the **remembered state** (columns on, what the cells show, the manual width if there is one).

It reaches back into the panel only through `StepHost` (rebuild the view, is the Groups-only view on, is the panel hidden, show a notice, the saved-state store), so it can be read
and tested apart from the 1,100 lines of the panel. Without a `StepTableModule` it does nothing and shows nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.export_tables import roi_step_table
from ...analysis.step_table import OP_BULK, OPERATIONS, GroupMembers
from ...selection.step_table_module import StepTableModule, compare_title
from ...storage.ui_state_keys import ROI_TABLE_COMPARE_COLUMNS, ROI_TABLE_STEP_DISPLAY, ROI_TABLE_STEP_WIDTH_MANUAL, ROI_TABLE_STEPS_SHOWN
from ..icon_button_style import style_general_icon_button
from ..plot_export_button import ExportData, PlotExportButton
from ..ribbon_icon_size import ICON_SIZE
from ..sensorgram.plan_icons import bulk_picture
from ..sensorgram.stat_icons import subtract_icon
from ..spectra.pick_menu import PickMenu, equation_picture
from ..spectra.view_menu import IconToggle
from ..ui_state import UiStateStore
from .model import RoiTreeModel
from .rows import COLUMN_COUNT
from .step_columns import (
    DISPLAY_BOTH,
    DISPLAY_EXCESS,
    DISPLAY_EXCESS_U,
    DISPLAY_EXCESS_Z,
    DISPLAY_MEAN,
    DISPLAY_MEAN_SEM,
    DISPLAY_SEM,
    DISPLAY_STD,
    DISPLAY_TIPS,
    DISPLAYS,
    OPERATION_LABELS,
    OPERATION_TIPS,
    title_html,
)
from .view import RoiTreeView

_MANUAL_WIDTH_RANGE = (40, 600)  # px: a remembered manual width outside this is ignored
_STATE_COALESCE_MS = 40
_RENDER_SIZE = ICON_SIZE * 2  # icons are drawn at twice the size and scaled down, as the ribbon does
_STROKE_WIDTH = 2.1
_DISPLAY_HTML = {
    DISPLAY_MEAN: "&mu;",
    DISPLAY_STD: "&sigma;",
    DISPLAY_BOTH: "&mu; &plusmn; &sigma;",
    DISPLAY_SEM: "SEM",
    DISPLAY_MEAN_SEM: "&mu; &plusmn; SEM",
    DISPLAY_EXCESS: "&mu; &minus; &mu;*",
    DISPLAY_EXCESS_U: "&mu; &minus; &mu;* &plusmn; u",
    DISPLAY_EXCESS_Z: "(&mu; &minus; &mu;*) / u",
}


@dataclass(frozen=True)
class StepHost:
    """What the controls need from the panel they sit in."""

    rebuild: Callable[..., None]  # (change, *, reveal=...): apply `change` to the model, then put back expansion, scroll position and selection
    group_only: Callable[[], bool]  # the Groups-only view is on
    deferred: Callable[[], bool]  # the panel is hidden: skip work, the panel catches up when shown
    notice: Callable[..., None]  # (text, *, error=...): a line in the dock's title bar and the status bar
    store: Callable[[], UiStateStore | None]


class StepColumnControls(QObject):
    def __init__(self, panel: QWidget, step_table: StepTableModule | None, model: RoiTreeModel, tree: RoiTreeView, host: StepHost) -> None:
        super().__init__(panel)
        self._steps = step_table
        self._model = model
        self._tree = tree
        self._host = host
        self._on = False  # the step columns are shown
        self._manual: int | None = None  # the width the user gave the step columns by dragging; None = each fits its content
        self._resizing = False  # the controls themselves are setting widths: that is not the user dragging an edge
        self._fit_action: QAction | None = None  # "Fit step columns to content" in the header's menu (`add_menu_entries`)
        self._restored = False  # the remembered list of comparison columns has been read: until then nothing may overwrite it
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.setInterval(_STATE_COALESCE_MS)
        self._state_timer.timeout.connect(self.update_state)

        self.separator = QFrame(panel)
        self.separator.setFixedSize(1, 18)
        self.columns_button = self._icon_button(panel, "layout-columns", checkable=True)
        self.columns_button.setToolTip(
            "Step columns: one column per experiment step with the mean and / or SD of every ROI and group in it (the numbers of the Sensorgram's lines). "
            "Click a step's header to sort by it."
        )
        self.columns_button.clicked.connect(lambda _checked=False: self.set_shown(self.columns_button.isChecked(), ask=True))
        self.display_pick = PickMenu(panel, name_on_face=False)
        for mode in DISPLAYS:
            self.display_pick.add_choice(mode, "", DISPLAY_TIPS[mode], equation_picture(_DISPLAY_HTML[mode]))  # (the symbol says it; the tip, on hover, says it in words)
        self.display_pick.finish()
        self.display_pick.setCurrentIndex(self.display_pick.findData(DISPLAY_BOTH))
        self.display_pick.setToolTip(
            "What the step columns show: the mean, the standard deviation (SD), the standard error of the mean (SEM = SD / sqrt(cubes in the step)), or the mean with one of them; "
            "or measured minus the bulk prediction (μ* from the row's own S_bulk and the step's refractive index), with its uncertainty u or divided by it."
        )
        self.subtract = IconToggle(
            subtract_icon,
            "Subtract the baseline: the means read as the shift from the Sensorgram's baseline selector instead of an absolute position. The same switch as the Sensorgram's "
            "(Baseline group); without it the absolute positions are shown, and ranking ROIs by them ranks their position, not their response.",
            panel,
        )
        self.add_compare_button = self._icon_button(panel, "math-function")
        self.add_compare_button.setToolTip(
            "Add a comparison column: the signal step of an evaluation pair against its baseline step (S1 - B1, or divided by the noise of B1), worked out from the step numbers. "
            "Set the pairs in the Sensorgram's Experimental plan tab (Evaluation). The arrow in a column's header changes its calculation or removes it."
        )
        self.add_compare_button.clicked.connect(lambda _checked=False: self._add_compare())
        self.bulk_button = self._icon_button(panel, "", checkable=True)  # (a painted icon, `plan_icons.bulk_picture`, as the Sensorgram's S_bulk button)
        self.bulk_button.setToolTip(
            "S_bulk column: the bulk sensitivity of every ROI and group, the slope of its step means against the refractive index of each step's solution (nm/RIU), "
            "from the steps of the S_bulk set (Sensorgram > Experimental plan > Evaluation > S_bulk, or right-click a step > Use for S_bulk). Click again to remove the column."
        )
        self.bulk_button.clicked.connect(lambda _checked=False: self._toggle_bulk())
        self.refresh_button = self._icon_button(panel, "refresh")
        self.refresh_button.clicked.connect(lambda _checked=False: self.recalculate())
        self.export_button = PlotExportButton("roi_table_steps", None, self.export_data, panel)  # the app gives it the folder to save in, like the plots' buttons
        self._dependents = (self.display_pick, self.subtract, self.add_compare_button, self.bulk_button, self.refresh_button, self.export_button)
        self.columns_button.setVisible(step_table is not None)
        self.separator.setVisible(step_table is not None)
        for widget in self._dependents:
            widget.setVisible(False)  # until the step columns are on
        if step_table is not None:
            step_table.columns_changed.connect(self._on_columns_changed)
            step_table.numbers_changed.connect(self._on_numbers_changed)
            step_table.state_changed.connect(self.schedule_state)
            step_table.notice.connect(lambda text: host.notice(text, error=True))
            self.display_pick.currentIndexChanged.connect(self._on_display_chosen)
            self.subtract.toggled.connect(self._on_subtract_toggled)
            tree.header().compare_menu_requested.connect(self._show_compare_menu)
            tree.header().fit_requested.connect(lambda _section: self.fit_now())

    # -- the toolbar ---------------------------------------------------------------------------------------------------------

    def add_to_toolbar(self, toolbar: QHBoxLayout) -> None:
        """After the panel's own buttons: a separator, the columns switch, and the controls that only matter while the columns are on."""
        toolbar.addSpacing(6)
        toolbar.addWidget(self.separator, 0, Qt.AlignmentFlag.AlignVCenter)
        toolbar.addSpacing(6)
        toolbar.addWidget(self.columns_button)
        for widget in self._dependents:
            toolbar.addWidget(widget)

    @staticmethod
    def _icon_button(parent: QWidget, icon_name: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(parent)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_general_icon_button(button)
        button.setProperty("step_icon", icon_name)  # not "icon_name": the panel's theme loop leaves these to `refresh_theme` below
        return button

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.separator.setStyleSheet(f"background: {theme.toolbar_border}; border: none;")
        style_general_icon_button(self.columns_button)
        self.columns_button.setIcon(
            load_tabler_icon(
                "layout-columns", color=theme.accent_blue if self.columns_button.isChecked() else theme.text_secondary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH
            )
        )
        style_general_icon_button(self.refresh_button)
        style_general_icon_button(self.add_compare_button)
        self.add_compare_button.setIcon(load_tabler_icon("math-function", color=theme.text_secondary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        style_general_icon_button(self.bulk_button)
        bulk_on = self._steps is not None and self._steps.has_bulk()
        self.bulk_button.blockSignals(True)
        self.bulk_button.setChecked(bulk_on)
        self.bulk_button.blockSignals(False)
        self.bulk_button.setIcon(QIcon(bulk_picture()(theme.accent_blue if bulk_on else theme.text_secondary)))
        for widget in (self.display_pick, self.subtract, self.export_button):
            widget.refresh_theme()
        self.update_state()

    # -- remembered state ----------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        if self._steps is None:
            return
        self._restored = True
        width = store.get(ROI_TABLE_STEP_WIDTH_MANUAL)
        if isinstance(width, int) and not isinstance(width, bool) and _MANUAL_WIDTH_RANGE[0] <= width <= _MANUAL_WIDTH_RANGE[1]:
            self._manual = width
        display = store.get(ROI_TABLE_STEP_DISPLAY)
        if display in DISPLAYS:
            self.display_pick.setCurrentIndex(max(self.display_pick.findData(display), 0))
            self._model.set_step_display(str(display))
        compare = store.get(ROI_TABLE_COMPARE_COLUMNS)
        if isinstance(compare, list):
            entries = [
                (int(item[0]), str(item[1])) for item in compare
                if isinstance(item, list) and len(item) == 2 and isinstance(item[0], int) and not isinstance(item[0], bool) and (item[1] in OPERATIONS or item[1] == OP_BULK)
            ]
            self._steps.restore_compare(entries)
        if store.get(ROI_TABLE_STEPS_SHOWN) is True:
            self.set_shown(True, ask=False)

    def section_resized(self, new_width: int) -> None:
        """The user dragged the edge of a step column: they all get that width, which is remembered, and they stop fitting their content (until `fit_now`)."""
        if new_width <= 0 or self._resizing:
            return
        self._manual = new_width
        self._apply_widths()
        store = self._host.store()
        if store is not None:
            store.set(ROI_TABLE_STEP_WIDTH_MANUAL, new_width)

    def is_fitting(self) -> bool:
        """True while the step columns follow their content (no manual width)."""
        return self._manual is None

    def fit_now(self) -> None:
        """Back to fitting: every step column takes the width its content needs, and follows it from now on. The manual width is forgotten."""
        self._manual = None
        store = self._host.store()
        if store is not None:
            store.set(ROI_TABLE_STEP_WIDTH_MANUAL, None)
        if self._on and self._steps is not None:
            self._apply_widths()

    def fit_if_auto(self) -> None:
        """What the step columns show changed (numbers, display, decimals, titles, rows): while they fit their content they follow it. Cheap when nothing is shown or the
        widths do not change (a width Qt already has is not set again)."""
        if self._manual is None and self._on and self._steps is not None and not self._host.deferred():
            self._apply_widths()

    def _apply_widths(self) -> None:
        """Set the widths of the step columns: the manual width for all, or each its own fit. (`_resizing`: this is not the user dragging an edge.)"""
        self._resizing = True
        try:
            if self._manual is None:
                self._tree.fit_step_columns()
            else:
                self._tree.apply_step_column_widths(self._manual)
        finally:
            self._resizing = False

    def add_menu_entries(self, menu: QMenu) -> None:
        """"Fit step columns to content" at the end of the menu a right-click on a column header opens (offered only while there is a manual width to give up)."""
        menu.addSeparator()
        self._fit_action = menu.addAction("Fit step columns to content")
        self._fit_action.setToolTip("The step columns are as wide as their title and numbers need, and follow them. (You gave them one width by dragging an edge; double-clicking an edge does the same.)")
        self._fit_action.triggered.connect(lambda _checked=False: self.fit_now())
        self.prepare_menu()

    def prepare_menu(self) -> None:
        """Called as the menu opens."""
        if self._fit_action is not None:
            self._fit_action.setVisible(self._on and self._manual is not None)

    # -- showing the columns -------------------------------------------------------------------------------------------------

    def is_shown(self) -> bool:
        return self._on

    def set_shown(self, on: bool, *, ask: bool) -> None:
        """Show or hide the step columns. `ask`: the user switched them on, so rows without numbers are calculated now (turning the columns on is asking to see numbers)."""
        if self._steps is None:
            return
        self._on = on
        self.columns_button.blockSignals(True)
        self.columns_button.setChecked(on)
        self.columns_button.blockSignals(False)
        self.refresh_theme()
        for widget in self._dependents:
            widget.setVisible(on)
        self._host.rebuild(lambda: self._model.set_step_columns(self._steps.columns() if on else (), self._steps if on else None), reveal=False)
        if on:
            self._apply_widths()
            self._sync_subtract()
        self.update_state()
        store = self._host.store()
        if store is not None:
            store.set(ROI_TABLE_STEPS_SHOWN, on)
        if on and ask:
            rois, groups = self.visible_rows()
            if self._steps.count_not_current(rois, groups):
                self.recalculate()

    def _on_columns_changed(self) -> None:
        """The steps changed (merged, added, removed, retitled) or a comparison column was made, changed or removed: the columns follow."""
        self._save_compare()
        self.refresh_theme()  # the S_bulk button shows whether its column is there
        if not self._on or self._steps is None:
            return
        self._host.rebuild(self._set_columns, reveal=False)
        self._apply_widths()  # (a column that came, went or was retitled: the manual width for all, or each its own fit)
        self.update_state()

    def _set_columns(self) -> None:
        self._model.set_step_columns(self._steps.columns(), self._steps)

    def _on_numbers_changed(self) -> None:
        if not self._on or self._host.deferred():
            return
        if self._model.sorts_by_step():
            self._host.rebuild(self._model.refresh_numbers, reveal=False)  # the order follows the numbers
        else:
            self._model.refresh_numbers()
        self.fit_if_auto()
        self.schedule_state()

    def _on_display_chosen(self, _index: int) -> None:
        mode = self.display_pick.currentData()
        if mode not in DISPLAYS:
            return
        if self._model.sorts_by_step():
            self._host.rebuild(lambda: self._model.set_step_display(mode), reveal=False)
        else:
            self._model.set_step_display(mode)
        self.fit_if_auto()  # "mean" is much narrower than "mean ± SD"
        store = self._host.store()
        if store is not None:
            store.set(ROI_TABLE_STEP_DISPLAY, mode)

    # -- comparison columns ----------------------------------------------------------------------------------------------------

    def _add_compare(self) -> None:
        if self._steps is None or not self._on:
            return
        spec = self._steps.add_compare()
        if spec.pair not in self._steps.defined_pairs():
            self._host.notice(
                f"Choose the baseline and signal step of evaluation pair {spec.pair} in the Sensorgram's Experimental plan tab (Evaluation) to fill the new column.", error=False
            )

    def _toggle_bulk(self) -> None:
        if self._steps is None or not self._on:
            return
        self._steps.set_bulk_shown(not self._steps.has_bulk())
        if self._steps.has_bulk() and not self._steps.bulk_points():
            self._host.notice(
                "Put steps into the S_bulk set (Sensorgram > Experimental plan > Evaluation > S_bulk) and import the refractive indices to fill the S_bulk column.", error=False
            )

    def _save_compare(self) -> None:
        store = self._host.store()
        if store is not None and self._steps is not None and self._restored:
            store.set(ROI_TABLE_COMPARE_COLUMNS, [[c.pair, c.op] for c in self._steps.compare_specs()])

    def set_compare_op(self, uid: int, op: str) -> None:
        if self._steps is not None:
            self._steps.set_compare_op(uid, op)

    def remove_compare(self, uid: int) -> None:
        if self._steps is not None:
            self._steps.remove_compare(uid)

    def _show_compare_menu(self, section: int, pos: QPoint) -> None:
        """The arrow in a comparison column's header: what the column calculates (each choice is the formula with real subscripts, as in the header, and its short name), and
        removing it. A `PickMenu` that is never shown itself, like the display menu next to the column button: a plain menu entry cannot draw subscripts."""
        columns = self._model.step_columns()
        index = section - COLUMN_COUNT
        if not 0 <= index < len(columns) or columns[index].kind != "compare":
            return
        column = columns[index]
        uid = column.ref[1]  # type: ignore[index]
        if column.op == OP_BULK:  # nothing to choose: the column is the S_bulk set's slope
            menu = QMenu(self._tree)
            menu.addAction("Remove this column").triggered.connect(lambda _checked=False: self.remove_compare(uid))
            menu.exec(pos)
            menu.deleteLater()
            return
        pick = PickMenu(self.columns_button, name_on_face=False)  # (a child of the visible button: it stays hidden, there is no flash of a window)
        for op in OPERATIONS:
            pick.add_choice(op, OPERATION_LABELS[op], OPERATION_TIPS[op], equation_picture(title_html(compare_title(column.pair, op))))
        remove = QToolButton(pick)
        remove.setText("Remove this column")
        remove.setAutoRaise(True)
        theme = get_active_theme()
        remove.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; padding: 4px 6px; text-align: left; color: {theme.text_primary}; }}"
            f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
        )
        pick.add_footer(remove)
        pick.finish()
        pick.setCurrentIndex(pick.findData(column.op))
        pick.currentIndexChanged.connect(lambda _index: self.set_compare_op(uid, str(pick.currentData())))
        remove.clicked.connect(lambda _checked=False: (pick.close_menu(), self.remove_compare(uid)))
        pick.popup_at(pos)
        pick.deleteLater()

    # -- the Subtract switch (the Sensorgram's one setting) -------------------------------------------------------------------

    def _on_subtract_toggled(self, on: bool) -> None:
        if self._steps is not None and on != self._steps.baseline_enabled():
            self._steps.set_baseline_enabled(on)

    def _sync_subtract(self) -> None:
        if self._steps is None:
            return
        self.subtract.blockSignals(True)
        self.subtract.setChecked(self._steps.baseline_enabled())
        self.subtract.blockSignals(False)
        self.subtract.refresh_theme()

    # -- calculating ---------------------------------------------------------------------------------------------------------

    def visible_rows(self) -> tuple[list[int], list[GroupMembers]]:
        """The rows on screen: every group header (a group's number needs all its ROIs, shown or not) and the ROIs under the groups that are open (all ROIs without groups)."""
        if not self._model.is_grouped_view():
            return [row.roi_id for row in self._model.rows()], []
        rois: list[int] = []
        groups: list[GroupMembers] = []
        for header in self._model.group_headers():
            members = tuple(sorted(self._model.member_ids(header)))
            groups.append(GroupMembers(self._model.group_id(header), members))
            if not self._host.group_only() and self._tree.isExpanded(header):
                rois.extend(members)
        return rois, groups

    def recalculate(self) -> None:
        if self._steps is None or not self._on:
            return
        rois, groups = self.visible_rows()
        self._steps.recalculate(rois, groups)

    def calculate_at_startup(self) -> None:
        """The app has finished loading after launch (2026-11-06, maintainer: "recalculate it during the start"): calculate the rows on screen once, as the refresh button would,
        so the step columns are filled when the user sits down. Quiet where the button would complain or where nobody asked for numbers: step columns off, the table hidden
        (a hidden panel does no work), no scan or no steps yet, or every row already current. It shows in the status bar's task indicator and can be cancelled there."""
        if self._steps is None or not self._on or self._host.deferred() or not self._steps.can_calculate():
            return
        rois, groups = self.visible_rows()
        if self._steps.count_not_current(rois, groups):
            self.recalculate()

    def cancel(self) -> None:
        """The task indicator's Cancel: stop the calculation (what was fitted is kept)."""
        if self._steps is not None:
            self._steps.cancel()

    def schedule_state(self, *_args: object) -> None:
        """Ask for the refresh button to be brought up to date (coalesced: settings and expansion change in bursts)."""
        if self._on and not self._state_timer.isActive():
            self._state_timer.start()

    def update_state(self) -> None:
        """The refresh button says whether the rows on screen need calculating (lit, with a tool tip that counts them); the Subtract switch follows the setting."""
        if self._steps is None or not self._on:
            return
        self._sync_subtract()
        theme = get_active_theme()
        rois, groups = self.visible_rows()
        missing = self._steps.count_not_current(rois, groups)
        total = len(rois) + len(groups)
        busy = self._steps.is_running()
        lit = missing > 0 and not busy
        self.refresh_button.setIcon(
            load_tabler_icon("refresh", color=theme.accent_gold if lit else theme.text_secondary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        )
        self.refresh_button.setEnabled(not busy)
        if busy:
            text = "Calculating the step columns... (Cancel in the status bar)"
        elif missing == 0:
            text = "The rows on screen are up to date. Click to calculate them again."
        else:
            text = (
                f"{missing} of the {total} rows on screen have no numbers or numbers that are out of date. Click to calculate the rows on screen. "
                "A group's row needs all its ROIs; the ROIs of a collapsed group are not calculated."
            )
        note = self._steps.scan_note()
        self.refresh_button.setToolTip(text + (f"\n\n{note}" if note else ""))

    # -- export ----------------------------------------------------------------------------------------------------------------

    def export_data(self) -> ExportData | None:
        """What "Save data" writes: the table as listed now, groups with their ROIs, the columns and display the user chose."""
        if not self._on or self._steps is None or not self._steps.has_numbers() or not self._steps.columns():
            # raised, not returned as "nothing": the export button shows this sentence to the user
            raise ValueError("Nothing to export yet: switch the step columns on and calculate them (the refresh button) first.")
        columns = self._steps.columns()

        def values(cells):
            current = [c.current for c in cells if c is not None]
            return [None if c is None else (c.n, c.mean, c.sd, c.sem, c.excess, c.excess_u) for c in cells], (None if not current else all(current))

        rows = []
        for group, members in self._model.display_order():
            ids = tuple(sorted(row.roi_id for row in members))
            if group is not None:
                cells, current = values([self._steps.group_cell(group.group_id, ids, c.ref) for c in columns])
                rows.append(("group", group.name, None, cells, current))
            for row in members:
                cells, current = values([self._steps.roi_cell(row.roi_id, c.ref) for c in columns])
                rows.append(("roi", "" if group is None else group.name, row.roi_id, cells, current))
        described = [
            (c.export_label or f"step {c.order} ({c.title})", c.value_word, c.unit, c.has_sd) for c in columns
        ]
        table = roi_step_table(described, rows, self._model.step_display())
        note = self._steps.scan_note()
        if note:
            table = replace(table, notes=(*table.notes, note))
        return ExportData({"roi_table_steps": table}, {"display": self._model.step_display(), **self._steps.export_info()})
