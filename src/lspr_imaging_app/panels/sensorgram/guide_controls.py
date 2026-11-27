"""The guides of the Plot group (Sensorgram View tab, LbL and S_bulk windows): horizontal reference lines at a y value (2026-11-27). The table, and every way to change a guide.

Screen purpose: mark a place on the y axis (a baseline position, a threshold, the value of a control) with a line behind the data, an optional label, a dash style, a colour, and
optionally its y value written on it. A guide is made, moved and removed on the plot itself (below) or edited in this table, which always shows every guide and its parameters.

Layout of the table (a `GroupMenuButton` pop-up, the look of the other View-tab pop-ups; a fixed number of rows, `MAX_GUIDES`, so the panel never changes size while it is open)::

    Guides: Peak position (nm)        <- the quantity the y axis shows now
         Value    Label        Line      Colour    y
    1   [ 650 ] [ Baseline   ] [Dashed v] [Red   v] [x]
    2   [     ] [            ] [Dashed v] [Auto  v] [ ]
    ...
    [Clear all]
    <a note when a typed value was refused>

Ways to change a guide (all end in `_set_guides`, so they are saved with the session and drawn at once):
- **In the table**: type a number and press Enter (or leave the field); empty the value to remove the guide; the label, line, colour and y boxes are as they look.
- **On the plot** (`SensorgramPlot`): right-click in the plot, "Add guide here" (at the pointer's y, writing its value); select a step (or several) on the plan bar, right-click, "Add guide
  from step" (at the mean of the plotted lines' means in the step, labelled with the step); drag a guide (open hand, closed while held); right-click on a guide for its menu (`show_menu`):
  Rename, Colour, Show y value, Remove.
- The windows (LbL, S_bulk) have the table and the dragging.

Important states: guides belong to the **y quantity** shown (like the other Plot options): another quantity (a shift instead of a position) shows its own guides. They are saved with the
session in the same block as the axis options (`PlotAxesModule`, the y side's `guides`), so this object writes there and the plot is redrawn through the same signal as for a tick step
(`AxisControls.axes_changed`). A value that is no finite number is refused: the row goes back to what it was and the note says why. Rows left empty in the middle close up on the next
refresh. When every row is used, a new guide is refused and the note says so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import QObject, QPoint, QSize
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QCheckBox, QColorDialog, QComboBox, QInputDialog, QLabel, QLineEdit, QMenu, QToolButton, QWidget

from ...domain.guide_lines import MAX_GUIDES, MAX_LABEL_CHARS, STYLES, Guide
from ...selection.plot_axes_module import PlotAxesModule
from ..menu_icons import give_icon
from .axis_break_icons import guides_menu_icon
from .group_menu import GroupMenuButton, widgets_row

GUIDES_TIP = (
    "Guides: horizontal reference lines at a value of the y axis (a baseline, a threshold), drawn behind the data. Add one with a right-click in the plot, or from a selected step; drag it "
    "to move it; right-click it for Rename, Colour and Remove. Saved with the session, separately for each quantity the y axis shows."
)
VALUE_TIP = "The y value the guide stands at (in the axis' unit). Empty = no guide in this row."
LABEL_TIP = f"Text written above the line at its right end (at most {MAX_LABEL_CHARS} characters). Optional."
STYLE_TIP = "How the line is drawn."
COLOR_TIP = "Colour of the line and its label. Auto follows the theme."
SHOW_TIP = "Write the guide's y value on the line (after its label)."
CLEAR_TIP = "Remove every guide of this quantity."
FULL_NOTE = f"All {MAX_GUIDES} guide rows are used: remove a guide first."
STYLE_NAMES = {"dash": "Dashed", "solid": "Solid", "dot": "Dotted"}
COLORS: tuple[tuple[str, str | None], ...] = (
    ("Auto", None), ("Red", "#d62728"), ("Orange", "#ff7f0e"), ("Green", "#2ca02c"), ("Blue", "#1f77b4"), ("Violet", "#9467bd"), ("Black", "#000000"), ("White", "#ffffff"),
)
_SWATCH_PX = 10
_WIDTHS = {"value": 64, "label": 96, "style": 76, "color": 76, "show": 24}  # the columns of the table, header and rows alike


def _swatch(color: str | None) -> QIcon:
    pixmap = QPixmap(_SWATCH_PX, _SWATCH_PX)
    pixmap.fill(QColor("#8a8f98") if color is None else QColor(color))
    return QIcon(pixmap)


def _format(value: float) -> str:
    return f"{value:.10g}"


class _Slot:
    """The fields of one guide row."""

    def __init__(self, parent: QWidget, prefix: str, number: int) -> None:
        self.value = QLineEdit(parent)
        self.value.setObjectName(f"{prefix}_{number}_value")
        self.value.setPlaceholderText("none")
        self.value.setToolTip(VALUE_TIP)
        self.value.setFixedWidth(_WIDTHS["value"])
        self.label = QLineEdit(parent)
        self.label.setObjectName(f"{prefix}_{number}_label")
        self.label.setPlaceholderText("label")
        self.label.setToolTip(LABEL_TIP)
        self.label.setMaxLength(MAX_LABEL_CHARS)
        self.label.setFixedWidth(_WIDTHS["label"])
        self.style = QComboBox(parent)
        self.style.setObjectName(f"{prefix}_{number}_style")
        self.style.setToolTip(STYLE_TIP)
        self.style.setFixedWidth(_WIDTHS["style"])
        for key in STYLES:
            self.style.addItem(STYLE_NAMES[key], key)
        self.color = QComboBox(parent)
        self.color.setObjectName(f"{prefix}_{number}_color")
        self.color.setToolTip(COLOR_TIP)
        self.color.setFixedWidth(_WIDTHS["color"])
        self.color.setIconSize(QSize(_SWATCH_PX, _SWATCH_PX))
        for name, color in COLORS:
            self.color.addItem(_swatch(color), name, color)
        self.show_value = QCheckBox(parent)
        self.show_value.setObjectName(f"{prefix}_{number}_show_value")
        self.show_value.setToolTip(SHOW_TIP)
        self.show_value.setFixedWidth(_WIDTHS["show"])
        self.row = widgets_row(parent, self.value, self.label, self.style, self.color, self.show_value)

    def show_guide(self, guide: Guide | None) -> None:
        self.value.setText("" if guide is None else _format(guide.value))
        self.label.setText("" if guide is None else guide.label)
        self.style.setCurrentIndex(0 if guide is None else max(0, self.style.findData(guide.style)))
        self.color.setCurrentIndex(self._color_index(None if guide is None else guide.color))
        self.show_value.setChecked(False if guide is None else guide.show_value)

    def _color_index(self, color: str | None) -> int:
        index = self.color.findData(color)
        if index < 0:  # a colour of a session file that the list does not have: shown as "Custom" and kept
            if self.color.itemText(self.color.count() - 1) != "Custom":
                self.color.addItem(_swatch(color), "Custom", color)
            else:
                self.color.setItemIcon(self.color.count() - 1, _swatch(color))
                self.color.setItemData(self.color.count() - 1, color)
            index = self.color.count() - 1
        return index

    def fields(self) -> tuple[QLineEdit, QLineEdit]:
        return self.value, self.label


def _header(parent: QWidget) -> QWidget:
    """The caption row of the table, its columns as wide as the rows' fields."""
    labels = []
    for key, text in (("value", "Value"), ("label", "Label"), ("style", "Line"), ("color", "Colour"), ("show", "y")):
        label = QLabel(text, parent)
        label.setFixedWidth(_WIDTHS[key])
        labels.append(label)
    return widgets_row(parent, *labels)


class GuideControls(QObject):
    def __init__(self, parent: QWidget, module: PlotAxesModule | None, plot_id: str, labels: Callable[[], tuple[str, str]], *, object_prefix: str = "guides") -> None:
        """`module`: the session's (None: a private one). `labels()`: the quantities the x and y axes show now (the guides belong to the y one)."""
        super().__init__(parent)
        self._module = module if module is not None else PlotAxesModule()
        self._plot_id, self._labels = plot_id, labels
        self._parent_widget = parent
        self._syncing = False
        self._slots = [_Slot(parent, object_prefix, number) for number in range(1, MAX_GUIDES + 1)]
        self.note = QLabel("", parent)
        self.note.setObjectName(f"{object_prefix}_note")
        self.note.setWordWrap(True)
        self.note.setMaximumWidth(340)
        self.clear_button = QToolButton(parent)
        self.clear_button.setObjectName(f"{object_prefix}_clear")
        self.clear_button.setText("Clear all")
        self.clear_button.setToolTip(CLEAR_TIP)
        self.clear_button.setAutoRaise(True)
        rows: list[tuple[str, QWidget | None]] = [("Guides", None), ("", _header(parent))]
        rows += [(str(number), slot.row) for number, slot in enumerate(self._slots, start=1)]
        rows.append(("", self.note))
        self.menu = GroupMenuButton(guides_menu_icon, GUIDES_TIP, rows, parent, footer=self.clear_button)
        for slot in self._slots:
            for box in slot.fields():
                box.editingFinished.connect(self._commit)
            slot.style.activated.connect(self._commit)
            slot.color.activated.connect(self._commit)
            slot.show_value.toggled.connect(self._commit)
        self.clear_button.clicked.connect(lambda _checked=False: self._set_guides(()))
        self._module.changed.connect(self._module_changed)
        self.refresh()

    # -- reading and writing ------------------------------------------------------------------------------------------------------

    def guides(self) -> tuple[Guide, ...]:
        """The guides of the y quantity shown now (from the session)."""
        return self._module.axes(self._plot_id, *self._labels()).y.guides

    def labels_changed(self) -> None:
        """The y axis shows another quantity: show that quantity's guides."""
        self.refresh()

    def refresh(self) -> None:
        guides = self.guides()
        self._syncing = True
        try:
            for index, slot in enumerate(self._slots):
                slot.show_guide(guides[index] if index < len(guides) else None)
            self.menu.set_heading_text(0, f"Guides: {self._labels()[1]}")
            self.clear_button.setEnabled(bool(guides))
        finally:
            self._syncing = False

    def _module_changed(self, plot: str) -> None:
        if plot == self._plot_id:
            self.refresh()

    def _commit(self, *_args: object) -> None:
        if self._syncing:
            return
        stored = self.guides()
        guides: list[Guide] = []
        problems: list[str] = []
        for number, slot in enumerate(self._slots, start=1):
            text = slot.value.text().strip().replace(",", ".")
            if not text:
                continue  # an empty value field: no guide in this row
            try:
                value = float(text)
            except ValueError:
                problems.append(f"Guide {number}: '{slot.value.text().strip()}' is not a number.")
                if number - 1 < len(stored):
                    guides.append(stored[number - 1])  # the row goes back to what the session has
                continue
            guide = Guide(value, slot.label.text(), slot.color.currentData(), slot.style.currentData(), slot.show_value.isChecked()).sanitized()
            if guide is None:
                problems.append(f"Guide {number}: the value must be a finite number.")
                if number - 1 < len(stored):
                    guides.append(stored[number - 1])
                continue
            guides.append(guide)
        self.note.setText(" ".join(problems))
        self._set_guides(tuple(guides))

    def _set_guides(self, guides: tuple[Guide, ...]) -> None:
        current = self._module.axes(self._plot_id, *self._labels())
        self._module.set_axes(self._plot_id, *self._labels(), replace(current, y=replace(current.y, guides=tuple(guides))))
        self.refresh()  # a refused row goes back; the module emits only on a real change, so the plot is told there

    def say(self, text: str) -> None:
        self.note.setText(text)

    # -- changes made on the plot ---------------------------------------------------------------------------------------------------

    def add_guides(self, new: list[Guide]) -> int:
        """Add guides (made on the plot): as many as there are free rows; the note says so when some did not fit. Returns how many were added."""
        current = list(self.guides())
        room = MAX_GUIDES - len(current)
        kept = [g for g in (item.sanitized() for item in new) if g is not None]
        added = kept[: max(room, 0)]
        self.note.setText(FULL_NOTE if len(added) < len(kept) else "")
        if added:
            self._set_guides(tuple(current + added))
        return len(added)

    def add_guide(self, guide: Guide) -> bool:
        return self.add_guides([guide]) == 1

    def move_guide(self, index: int, value: float) -> None:
        """A guide was dragged to `value`."""
        guides = list(self.guides())
        if 0 <= index < len(guides):
            moved = replace(guides[index], value=float(value)).sanitized()
            if moved is not None and moved != guides[index]:
                guides[index] = moved
                self._set_guides(tuple(guides))

    def remove_guide(self, index: int) -> None:
        guides = list(self.guides())
        if 0 <= index < len(guides):
            del guides[index]
            self._set_guides(tuple(guides))

    def change_guide(self, index: int, **fields: object) -> None:
        """Change fields of one guide (label, color, style, show_value)."""
        guides = list(self.guides())
        if 0 <= index < len(guides):
            changed = replace(guides[index], **fields).sanitized()  # type: ignore[arg-type]
            if changed is not None and changed != guides[index]:
                guides[index] = changed
                self._set_guides(tuple(guides))

    # -- the guide's own menu (a right-click on it) ------------------------------------------------------------------------------------

    def build_menu(self, index: int, parent: QWidget | None = None) -> QMenu | None:
        """The menu of guide `index`: Rename, Colour, Show y value, Remove. None when there is no such guide."""
        guides = self.guides()
        if not 0 <= index < len(guides):
            return None
        guide = guides[index]
        menu = QMenu(parent or self._parent_widget)
        header = menu.addAction(f"Guide {guide.value:g}" + (f": {guide.label}" if guide.label else ""))
        header.setEnabled(False)
        menu.addSeparator()
        give_icon(menu.addAction("Rename..."), "rename").triggered.connect(lambda _checked=False: self.rename(index))
        give_icon(menu.addAction("Colour..."), "colour").triggered.connect(lambda _checked=False: self.choose_color(index))
        if guide.color is not None:
            give_icon(menu.addAction("Default colour"), "colour_clear").triggered.connect(lambda _checked=False: self.change_guide(index, color=None))
        show = give_icon(menu.addAction("Show y value"), "show_value")
        show.setCheckable(True)
        show.setChecked(guide.show_value)
        show.triggered.connect(lambda checked=False: self.change_guide(index, show_value=bool(checked)))
        menu.addSeparator()
        give_icon(menu.addAction("Remove guide"), "delete").triggered.connect(lambda _checked=False: self.remove_guide(index))
        return menu

    def show_menu(self, index: int, screen_pos: QPoint) -> None:
        menu = self.build_menu(index)
        if menu is not None:
            menu.exec(screen_pos)
            menu.deleteLater()

    def rename(self, index: int) -> None:
        guides = self.guides()
        if not 0 <= index < len(guides):
            return
        text, ok = QInputDialog.getText(self._parent_widget, "Rename guide", "Label (empty = none):", text=guides[index].label)
        if ok:
            self.change_guide(index, label=text)

    def choose_color(self, index: int) -> None:
        guides = self.guides()
        if not 0 <= index < len(guides):
            return
        current = QColor(guides[index].color or "#8a8f98")
        color = QColorDialog.getColor(current, self._parent_widget, "Choose the colour of the guide")
        if color.isValid():
            self.change_guide(index, color=color.name())

    def refresh_theme(self) -> None:
        self.menu.refresh_theme()
