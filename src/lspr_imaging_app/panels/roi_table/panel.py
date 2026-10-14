"""``RoiTablePanel`` (sketch §7 "ROI Toolbox" front doors, §10).

The ROI/Group table: every ROI with its number, name, position and diameters,
organised under collapsible group headers. It is a thin
renderer over `RoiToolbox` (it holds no ROI or group state; every edit is a
toolbox command, so Ctrl+Z undoes it like any other) and it drives the shared
`SelectionModule`, so selecting rows here selects the same ROIs on the image.

**What the table does**
- Rows: ``#`` (the ROI's place in the list; reordering changes these) with a colour dot. The
  view mode (right-click the column headers) picks what else is shown. **Simple**: the group label. **Geometry**: x, y, a
  shape pictogram and the shape's diameters (a ring: D_s, d_r, D_r; D = outer, d = inner diameter,
  s = sample, r = reference; a number the shape does not have is left blank). Lengths show in px or µm following the Geometry display unit; the px/µm toggle in the
  toolbar changes that unit (the same setting as the Image ribbon's View tab). A ring diameter the
  ROI takes from the shared default is dimmed.
- Click a header to sort ascending/descending. Sorting only changes the view;
  to *reorder* ROIs (change their numbers) sort by ``#`` ascending first.
- Edit a cell with a double-click or F2. With several ROIs selected, editing
  a position or diameter of one of them sets it on all of them (one undo step).
  Excel-style on the current cell: Ctrl+C copies its value, Ctrl+V pastes it into
  that column of every selected ROI, Ctrl+D copies the topmost selected row's value
  down the selection. In a diameter editor Up/Down step the value by 0.5 px (Shift:
  5 px); Ctrl+wheel over a diameter cell steps and applies it.
- A group header selects its members; double-click renames, the chevron
  collapses. Double-click a colour chip to change a ROI's colour (or recolour
  the group).
- Drag ROIs by their rows: between rows of a list to reorder them (the same
  rule as the buttons: sorted by ``#`` ascending), onto a group header to put
  them in that group, onto "Ungrouped" to take them out of their groups.
- Right-click for group / ungroup / colour / shift / reset diameters / move /
  delete. Delete removes the selected ROIs; a selected group header counts as
  all its ROIs. Ungroup dissolves a group and keeps its ROIs.

**Reordering and deleting renumber ROIs**, which a running analysis cannot
follow, so they are refused until it finishes (`AnalysisEngine.is_running`).

Bad input (a size below the minimum, a ring with inner >= outer, an empty
group name) is refused by the toolbox before anything changes, and shown here
in the footer; it is never silently corrected.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QLinearGradient, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QColorDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.engine import AnalysisEngine
from ...image_tools import GeometryModule
from ...roi import RoiToolbox
from ...roi.display_style import RoiDisplayStyle
from ...roi.scope import RoiEditTarget
from ...selection import SelectionModule
from ...storage.ui_state_keys import ROI_TABLE_COLLAPSED, ROI_TABLE_COLUMN_WIDTHS, ROI_TABLE_SORT, ROI_TABLE_VIEW_MODE
from ..image.general_group import ICON_SIZE, style_general_icon_button
from ..ui_state import UiStateStore
from ..visibility_gate import HiddenPanelGate
from ..unit_toggle import UnitToggle
from .delegate import RoiTableDelegate
from .dialogs import ShiftDialog
from .model import DropTarget, RoiTreeModel
from .rows import (
    COLUMN_COUNT,
    COLUMN_ID,
    COLUMN_NAME,
    COLUMN_RING_IN,
    COLUMN_RING_OUT,
    COLUMN_SAMPLE,
    COLUMN_X,
    COLUMN_Y,
    DIAMETER_COLUMNS,
    EDITABLE_COLUMNS,
    PIXELS,
    SHAPE_FIELDS,
    VIEW_GEOMETRY,
    VIEW_MODE_COLUMNS,
    VIEW_MODES,
    VIEW_SIMPLE,
    LengthUnit,
    from_display,
    micrometers,
    movement_scope,
    parse_length,
    step_target_index,
    step_text,
)
from .view import RoiTreeView, suggested_column_widths

_LEGACY_COLUMN_COUNT = 7  # a saved width list from before the Position / Shape / Shape vector columns
_REDRAW_COALESCE_MS = 100  # sketch §8
_NOTICE_MS = 8000
_ERROR_NOTICE_MS = 15000
NUMERIC_COLUMNS = (COLUMN_X, COLUMN_Y, *DIAMETER_COLUMNS)
_UNGROUPED_KEY = "__ungrouped__"
_RENDER_SIZE = ICON_SIZE * 2  # icons are drawn at twice the size and scaled down, as the ribbon does
_STROKE_WIDTH = 2.1


def _group_key(group_id: str | None) -> str:
    return _UNGROUPED_KEY if group_id is None else group_id


def _message(exc: Exception) -> str:
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


class RoiTablePanel(QWidget):
    status_message = pyqtSignal(str)
    """A one-line result for the app's status bar (also shown in the footer)."""

    def __init__(
        self,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        geometry: GeometryModule,
        analysis_engine: AnalysisEngine,
        parent: QWidget | None = None,
        edit_target: RoiEditTarget | None = None,
        display_style: RoiDisplayStyle | None = None,
    ) -> None:
        super().__init__(parent)
        # The shared ROI display style: its Sample colour is the colour of a ROI with none of its own, the same
        # object the Image overlay draws with (None: the palette default).
        self._display_style = display_style
        self._toolbox = roi_toolbox
        # Where a geometry edit lands (scope + cube; None = the base geometry, as before the timeline).
        self._edit_target = edit_target
        self._selection = selection
        self._geometry = geometry
        self._engine = analysis_engine
        self._collapsed: set[str] = set()
        self._syncing = False  # the view and SelectionModule are being brought into line
        self._restoring_view = False
        self._store: UiStateStore | None = None

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._on_refresh_timer)
        self._gate = HiddenPanelGate(on_shown=self._schedule_refresh)  # no rebuilding while the dock is hidden
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._update_footer)

        self._build_ui()
        self._connect()
        self._apply_theme()
        self.refresh_now()

    # -- remembered state ---------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        """Put the view mode, sort, collapsed groups and column widths back as
        last left, and keep saving them (see `panels/ui_state.py`). Called once
        by the app shell."""
        self._store = store
        saved_mode = store.get(ROI_TABLE_VIEW_MODE)
        if saved_mode in VIEW_MODES:
            self._apply_view_mode(str(saved_mode))
        saved_sort = store.get(ROI_TABLE_SORT)
        if (
            isinstance(saved_sort, list) and len(saved_sort) == 2
            and isinstance(saved_sort[0], int) and not isinstance(saved_sort[0], bool)
            and saved_sort[0] in VIEW_MODE_COLUMNS[self._view_mode] and isinstance(saved_sort[1], bool)
        ):
            self._rebuild(lambda: self._model.set_sort(saved_sort[0], saved_sort[1]))
        collapsed = store.get(ROI_TABLE_COLLAPSED)
        if isinstance(collapsed, list) and all(isinstance(item, str) for item in collapsed):
            self._collapsed = set(collapsed)
            self._rebuild(lambda: None)
        widths = store.get(ROI_TABLE_COLUMN_WIDTHS)
        if (
            isinstance(widths, list) and len(widths) in (_LEGACY_COLUMN_COUNT, COLUMN_COUNT)
            and all(isinstance(w, int) and not isinstance(w, bool) and w > 0 for w in widths)
        ):
            suggested = suggested_column_widths(self._tree.fontMetrics())
            floor = suggested[COLUMN_ID]  # a narrower saved ID column clips 3-digit ids
            for column, width in enumerate(widths):  # a list of the old 7 columns leaves the three new ones at their default
                if column == COLUMN_NAME and width > 3 * suggested[column]:
                    continue  # a width saved while this column stretched: keep the default
                self._widths[column] = max(width, floor) if column == COLUMN_ID else width
            self._apply_widths()

    # -- construction -----------------------------------------------------------------

    def _build_ui(self) -> None:
        self._model = RoiTreeModel(self)
        self._tree = RoiTreeView(self)
        self._tree.setModel(self._model)
        self._tree.setItemDelegate(RoiTableDelegate(self._tree))
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.apply_default_column_widths()
        self._widths = suggested_column_widths(self._tree.fontMetrics())  # what each column is set to, also while hidden
        self._view_mode = VIEW_GEOMETRY
        self._tree.set_view_mode(self._view_mode)  # the unit toggle (made below) follows in `_apply_view_mode`

        self._hint = QLabel("No ROIs yet.\nAdd them on the Image panel, or run detection.", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._tree)
        self._stack.addWidget(self._hint)

        self._group_button = self._tool_button("group-rois", self._group_selected_or_new)
        self._ungroup_button = self._tool_button("ungroup-rois", self._ungroup_selected)
        self._color_button = self._tool_button("droplet", self._color_selected)
        self._up_button = self._tool_button("arrow-up", lambda: self._move_selected(-1))
        self._down_button = self._tool_button("arrow-down", lambda: self._move_selected(1))
        self._delete_button = self._tool_button("trash", self._delete_selected)
        self._view_menu = QMenu(self)  # opened by a right-click on the column headers (`_show_header_menu`)
        self._view_actions: dict[str, QAction] = {}
        action_group = QActionGroup(self)
        action_group.setExclusive(True)
        for mode, text in ((VIEW_SIMPLE, "Simple view (ID and group label)"), (VIEW_GEOMETRY, "Geometry view (position, shape, diameters)")):
            action = self._view_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(mode == self._view_mode)
            action.triggered.connect(lambda _checked=False, m=mode: self._on_view_mode_chosen(m))
            action_group.addAction(action)
            self._view_actions[mode] = action
        self._view_menu.setToolTipsVisible(True)
        self._view_actions[VIEW_GEOMETRY].setToolTip("x, y, shape and the diameters. D = outer, d = inner diameter; s = sample, r = reference.")
        self._view_actions[VIEW_SIMPLE].setToolTip("Only the ID and the group labels.")
        self._tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._unit_toggle = UnitToggle(self._geometry, self)  # px <-> µm for x, y and the diameters

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 4, 6, 4)
        toolbar.setSpacing(2)
        self._group_slot = QStackedWidget(self)  # one place for Group / Ungroup: whichever fits the selection
        self._group_slot.addWidget(self._group_button)
        self._group_slot.addWidget(self._ungroup_button)
        toolbar.addWidget(self._group_slot)
        toolbar.addWidget(self._color_button)
        toolbar.addSpacing(8)
        for button in (self._up_button, self._down_button):
            toolbar.addWidget(button)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._delete_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self._unit_toggle)

        self._footer = QLabel(self)
        self._footer.setContentsMargins(8, 3, 8, 4)
        self._footer.setWordWrap(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._footer)

    def _tool_button(self, icon_name: str, handler: Callable[[], None] | None, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setProperty("icon_name", icon_name)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        style_general_icon_button(button)
        if handler is not None:
            button.clicked.connect(lambda _checked=False: handler())
        return button

    def _connect(self) -> None:
        self._toolbox.geometry_changed.connect(self._schedule_refresh)
        self._toolbox.cosmetic_changed.connect(self._schedule_refresh)
        self._geometry.cosmetic_changed.connect(self._schedule_refresh)  # display unit, calibration
        if self._display_style is not None:
            self._display_style.changed.connect(self._schedule_refresh)  # the Sample colour of a ROI with none
        self._selection.cube_changed.connect(self._on_cube_changed)  # the geometry shown is the one valid on this cube
        self._selection.roi_selection_changed.connect(self._on_selection_changed_elsewhere)
        self._model.cell_edited.connect(self._on_cell_edited)
        self._model.roi_dropped.connect(self._on_roi_dropped)
        self._tree.selectionModel().selectionChanged.connect(self._on_view_selection_changed)
        self._tree.expanded.connect(lambda index: self._on_expansion_changed(index, True))
        self._tree.collapsed.connect(lambda index: self._on_expansion_changed(index, False))
        self._tree.header().sectionClicked.connect(self._on_header_clicked)
        self._tree.header().sectionResized.connect(self._on_section_resized)
        self._tree.header().customContextMenuRequested.connect(self._show_header_menu)
        self._tree.delete_requested.connect(self._delete_selected)
        self._tree.move_requested.connect(self._move_selected)
        self._tree.copy_requested.connect(self._copy_cell)
        self._tree.paste_requested.connect(self._paste_cells)
        self._tree.fill_down_requested.connect(self._fill_down)
        self._tree.step_requested.connect(self._step_cell)
        self._tree.chip_double_clicked.connect(self._on_chip_double_clicked)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)

    # -- theme ----------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        """Called when the user switches theme (the app shell's theme menu)."""
        self._apply_theme()

    def _apply_theme(self) -> None:
        theme = get_active_theme()
        self._tree.setStyleSheet(
            f"QTreeView {{ background: {theme.window_bg}; border: none; outline: none; }}"
            f"QHeaderView::section {{ background: {theme.toolbar_bg}; color: {theme.text_muted}; border: none; "
            f"border-bottom: 1px solid {theme.toolbar_border}; padding: 4px 6px; font-weight: 600; }}"
        )
        self._hint.setStyleSheet(f"color: {theme.text_dim}; background: {theme.window_bg}; padding: 24px;")
        for button in self.findChildren(QToolButton):
            name = button.property("icon_name")
            if name:  # the unit toggle is not one of these: it styles itself (`UnitToggle.refresh_theme`)
                self._style_tool_button(button, str(name))
        self._unit_toggle.refresh_theme()
        self._update_footer()
        self._tree.viewport().update()

    def _style_tool_button(self, button: QToolButton, icon_name: str) -> None:
        """The Image ribbon's icon-button look: its stylesheet (`padding: 0`
        matters: without it the app-wide style shrinks the icon to a few
        pixels), its sizes, its icon rendering. A toggle that is on is drawn in
        the accent colour, as the ribbon's toggles are."""
        theme = get_active_theme()
        style_general_icon_button(button)
        if button is self._color_button:
            button.setIcon(self._rainbow_icon(icon_name))
            return
        on = button.isCheckable() and button.isChecked()
        button.setIcon(
            load_tabler_icon(
                icon_name, color=theme.accent_blue if on else theme.text_secondary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH
            )
        )

    @staticmethod
    def _rainbow_icon(icon_name: str) -> QIcon:
        """The icon's shape, filled solid, with a rainbow gradient (it is drawn white, then the gradient is painted
        only where the icon has pixels), so the colour button reads as "colours"."""
        pixmap = load_tabler_icon(
            icon_name, color="#ffffff", fill="#ffffff", size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH
        ).pixmap(
            _RENDER_SIZE, _RENDER_SIZE
        )
        # Span the gradient over the droplet itself (it covers only the middle of the icon square), so the whole
        # spectrum shows inside the shape rather than just the middle hues.
        w, h = pixmap.width(), pixmap.height()
        gradient = QLinearGradient(0.32 * w, 0.12 * h, 0.68 * w, 0.88 * h)
        steps = 12
        for i in range(steps + 1):  # full hue circle, red -> magenta, vivid
            gradient.setColorAt(i / steps, QColor.fromHsvF(0.85 * i / steps, 0.9, 1.0))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), gradient)
        painter.end()
        return QIcon(pixmap)

    # -- refresh --------------------------------------------------------------------------

    def _schedule_refresh(self, *_args: object) -> None:
        if self._gate.defer():
            return
        self._redraw_timer.start()

    def _on_refresh_timer(self) -> None:
        if self._gate.defer():  # the timer was already running when the dock was hidden
            return
        self.refresh_now()

    def set_panel_visible(self, visible: bool) -> None:
        """Connected to the dock's `visibilityChanged`: hidden = no table rebuilds; one catch-up when shown again."""
        self._gate.set_visible(visible)

    def _on_cube_changed(self, *_args: object) -> None:
        # Only a ROI with a per-cube timeline looks different on another cube. Without one the rows are the
        # same for every cube, and rebuilding all of them on each cube change cost ~12 % of the GUI thread
        # while the slideshow stepped through cubes (measured 2026-10-14, 160 ROIs).
        if self._toolbox.has_timeline():
            self._schedule_refresh()

    def refresh_now(self) -> None:
        """Rebuild the table from the toolbox now (normally done ~100 ms after
        the last change, so a burst of edits rebuilds once)."""
        self._redraw_timer.stop()
        self._rebuild(
            lambda: self._model.set_content(
                self._toolbox.rois_at(self._selection.current_cube()),
                self._toolbox.groups(),
                self._toolbox.detection_settings(),
                self._unit(),
                None if self._display_style is None else self._display_style.sample.color,
            )
        )

    def _edit_kwargs(self) -> dict:
        return {} if self._edit_target is None else self._edit_target.kwargs()

    def _unit(self) -> LengthUnit:
        if self._geometry.settings().display_units == "um" and self._geometry.can_display_micrometers():
            return micrometers(self._geometry.microns_per_pixel_scalar())
        return PIXELS

    def _rebuild(self, change: Callable[[], None], *, reveal: bool = True) -> None:
        """Apply ``change`` to the model, then put back what a model reset
        throws away: group expansion, scroll position, selection. With
        ``reveal`` the selected ROIs' groups are expanded so they show; without
        it (a re-sort) a collapsed group stays collapsed."""
        bar = self._tree.verticalScrollBar()
        scroll = bar.value()
        change()
        self._restoring_view = True
        try:
            self._tree.expandAll()
            for header in self._model.group_headers():
                self._tree.setFirstColumnSpanned(header.row(), QModelIndex(), True)
                if _group_key(self._model.group_id(header)) in self._collapsed:
                    self._tree.collapse(header)
        finally:
            self._restoring_view = False
        self._stack.setCurrentIndex(0 if self._model.rows() else 1)
        column, descending = self._model.sort_state()
        self._tree.header().setSortIndicator(
            column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        )
        bar.setValue(scroll)
        ids = self._selection.selected_roi_ids()
        self._model.set_selected_ids(ids)
        keep_groups, self._reselect_groups = self._reselect_groups, None
        if keep_groups:
            self._select_group_headers(keep_groups)  # after moving groups: keep the headers selected, nothing expands
        else:
            self._apply_selection_to_view(ids, reveal=reveal)
        self._update_toolbar_state()
        self._update_footer()

    # -- selection ------------------------------------------------------------------------

    def _view_selection(self) -> tuple[list[int], list[QModelIndex]]:
        """``(ROI ids selected as rows, group headers selected)``."""
        rois: list[int] = []
        headers: list[QModelIndex] = []
        for index in self._tree.selectionModel().selectedRows(0):
            if self._model.is_group(index):
                headers.append(index)
            else:
                roi_id = self._model.roi_id(index)
                if roi_id is not None:
                    rois.append(roi_id)
        return rois, headers

    _reselect_groups: list[str] | None = None
    """Set by a group move: the next rebuild re-selects these headers instead of their members."""

    def _select_group_headers(self, group_ids: list[str]) -> None:
        selection = QItemSelection()
        for group_id in group_ids:
            index = self._model.group_index(group_id)
            if index.isValid():
                selection.select(index, index)
        self._syncing = True
        try:
            self._tree.selectionModel().select(
                selection, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
            )
        finally:
            self._syncing = False

    def _selected_group_ids(self) -> list[str]:
        """Real groups whose header is selected ("Ungrouped" is no group), in the groups' display order."""
        _rois, headers = self._view_selection()
        picked = {self._model.group_id(header) for header in headers}
        return [group.group_id for group in self._toolbox.groups() if group.group_id in picked]

    def _on_view_selection_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        rois, headers = self._view_selection()
        ids = set(rois)
        for header in headers:
            ids.update(self._model.member_ids(header))
        self._syncing = True
        try:
            self._selection.set_roi_selection(ids)
        finally:
            self._syncing = False
        self._model.set_selected_ids(self._selection.selected_roi_ids())
        self._update_toolbar_state()
        self._update_footer()

    def _on_selection_changed_elsewhere(self, ids: object) -> None:
        """The shared selection changed (a click on the image, a delete that
        renumbered...). When the change came from this table the view already
        shows it."""
        self._model.set_selected_ids(ids)  # type: ignore[arg-type]
        if not self._syncing:
            self._apply_selection_to_view(ids, reveal=True)  # type: ignore[arg-type]
        self._update_toolbar_state()
        self._update_footer()

    def _apply_selection_to_view(self, roi_ids: object, *, reveal: bool) -> None:
        indexes = self._model.roi_indexes(roi_ids)  # type: ignore[arg-type]
        selection = QItemSelection()
        for index in indexes:
            selection.select(index, index)
        self._syncing = True
        try:
            self._tree.selectionModel().select(
                selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
        finally:
            self._syncing = False
        if reveal and indexes:
            first = min(indexes, key=lambda index: self._model.roi_id(index) or 0)
            if self._model.parent(first).isValid() and not self._tree.isExpanded(self._model.parent(first)):
                self._tree.expand(self._model.parent(first))
            self._tree.scrollTo(first, QAbstractItemView.ScrollHint.EnsureVisible)

    def _selected_ids(self) -> list[int]:
        return sorted(self._selection.selected_roi_ids())

    # -- expansion, sorting, widths, flat/grouped ------------------------------------------

    def _on_expansion_changed(self, index: QModelIndex, expanded: bool) -> None:
        if self._restoring_view or not self._model.is_group(index):
            return
        key = _group_key(self._model.group_id(index))
        (self._collapsed.discard if expanded else self._collapsed.add)(key)
        if self._store is not None:
            self._store.set(ROI_TABLE_COLLAPSED, sorted(self._collapsed))

    def _on_header_clicked(self, column: int) -> None:
        current, descending = self._model.sort_state()
        new = (column, not descending) if column == current else (column, False)
        self._rebuild(lambda: self._model.set_sort(*new), reveal=False)  # sorting must not expand collapsed groups
        if self._store is not None:
            self._store.set(ROI_TABLE_SORT, [new[0], new[1]])

    def _on_section_resized(self, column: int, _old: int, new: int) -> None:
        if new > 0 and not self._tree.isColumnHidden(column):
            self._widths[column] = new
        if self._store is not None:
            self._store.set(ROI_TABLE_COLUMN_WIDTHS, list(self._widths))

    def _apply_widths(self) -> None:
        for column, width in enumerate(self._widths):
            self._tree.setColumnWidth(column, width)

    def _show_header_menu(self, pos: QPoint) -> None:
        """Right-click on the column headers: choose the table view."""
        self._view_menu.exec(self._tree.header().mapToGlobal(pos))

    def _on_view_mode_chosen(self, mode: str) -> None:
        self._apply_view_mode(mode)
        if self._store is not None:
            self._store.set(ROI_TABLE_VIEW_MODE, mode)

    def _apply_view_mode(self, mode: str) -> None:
        """Show the columns of ``mode``. A sort by a column the new mode hides goes back to the ``#`` order."""
        self._view_mode = mode
        self._tree.set_view_mode(mode)
        self._apply_widths()  # a column hidden at start-up never got its saved width
        self._view_actions[mode].setChecked(True)
        self._unit_toggle.setVisible(mode == VIEW_GEOMETRY)  # the unit only matters for the geometry columns
        if self._model.sort_state()[0] not in VIEW_MODE_COLUMNS[mode]:
            self._rebuild(lambda: self._model.set_sort(COLUMN_ID, False), reveal=False)
            if self._store is not None:
                self._store.set(ROI_TABLE_SORT, [COLUMN_ID, False])

    # -- notices ---------------------------------------------------------------------------

    def _show_notice(self, text: str, *, error: bool = False) -> None:
        theme = get_active_theme()
        self._footer.setText(text)
        self._footer.setStyleSheet(f"color: {theme.accent_red if error else theme.text_secondary};")
        self._footer.setToolTip(text)
        self._notice_timer.start(_ERROR_NOTICE_MS if error else _NOTICE_MS)
        self.status_message.emit(text)

    def _update_footer(self) -> None:
        theme = get_active_theme()
        self._notice_timer.stop()
        total = len(self._model.rows())
        chosen = len(self._selection.selected_roi_ids())
        text = "No ROIs" if total == 0 else f"{total} ROI{'' if total == 1 else 's'}"
        group_count = len(self._toolbox.groups())
        if group_count:
            text += f"  ·  {group_count} group{'' if group_count == 1 else 's'}"
        if chosen:
            text += f"  ·  {chosen} selected"
        self._footer.setText(text)
        self._footer.setToolTip("")
        self._footer.setStyleSheet(f"color: {theme.text_muted};")

    def _guarded(self, action: str, command: Callable[[], None]) -> bool:
        """Run a toolbox command; show a refusal (bad value, stale id) in the
        footer instead of letting it vanish. Returns whether it ran."""
        try:
            command()
        except (ValueError, KeyError) as exc:
            self._show_notice(f"{action}: {_message(exc)}", error=True)
            return False
        return True

    def _renumbering_allowed(self) -> bool:
        if self._engine.is_running():
            self._show_notice("Wait for the analysis to finish before reordering or deleting ROIs.", error=True)
            return False
        return True

    # -- toolbar state ----------------------------------------------------------------------

    def _update_toolbar_state(self) -> None:
        ids = self._selected_ids()
        _rois, headers = self._view_selection()
        rows = self._model.rows()
        scope = movement_scope(ids, rows, grouped=self._model.is_grouped_view()) if ids else None
        sorted_by_number = self._model.sort_state() == (COLUMN_ID, False)
        movable = scope is not None and sorted_by_number
        group_ids = self._selected_group_ids() if not _rois else []
        moving_groups = bool(group_ids) and self._model.is_grouped_view()

        self._group_button.setToolTip(
            f"Group the {len(ids)} selected ROI{'' if len(ids) == 1 else 's'} (each gets its own tint of the group colour)"
            if ids else "New empty group"
        )
        selected_groups = self._selected_group_ids()
        in_group = any(row.group_id is not None for row in rows if row.roi_id in set(ids))
        self._group_slot.setCurrentWidget(self._ungroup_button if selected_groups or in_group else self._group_button)
        self._ungroup_button.setToolTip("Ungroup: take the selected ROIs or groups out of their groups (the ROIs stay).")
        self._color_button.setEnabled(bool(ids) or bool(headers))
        self._color_button.setToolTip(
            "Recolour the group (every member gets a new tint)." if headers and not _rois else "Set the colour of the selected ROIs."
        )
        for button, word, step in ((self._up_button, "up", -1), (self._down_button, "down", 1)):
            if moving_groups:
                button.setEnabled(True)
                noun = "group" if len(group_ids) == 1 else "groups"
                button.setToolTip(f"Move the selected {noun} {word} one place (Alt+{'Up' if step < 0 else 'Down'}).")
                continue
            button.setEnabled(movable)
            if not sorted_by_number:
                button.setToolTip("Sort by # (ascending) to reorder ROIs.")
            elif ids and scope is None:
                button.setToolTip("Select ROIs from a single group to reorder them.")
            else:
                button.setToolTip(f"Move the selected ROIs {word} one place (Alt+{'Up' if step < 0 else 'Down'}).")
        self._delete_button.setEnabled(bool(ids) or bool(headers))
        self._delete_button.setToolTip(
            "Delete the selected groups together with all their ROIs (Delete)." if headers and not _rois else "Delete the selected ROIs (Delete)."
        )

    # -- editing a cell -----------------------------------------------------------------------

    def _edit_targets(self, roi_id: int) -> list[int]:
        """The ROI edited, or, if it is part of a multi-ROI selection, the
        whole selection (so one edit sets a diameter on all of them)."""
        selected = self._selected_ids()
        return selected if roi_id in selected and len(selected) > 1 else [roi_id]

    def _on_cell_edited(self, kind: str, key: object, column: int, text: str) -> None:
        if kind == "group":
            self._guarded("Rename group", lambda: self._toolbox.rename_group(str(key), text))
            return
        roi_id = int(key)  # type: ignore[arg-type]
        if column == COLUMN_NAME:
            self._guarded("Rename ROI", lambda: self._toolbox.set_roi_label(roi_id, text))
            return
        self._set_numeric_cells(self._edit_targets(roi_id), column, text)

    def _set_numeric_cells(self, targets: list[int], column: int, text: str) -> None:
        """Set one x / y / diameter column to ``text`` (in the display unit) on every
        ROI in ``targets``: the one path for an edit, a paste, a fill down and a step."""
        unit = self._model.unit()
        try:
            value = parse_length(text, unit)
        except ValueError as exc:
            self._show_notice(str(exc), error=True)
            return
        noun = f"{len(targets)} ROIs" if len(targets) > 1 else f"ROI {targets[0]}"
        if column in (COLUMN_X, COLUMN_Y):
            axis = "x" if column == COLUMN_X else "y"
            if self._guarded(f"Set {axis}", lambda: self._toolbox.place_rois(targets, **{axis: value}, **self._edit_kwargs())) and len(targets) > 1:
                self._show_notice(f"Set {axis} of {noun} to {text.strip()} {unit.label}.")
            return
        field = {
            COLUMN_SAMPLE: "sample_diameter_px",
            COLUMN_RING_IN: "reference_inner_diameter_px",
            COLUMN_RING_OUT: "reference_outer_diameter_px",
        }.get(column)
        if field is None:
            return
        shapes = {row.roi_id: row.shape for row in self._model.rows()}
        targets = [i for i in targets if column in SHAPE_FIELDS.get(shapes.get(i, ""), ())]  # a disk has no ring, a mask no diameter
        if not targets:
            self._show_notice("These ROIs have no such diameter (a mask has none, a disk has no reference ring).")
            return
        if self._guarded("Set diameter", lambda: self._toolbox.resize_rois(targets, **{field: value}, **self._edit_kwargs())) and len(targets) > 1:
            self._show_notice(f"Set the diameter of {noun}.")

    # -- Excel-style copy / paste / fill down / step (on the current cell) ---------------------

    def _current_roi_cell(self) -> tuple[QModelIndex, int] | None:
        index = self._tree.currentIndex()
        roi_id = self._model.roi_id(index) if index.isValid() else None
        return None if roi_id is None else (index, roi_id)

    def _copy_cell(self) -> None:
        cell = self._current_roi_cell()
        if cell is None or cell[0].column() not in EDITABLE_COLUMNS:
            return
        text = str(cell[0].data(Qt.ItemDataRole.EditRole) or "")
        if text:
            QApplication.clipboard().setText(text)
            self._show_notice(f"Copied {text}.")

    def _paste_cells(self) -> None:
        """The clipboard's first value goes into the current column of every selected ROI
        (of the current row alone with none selected)."""
        cell = self._current_roi_cell()
        if cell is None:
            return
        index, roi_id = cell
        if index.column() not in NUMERIC_COLUMNS:
            self._show_notice("Paste works on the x, y and diameter columns.")
            return
        lines = QApplication.clipboard().text().strip().splitlines()
        text = lines[0].split("\t")[0] if lines else ""
        self._set_numeric_cells(self._selected_ids() or [roi_id], index.column(), text)

    def _fill_down(self) -> None:
        """The topmost selected row's value in the current column goes to the other selected rows."""
        cell = self._current_roi_cell()
        if cell is None or cell[0].column() not in NUMERIC_COLUMNS:
            self._show_notice("Fill down works on the x, y and diameter columns.")
            return
        column = cell[0].column()
        rows = [r for r in self._tree.selectionModel().selectedRows() if self._model.roi_id(r) is not None]
        if len(rows) < 2:
            self._show_notice("Select two or more ROIs to fill down.")
            return
        rows.sort(key=lambda r: self._tree.visualRect(r).top())
        text = str(rows[0].siblingAtColumn(column).data(Qt.ItemDataRole.EditRole) or "")
        self._set_numeric_cells([self._model.roi_id(r) for r in rows[1:]], column, text)  # type: ignore[misc]

    def _step_cell(self, index: QModelIndex, direction: int, large: bool) -> None:
        """Ctrl+wheel over a diameter cell: one step, on every selected ROI if the row is selected."""
        roi_id = self._model.roi_id(index)
        if roi_id is None or index.column() not in DIAMETER_COLUMNS:
            return
        stepped = step_text(str(index.data(Qt.ItemDataRole.EditRole) or ""), direction, large, self._model.unit())
        if stepped is not None:
            self._on_cell_edited("roi", roi_id, index.column(), stepped)

    # -- commands ------------------------------------------------------------------------------

    def _choose_color(self, initial_hex: str, title: str) -> str | None:
        color = QColorDialog.getColor(QColor(initial_hex), self, title)
        return color.name() if color.isValid() else None

    def _group_selected_or_new(self) -> None:
        ids = self._selected_ids()
        default = f"Group {len(self._toolbox.groups()) + 1}"
        name, accepted = QInputDialog.getText(
            self, "Group ROIs" if ids else "New group", "Group name", text=default
        )
        if not accepted:
            return
        if ids:
            self._guarded("Group ROIs", lambda: self._toolbox.group_rois(ids, name))
        else:
            self._guarded("New group", lambda: self._toolbox.create_group(name))

    def _add_selected_to_group(self, group_id: str) -> None:
        ids = self._selected_ids()
        if ids:
            self._guarded("Add to group", lambda: self._toolbox.add_rois_to_group(ids, group_id))

    def _ungroup_selected(self) -> None:
        ids = self._selected_ids()
        empty = [gid for gid in self._selected_group_ids() if not any(row.group_id == gid for row in self._model.rows())]
        if empty:  # an empty group has no ROIs to take out: it is just removed
            self._delete_groups(empty)
        if ids:
            self._guarded("Ungroup", lambda: self._toolbox.remove_rois_from_groups(ids))

    def _color_selected(self) -> None:
        rois, headers = self._view_selection()
        if headers and not rois:
            real = self._selected_group_ids()
            if real:
                self._recolor_groups(real)
                return
        ids = self._selected_ids()
        if not ids:
            return
        first = self._model.row_for(ids[0])
        color = self._choose_color(first.color_hex if first else "#f59e0b", "Choose ROI color")
        if color is not None:
            self._guarded("Set color", lambda: self._toolbox.set_roi_colors(ids, color))

    def _clear_color_selected(self) -> None:
        ids = self._selected_ids()
        if ids:
            self._guarded("Clear color", lambda: self._toolbox.set_roi_colors(ids, None))

    def _recolor_group(self, group_id: str) -> None:
        self._recolor_groups([group_id])

    def _recolor_groups(self, group_ids: list[str]) -> None:
        """One colour for every given group (each member gets a fresh tint of it)."""
        first = next((g for g in self._toolbox.groups() if g.group_id in group_ids), None)
        if first is None:
            return
        title = "Choose group color" if len(group_ids) == 1 else "Choose color for the groups"
        color = self._choose_color(first.sample_color_hex, title)
        if color is not None:
            for group_id in group_ids:
                self._guarded("Recolor group", lambda gid=group_id: self._toolbox.recolor_group(gid, color))

    def _on_chip_double_clicked(self, index: QModelIndex) -> None:
        if self._model.is_group(index):
            group_id = self._model.group_id(index)
            if group_id is not None:
                self._recolor_group(group_id)
            return
        roi_id = self._model.roi_id(index)
        if roi_id is None:
            return
        targets = self._edit_targets(roi_id)
        row = self._model.row_for(roi_id)
        color = self._choose_color(row.color_hex if row else "#f59e0b", "Choose ROI color")
        if color is not None:
            self._guarded("Set color", lambda: self._toolbox.set_roi_colors(targets, color))

    def _reset_diameters_selected(self) -> None:
        ids = self._selected_ids()
        if ids:
            self._guarded("Reset diameters", lambda: self._toolbox.reset_roi_diameters(ids, **self._edit_kwargs()))

    def _shift_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        unit = self._model.unit()
        dialog = ShiftDialog(len(ids), unit.label, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        dx, dy = dialog.shift()
        self._guarded("Shift", lambda: self._toolbox.translate_rois(ids, from_display(dx, unit), from_display(dy, unit), **self._edit_kwargs()))

    def _reorder(self, target_for: Callable[[tuple[int, ...], list[int]], int]) -> None:
        if not self._renumbering_allowed():
            return
        if self._model.sort_state() != (COLUMN_ID, False):
            self._show_notice("Sort by # (ascending) to reorder ROIs.", error=True)
            return
        ids = self._selected_ids()
        if not ids:
            return
        scope = movement_scope(ids, self._model.rows(), grouped=self._model.is_grouped_view())
        if scope is None:
            self._show_notice("Select ROIs from a single group to reorder them.", error=True)
            return
        target = target_for(scope, ids)
        self._guarded("Reorder", lambda: self._toolbox.move_in_order(ids, target, scope_ids=scope))

    def _move_selected(self, direction: int) -> None:
        rois, _headers = self._view_selection()
        group_ids = self._selected_group_ids()
        if group_ids and not rois and self._model.is_grouped_view():
            self._move_groups(group_ids, direction)  # only group headers are selected: move the groups themselves
            return
        self._reorder(lambda scope, ids: step_target_index(scope, ids, direction))

    def _move_to_edge(self, *, top: bool) -> None:
        self._reorder(lambda scope, ids: 0 if top else len(scope))

    def _delete_selected(self) -> None:
        """Delete the selected ROIs; a selected group header counts as all its ROIs."""
        ids = self._selected_ids()
        if ids:
            if self._renumbering_allowed():
                self._guarded("Delete", lambda: self._toolbox.delete_rois(tuple(ids)))
            return
        self._delete_groups(self._selected_group_ids())  # only empty groups selected: nothing but the groups to remove

    def _scope_for_drop(self, target: DropTarget) -> tuple[int, ...]:
        """The ROIs in the list a drop was aimed at, ascending by number: all
        of them for the flat list, else that group's (or "Ungrouped"'s)."""
        rows = self._model.rows()
        if target.kind == "flat":
            return tuple(sorted(row.roi_id for row in rows))
        return tuple(sorted(row.roi_id for row in rows if row.group_id == target.group_id))

    def _on_roi_dropped(self, ids: object, target: DropTarget) -> None:
        """ROIs were dragged and dropped. Onto a group header they join that
        group (onto "Ungrouped" they leave their groups). Between the rows of a
        list they are moved there, when they all come from that same list;
        otherwise (some come from another group) the drop puts them in the
        group whose list it landed in."""
        moved = sorted({int(roi_id) for roi_id in ids})  # type: ignore[union-attr]
        if target.kind != "flat":
            scope = self._scope_for_drop(target)
            if target.kind == "header" or any(roi_id not in scope for roi_id in moved):
                self._put_in_group(moved, target.group_id)
                return
        else:
            scope = self._scope_for_drop(target)
        if not self._renumbering_allowed():
            return
        if self._model.sort_state() != (COLUMN_ID, False):
            self._show_notice("Sort by # (ascending) to reorder ROIs by dragging.", error=True)
            return
        before = sum(1 for roi_id in scope[: target.position] if roi_id in set(moved))
        index = target.position - before
        self._guarded("Reorder", lambda: self._toolbox.move_in_order(moved, index, scope_ids=scope))

    def _put_in_group(self, roi_ids: list[int], group_id: str | None) -> None:
        if group_id is None:
            self._guarded("Ungroup", lambda: self._toolbox.remove_rois_from_groups(roi_ids))
        else:
            self._guarded("Add to group", lambda: self._toolbox.add_rois_to_group(roi_ids, group_id))

    def _move_groups(self, group_ids: list[str], direction: int) -> None:
        """Move the groups one place up (-1) or down (+1). The selected groups
        keep their order relative to each other; a block already at the end of
        the list stays there."""
        if self._groups_sorted_by_label():
            self._show_notice("Groups are sorted by label: sort by # to move them.", error=True)
            return
        chosen = set(group_ids)
        order = [group.group_id for group in self._toolbox.groups()]
        final = list(order)
        if direction < 0:
            for i in range(1, len(final)):
                if final[i] in chosen and final[i - 1] not in chosen:
                    final[i - 1], final[i] = final[i], final[i - 1]
        else:
            for i in range(len(final) - 2, -1, -1):
                if final[i] in chosen and final[i + 1] not in chosen:
                    final[i + 1], final[i] = final[i], final[i + 1]
        self._apply_group_order(order, final)

    def _groups_sorted_by_label(self) -> bool:
        """The grouped view lists the groups by label, not in their own order (moving them would show nothing)."""
        return self._model.is_grouped_view() and self._model.sort_state()[0] == COLUMN_NAME

    def _set_groups_expanded(self, indexes: list[QModelIndex], expanded: bool) -> None:
        """Expand or collapse these group headers; one already in that state stays as it is."""
        for index in indexes:
            if self._tree.isExpanded(index) != expanded:
                self._tree.setExpanded(index, expanded)

    def _move_groups_to_edge(self, group_ids: list[str], *, top: bool) -> None:
        """Put the groups at the top / bottom of the list, keeping their order relative to each other."""
        if self._groups_sorted_by_label():
            self._show_notice("Groups are sorted by label: sort by # to move them.", error=True)
            return
        chosen = set(group_ids)
        order = [group.group_id for group in self._toolbox.groups()]
        picked = [gid for gid in order if gid in chosen]
        others = [gid for gid in order if gid not in chosen]
        self._apply_group_order(order, picked + others if top else others + picked)

    def _apply_group_order(self, order: list[str], final: list[str]) -> None:
        """Reach ``final`` with `reorder_group` (one undo step per group that moves)."""
        if final == order:
            return
        self._reselect_groups = self._selected_group_ids()

        def run() -> None:
            for index, group_id in enumerate(final):
                self._toolbox.reorder_group(group_id, index)

        self._guarded("Move group", run)

    # -- context menu ---------------------------------------------------------------------------

    def _show_context_menu(self, pos: QPoint) -> None:
        index = self._tree.indexAt(pos)
        menu = QMenu(self)
        if not index.isValid():
            menu.addAction("New empty group…", self._group_selected_or_new_empty)
        elif self._model.is_group(index):
            self._fill_group_menu(menu, index)
        else:
            roi_id = self._model.roi_id(index)
            if roi_id is not None and roi_id not in self._selection.selected_roi_ids():
                self._selection.set_roi_selection({roi_id})
            self._fill_roi_menu(menu)
        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _group_selected_or_new_empty(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "New group", "Group name", text=f"Group {len(self._toolbox.groups()) + 1}"
        )
        if accepted:
            self._guarded("New group", lambda: self._toolbox.create_group(name))

    def _fill_roi_menu(self, menu: QMenu) -> None:
        ids = self._selected_ids()
        noun = "ROI" if len(ids) == 1 else f"{len(ids)} ROIs"
        menu.addAction(f"Group {noun}…", self._group_selected_or_new)
        groups = self._toolbox.groups()
        if groups:
            submenu = menu.addMenu("Add to group")
            for group in groups:
                submenu.addAction(group.name, lambda gid=group.group_id: self._add_selected_to_group(gid))
        in_group = any(row.group_id is not None for row in self._model.rows() if row.roi_id in set(ids))
        menu.addAction("Remove from group", self._ungroup_selected).setEnabled(in_group)
        menu.addSeparator()
        menu.addAction("Set color…", self._color_selected)
        menu.addAction("Clear color", self._clear_color_selected)
        menu.addAction("Reset diameters to default", self._reset_diameters_selected)
        menu.addAction("Shift position…", self._shift_selected)
        menu.addSeparator()
        movable = (
            self._model.sort_state() == (COLUMN_ID, False)
            and movement_scope(ids, self._model.rows(), grouped=self._model.is_grouped_view()) is not None
        )
        move_menu = menu.addMenu("Move")
        move_menu.setEnabled(movable)
        for text, handler in (
            ("To top", lambda: self._move_to_edge(top=True)),
            ("Up", lambda: self._move_selected(-1)),
            ("Down", lambda: self._move_selected(1)),
            ("To bottom", lambda: self._move_to_edge(top=False)),
        ):
            move_menu.addAction(text, handler)
        menu.addSeparator()
        menu.addAction(f"Delete {noun}", self._delete_selected)

    def _fill_group_menu(self, menu: QMenu, index: QModelIndex) -> None:
        group_id = self._model.group_id(index)
        selected = self._selected_group_ids()
        targets = selected if group_id in selected else ([group_id] if group_id is not None else [])
        _rois, headers = self._view_selection()
        shown = headers if any(header == index for header in headers) else [index]  # the headers this menu acts on
        menu.addAction("Expand", lambda: self._set_groups_expanded(shown, True))
        menu.addAction("Collapse", lambda: self._set_groups_expanded(shown, False))
        menu.addSeparator()
        if group_id is None:
            return  # "Ungrouped" is not a real group: nothing else applies
        many = len(targets) > 1
        order = [group.group_id for group in self._toolbox.groups()]
        positions = sorted(order.index(gid) for gid in targets if gid in order)
        at_top = positions == list(range(len(positions)))
        at_bottom = positions == list(range(len(order) - len(positions), len(order)))
        menu.addAction("Rename…", lambda: self._tree.edit(index)).setEnabled(not many)
        menu.addAction("Set color…", lambda: self._recolor_groups(targets))
        menu.addSeparator()
        move_menu = menu.addMenu("Move groups" if many else "Move group")
        move_menu.setEnabled(not self._groups_sorted_by_label())
        move_menu.addAction("To top", lambda: self._move_groups_to_edge(targets, top=True)).setEnabled(not at_top)
        move_menu.addAction("Up", lambda: self._move_groups(targets, -1)).setEnabled(not at_top)
        move_menu.addAction("Down", lambda: self._move_groups(targets, 1)).setEnabled(not at_bottom)
        move_menu.addAction("To bottom", lambda: self._move_groups_to_edge(targets, top=False)).setEnabled(not at_bottom)
        menu.addSeparator()
        menu.addAction(
            "Ungroup (keeps the ROIs)", lambda: self._delete_groups(targets)
        )
        menu.addAction("Delete groups and their ROIs" if many else "Delete group and its ROIs", self._delete_selected_groups_with_rois(targets))

    def _delete_selected_groups_with_rois(self, group_ids: list[str]) -> Callable[[], None]:
        def run() -> None:
            ids = sorted(row.roi_id for row in self._model.rows() if row.group_id in group_ids)
            if ids and self._renumbering_allowed():
                self._guarded("Delete", lambda: self._toolbox.delete_rois(tuple(ids)))
            elif not ids:
                self._delete_groups(group_ids)

        return run

    def _delete_groups(self, group_ids: list[str]) -> None:
        for group_id in group_ids:
            self._guarded("Delete group", lambda gid=group_id: self._toolbox.delete_group(gid))
