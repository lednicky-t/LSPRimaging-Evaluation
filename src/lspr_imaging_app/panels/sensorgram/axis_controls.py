"""The "Plot" group of the Sensorgram's View tab, and of the LbL and S_bulk plot windows: tick spacing, display range and grid of the two axes (2026-11-27).

Screen purpose: make a plot readable the way Origin or Excel let you: choose how far apart the labelled (major) and the small (minor) ticks stand, fix the part of the data that is shown
instead of the autoscale, and set the grid. One icon in the ribbon; a click opens the panel (a `GroupMenuButton`, the look of the Image ribbon's pop-ups).

Layout of the panel (one block per axis, then the shared rows)::

    X axis: Cube (#)                <- the quantity the axis shows now
    Ticks      [ auto ] [ auto ]    <- major step, minor step (a minor step needs a major one)
    Range      [ auto ] [ auto ] [Use view]   <- lowest and highest value shown (in the numbers the axis prints)
    Zero at    [ none ] [Step...]  <- the x value printed as 0 (the Sensorgram's x axis only)
    Grid       [x] show
    Breaks     [pencil] [Clear] 2 breaks     <- the Sensorgram's x axis only
    Lines      [Continue] [Break]            <- a line goes on across a break, or ends before it and starts again after it
    Y axis: Peak position (nm)
    ...
    Grid strength  [ 15 % ]
    <a note when a typed value was refused>
    [Reset to default]

Main user actions: type a number and press Enter (or leave the field); an empty field is "auto". "Use view" copies what is on screen now into the two range fields (zoom with the mouse, then
fix it). **Zero at** changes only the numbers printed on the x axis: they count from the value typed (or from the start of a plan step picked from the list), the ticks stand
on multiples of the step from there, and a typed range is in those numbers. Data, cursor, steps and tables keep the data's own x. The range replaces the autoscale: the "A" button and View All come back to it; an empty end stays automatic. The reset button puts these two axes (the quantities shown now) and the
grid strength back to automatic; the options of a quantity not shown now are kept.

Important states: the options belong to the **quantity** an axis shows (its label), so cubes and time, or a position and a shift, each remember their own; they are saved in the session
(`PlotAxesModule`, one per app, a plot id each). A typed value that makes no sense (a step of zero or below, a minor step not below the major one, a minimum not below the maximum, text that
is no number) is refused: the field goes back to what it was and the note under the grid strength says why. A tick step that would draw more than `MAX_TICKS` ticks is ignored while it would
(the tool tip says so); the typed value stays.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QSpinBox, QToolButton, QWidget

from ...domain.plot_axes import DEFAULT_GRID_ALPHA, GRID_ALPHA_RANGE, MAX_TICKS, AxisOptions, PlotAxes
from ...selection.plot_axes_module import PlotAxesModule
from ..spectra.view_menu import IconToggle
from .axis_break_icons import edit_breaks_icon
from .group_menu import GroupMenuButton, segment_stylesheet, segmented_buttons, widgets_row
from .plot_look import RESET_TIP, reset_button, tabler

AXES_TIP = (
    "Plot: how far apart the major and minor ticks of each axis stand, which part of the data is shown instead of the autoscale, and the grid. Saved with the session, separately for "
    "each quantity an axis shows (cubes or time, a position or a shift)."
)
MAJOR_TIP = f"Distance between the labelled ticks, in the axis' own unit. Empty = automatic. A step that would draw more than {MAX_TICKS} ticks across the view is ignored."
MINOR_TIP = f"Distance between the small ticks (smaller than the major step). Empty = none. Needs a major step. A step that would draw more than {MAX_TICKS} ticks is left out."
LOW_TIP = "Lowest value shown. Empty = the autoscale's own."
HIGH_TIP = "Highest value shown. Empty = the autoscale's own."
VIEW_TIP = "Use what is on screen now as the range of this axis (zoom with the mouse first, then press this)."
GRID_TIP = "Draw this axis' grid lines (the minor ticks' lines at half the strength)."
ALPHA_TIP = "How strong the grid lines are. 15 % is what the plots always had."
EDIT_BREAKS_TIP = (
    "Edit the axis breaks. The axis is drawn continuous with each break as a shaded span: click the plot to add a break there, drag a span's edges (or the span) to change what it leaves out, "
    "right-click a span to remove it. Switch it off to see the plot with the breaks."
)
CLEAR_BREAKS_TIP = "Remove every break of this axis."
JOIN_TIP = "Continue: a line goes on across a break (it runs from the last point before to the first point after). Break: it ends before the break and starts again after it."
JOIN_KEYS = ("continue", "break")
JOIN_LABELS = {"continue": "Continue", "break": "Break"}
JOIN_TIPS = {"continue": "A line goes on across a break.", "break": "A line ends before a break and starts again after it."}
ZERO_TIP = (
    "The x value printed as 0: the numbers on the axis count from it (for example 0 at the start of the step where the solution changed). Only the numbers on the axis change: the ticks "
    "stand on multiples of the step from this value, and a typed range is in these numbers. The data, the cursor, the steps and the tables keep the data's own x. Empty = no zero."
)
ZERO_STEP_TIP = "Put the zero at the start of a plan step (the list is the steps of the plan bar now)."
ZERO_STEP_NONE = "Step..."
SIDE_TITLES = {"x": "X axis", "y": "Y axis"}


def _format(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


class _Side:
    """The fields of one axis."""

    def __init__(self, parent: QWidget, side: str, prefix: str) -> None:
        self.side = side
        self.major, self.minor, self.low, self.high = (_field(parent, f"{prefix}_{side}_{name}", tip) for name, tip in (("major", MAJOR_TIP), ("minor", MINOR_TIP), ("low", LOW_TIP), ("high", HIGH_TIP)))
        self.use_view = QToolButton(parent)
        self.use_view.setObjectName(f"{prefix}_{side}_use_view")
        self.use_view.setText("Use view")
        self.use_view.setToolTip(VIEW_TIP)
        self.use_view.setAutoRaise(True)
        self.grid = QCheckBox("show", parent)
        self.grid.setObjectName(f"{prefix}_{side}_grid")
        self.grid.setChecked(True)
        self.grid.setToolTip(GRID_TIP)
        self.ticks_row = widgets_row(parent, self.major, self.minor)
        self.range_row = widgets_row(parent, self.low, self.high, self.use_view)

    def fields(self) -> tuple[QLineEdit, ...]:
        return self.major, self.minor, self.low, self.high


class _StepCombo(QComboBox):
    """A drop-down of the plan steps; it asks for the list each time it opens, so it never shows steps of an earlier plan. The first entry is the caption; picking a step reports its x."""

    step_x_chosen = pyqtSignal(float)

    def __init__(self, parent: QWidget, steps: Callable[[], list[tuple[str, float]]]) -> None:
        super().__init__(parent)
        self._steps = steps
        self._xs: list[float] = []
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.addItem(ZERO_STEP_NONE)
        self.activated.connect(self._on_activated)

    def showPopup(self) -> None:  # Qt signature
        self.clear()
        self.addItem(ZERO_STEP_NONE)
        self._xs = []
        for label, x in self._steps():
            self.addItem(label)
            self._xs.append(float(x))
        super().showPopup()

    def _on_activated(self, index: int) -> None:
        if 1 <= index <= len(self._xs):
            self.step_x_chosen.emit(self._xs[index - 1])
        self.setCurrentIndex(0)  # a caption again: it is an action, not a state


def _field(parent: QWidget, name: str, tip: str) -> QLineEdit:
    box = QLineEdit(parent)
    box.setObjectName(name)
    box.setPlaceholderText("auto")
    box.setToolTip(tip)
    box.setFixedWidth(64)
    return box


class AxisControls(QObject):
    break_edit_requested = pyqtSignal(bool)  # the user switched the break edit mode on or off; the owner starts it (and may refuse: `set_break_edit_state(False)`)
    axes_changed = pyqtSignal()  # what the plot should draw its axes with changed (an edit, a restored session, another quantity on an axis): `axes()` has the new value

    def __init__(
        self,
        parent: QWidget,
        module: PlotAxesModule | None,
        plot_id: str,
        labels: Callable[[], tuple[str, str]],
        current_view: Callable[[], tuple[tuple[float, float], tuple[float, float]]],
        *,
        object_prefix: str = "axes",
        breaks: bool = False,
        zero_steps: Callable[[], list[tuple[str, float]]] | None = None,
    ) -> None:
        """`module`: the session's (None: a private one, for a plot with no session). `labels()`: the quantities the x and y axes show now. `current_view()`: the visible
        ((x low, x high), (y low, y high)) for "Use view". `breaks`: the x axis can have breaks (the Sensorgram's): a row with the edit mode, a Clear button and the count. `zero_steps()`: the plan's steps as (label, x of its start); given = the x axis has a "Zero at" row (the Sensorgram's)."""
        super().__init__(parent)
        self._module = module if module is not None else PlotAxesModule()
        self._plot_id, self._labels, self._current_view = plot_id, labels, current_view
        self._syncing = False
        self._shown_labels: tuple[str, str] | None = None
        self._sides = {side: _Side(parent, side, object_prefix) for side in ("x", "y")}
        self.alpha = QSpinBox(parent)
        self.alpha.setObjectName(f"{object_prefix}_grid_alpha")
        self.alpha.setRange(round(GRID_ALPHA_RANGE[0] * 100), round(GRID_ALPHA_RANGE[1] * 100))
        self.alpha.setValue(round(DEFAULT_GRID_ALPHA * 100))
        self.alpha.setSuffix(" %")
        self.alpha.setToolTip(ALPHA_TIP)
        self.alpha.setKeyboardTracking(False)
        self.note = QLabel("", parent)
        self.note.setObjectName(f"{object_prefix}_note")
        self.note.setWordWrap(True)
        self.note.setMaximumWidth(260)
        self.edit_breaks = IconToggle(edit_breaks_icon, EDIT_BREAKS_TIP, parent)
        self.edit_breaks.setObjectName(f"{object_prefix}_edit_breaks")
        self.clear_breaks = QToolButton(parent)
        self.clear_breaks.setObjectName(f"{object_prefix}_clear_breaks")
        self.clear_breaks.setText("Clear")
        self.clear_breaks.setToolTip(CLEAR_BREAKS_TIP)
        self.clear_breaks.setAutoRaise(True)
        self.join_row, self._join_group, self.join_buttons = segmented_buttons(parent, JOIN_KEYS, JOIN_LABELS, JOIN_TIPS, f"{object_prefix}_join")
        self.join_row.setToolTip(JOIN_TIP)
        self.break_count = QLabel("", parent)
        self.break_count.setObjectName(f"{object_prefix}_break_count")
        self._has_breaks = bool(breaks)
        self._has_zero = zero_steps is not None
        self.zero = _field(parent, f"{object_prefix}_x_zero", ZERO_TIP)
        self.zero.setPlaceholderText("none")
        self.zero_step = _StepCombo(parent, zero_steps if zero_steps is not None else (lambda: []))
        self.zero_step.setObjectName(f"{object_prefix}_x_zero_step")
        self.zero_step.setToolTip(ZERO_STEP_TIP)
        self._reset = reset_button(parent, f"{object_prefix}_reset", self.reset)
        self._reset.setToolTip(RESET_TIP)
        rows: list[tuple[str, QWidget | None]] = []
        for side, block in self._sides.items():
            rows += [(SIDE_TITLES[side], None), ("Ticks", block.ticks_row), ("Range", block.range_row)]
            if side == "x" and self._has_zero:
                rows.append(("Zero at", widgets_row(parent, self.zero, self.zero_step)))
            rows.append(("Grid", block.grid))
            if side == "x" and self._has_breaks:
                rows.append(("Breaks", widgets_row(parent, self.edit_breaks, self.clear_breaks, self.break_count)))
                rows.append(("Lines", self.join_row))
        rows += [("Grid strength", self.alpha), ("", self.note)]
        if not self._has_breaks:  # kept (they hold no value) but in no panel
            for widget in (self.edit_breaks, self.clear_breaks, self.break_count, self.join_row):
                widget.hide()
        if not self._has_zero:
            for widget in (self.zero, self.zero_step):
                widget.hide()
        self.menu = GroupMenuButton(tabler("chart-line"), AXES_TIP, rows, parent, footer=self._reset)
        for side, block in self._sides.items():
            for box in block.fields():
                box.editingFinished.connect(self._commit)
            block.grid.toggled.connect(self._commit)
            block.use_view.clicked.connect(lambda _checked=False, which=side: self._use_view(which))
        self.alpha.valueChanged.connect(self._commit)
        self.zero.editingFinished.connect(self._commit)
        self.zero_step.step_x_chosen.connect(self._zero_from_step)
        for button in self.join_buttons.values():
            button.toggled.connect(self._commit)
        self.edit_breaks.toggled.connect(lambda on: None if self._syncing else self.break_edit_requested.emit(bool(on)))
        self.clear_breaks.clicked.connect(lambda _checked=False: self.set_x_breaks(()))
        self._module.changed.connect(self._module_changed)
        for button in self.join_buttons.values():
            button.setStyleSheet(segment_stylesheet())
        self.refresh()

    # -- reading and writing ------------------------------------------------------------------------------------------------------

    @property
    def module(self) -> PlotAxesModule:
        """The module these controls write to (the session's, or the private one of a plot with no session): the guides' controls must write to the same one."""
        return self._module

    def axes(self) -> PlotAxes:
        """What the plot draws its axes with now (the session's options for the quantities shown)."""
        return self._module.axes(self._plot_id, *self._labels())

    def labels_changed(self) -> None:
        """An axis shows another quantity (or its first one): show that quantity's options, and tell the plot. Does nothing when the labels are the ones already shown."""
        if self._labels() != self._shown_labels:
            self.refresh()
            self.axes_changed.emit()

    def refresh(self) -> None:
        """Put the session's options for the quantities shown now into the fields (no change is reported)."""
        labels = self._labels()
        axes = self._module.axes(self._plot_id, *labels)
        self._syncing = True
        try:
            for side, block in self._sides.items():
                options = axes.side(side)
                for box, value in zip(block.fields(), (options.major, options.minor, options.low, options.high)):
                    box.setText(_format(value))
                block.minor.setEnabled(options.major is not None)
                block.grid.setChecked(options.grid)
                if side == "x":
                    self.zero.setText(_format(options.zero))
                self.menu.set_heading_text(0 if side == "x" else 1, f"{SIDE_TITLES[side]}: {labels[0 if side == 'x' else 1]}")
            self.alpha.setValue(round(axes.grid_alpha * 100))
            count = len(axes.x.breaks)
            self.break_count.setText("none" if not count else f"{count} break" + ("" if count == 1 else "s"))
            self.clear_breaks.setEnabled(bool(count))
            self.join_buttons["continue" if axes.x.join_lines else "break"].setChecked(True)
        finally:
            self._syncing = False
        self._shown_labels = labels

    def _module_changed(self, plot: str) -> None:
        if plot == self._plot_id:
            self.refresh()
            self.axes_changed.emit()

    def _parse_side(self, side: str) -> AxisOptions | str:
        """The options typed for `side`, or the text saying why they are refused."""
        block, name = self._sides[side], SIDE_TITLES[side]
        numbers: list[float | None] = []
        for box, caption in zip(block.fields(), ("major step", "minor step", "minimum", "maximum")):
            text = box.text().strip().replace(",", ".")
            if not text:
                numbers.append(None)
                continue
            try:
                value = float(text)
            except ValueError:
                return f"{name}: '{box.text().strip()}' is not a number ({caption})."
            if value != value or abs(value) == float("inf"):
                return f"{name}: the {caption} must be a finite number."
            numbers.append(value)
        major, minor, low, high = numbers
        if major is not None and major <= 0.0:
            return f"{name}: the major step must be above zero."
        if major is None:
            minor = None  # a minor step has no meaning without a major one (its field is greyed out): clearing the major step clears it too
        if minor is not None and (minor <= 0.0 or minor >= major):
            return f"{name}: the minor step must be above zero and below the major step."
        if low is not None and high is not None and low >= high:
            return f"{name}: the minimum must be below the maximum."
        stored = self._module.axes(self._plot_id, *self._labels()).side(side)  # the breaks are set on the plot, not typed here: a commit keeps them
        zero = stored.zero
        if side == "x" and self._has_zero:
            text = self.zero.text().strip().replace(",", ".")
            zero = None
            if text:
                try:
                    zero = float(text)
                except ValueError:
                    return f"{name}: '{self.zero.text().strip()}' is not a number (zero)."
                if zero != zero or abs(zero) == float("inf"):
                    return f"{name}: the zero must be a finite number."
        join = stored.join_lines if not (side == "x" and self._has_breaks) else self.join_buttons["continue"].isChecked()
        return AxisOptions(major=major, minor=minor, low=low, high=high, grid=block.grid.isChecked(), zero=zero, guides=stored.guides, join_lines=join, breaks=stored.breaks)

    def _commit(self, *_args: object) -> None:
        if self._syncing:
            return
        parsed = {side: self._parse_side(side) for side in self._sides}
        problems = [value for value in parsed.values() if isinstance(value, str)]
        self.note.setText(" ".join(problems))
        if problems:
            # A refused side goes back to what the session has; the other side's edit is still taken.
            current = self._module.axes(self._plot_id, *self._labels())
            parsed = {side: (current.side(side) if isinstance(value, str) else value) for side, value in parsed.items()}
        axes = PlotAxes(parsed["x"], parsed["y"], self.alpha.value() / 100.0)
        self._module.set_axes(self._plot_id, *self._labels(), axes)
        self.refresh()  # a refused field goes back; a corrected one shows as stored (the module emits only on a real change, so the plot is told there)

    def set_x_breaks(self, breaks) -> None:
        """The plot (or the Clear button) set the breaks of the x quantity shown now: keep them in the session; the plot is told through `axes_changed`."""
        current = self._module.axes(self._plot_id, *self._labels())
        self._module.set_axes(self._plot_id, *self._labels(), current.with_side("x", replace(current.x, breaks=tuple(breaks))))

    def set_break_edit_state(self, on: bool) -> None:
        """Show the edit mode as on or off without reporting it (the plot ended it, or refused it)."""
        was, self._syncing = self._syncing, True
        try:
            self.edit_breaks.setChecked(bool(on))
        finally:
            self._syncing = was

    def say(self, text: str) -> None:
        """Put a message in the note under the grid strength (why something was refused)."""
        self.note.setText(text)

    def _zero_from_step(self, x: float) -> None:
        self.zero.setText(f"{x:.6g}")
        self._commit()

    def _use_view(self, side: str) -> None:
        (x_low, x_high), (y_low, y_high) = self._current_view()
        low, high = (x_low, x_high) if side == "x" else (y_low, y_high)
        if side == "x":  # the range fields are in the numbers the axis prints
            zero = self._module.axes(self._plot_id, *self._labels()).x.zero or 0.0
            low, high = low - zero, high - zero
        block = self._sides[side]
        block.low.setText(f"{low:.6g}")
        block.high.setText(f"{high:.6g}")
        self._commit()

    def reset(self) -> None:
        """Back to automatic for the two quantities shown now, and the default grid strength. The guides stay (they have their own "Clear all" in the Guides panel)."""
        self.note.setText("")
        guides = self._module.axes(self._plot_id, *self._labels()).y.guides
        self._module.set_axes(self._plot_id, *self._labels(), PlotAxes(y=AxisOptions(guides=guides)))
        self.refresh()

    def refresh_theme(self) -> None:
        self.menu.refresh_theme()
        for button in self.join_buttons.values():
            button.setStyleSheet(segment_stylesheet())
