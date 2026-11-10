"""Sensorgram ribbon, "Baseline" group: the drift-baseline controls (2026-11-09).

Screen purpose: choose how the baseline that "Subtract" takes off is made (`analysis/baseline_drift.py`): the mean of one range of the trace (the
method it always was), a curve through plan steps marked as anchors, or a curve through anchor points clicked on the plot.

Main user actions:
- Method pick-up: Range (saved as "window") / Anchor points.
- Shape pick-up (Anchor points, or Range with marked steps): constant, linear, quadratic, cubic, PCHIP.
- Border offset box (Range with marked steps): cubes left out at both ends of every anchor step.
- Anchor-point edit toggle (Anchor points only): while on, a left click on the plot adds an anchor point at the nearest cube, a right click on a
  point's square removes it, a right click anywhere else opens `fill_context_menu` (the cube cursor, the plot's own menu and the plan bar wait until
  it is off).
- Anchors menu (Anchor points only): whom new points apply to (all ROIs / the lines shown), how many cubes each point averages, clearing the points,
  and what the last fit said (anchor scatter, lines that could not be corrected and why).
- Anchor steps (Range only): `fill_step_menu` adds "make / remove baseline anchor" to a plan step's right-click menu; the panel offers it while the
  Range pencil is on (`SensorgramPanel._baseline_step_menu`).

Layout: five controls in the existing Baseline group, after the Subtract switch (Method, Shape, offset, edit pencil, Anchors menu), each shown only
when it means something for the chosen method; the Range method's own pencil and band menu belong to the panel.

Important states: no anchors, or fewer than the shape needs -> every line says so and is left uncorrected (never a silent switch to a simpler shape,
and marked steps never fall back to the range's mean). The user is told what is missing where they act (2026-11-10): anchor points by the number
beside the edit cross-hair; anchor steps by a short note at the pointer when a mark (or a new shape) leaves too few (`steps_note`, `say`), by the first
line of the anchor items in the step's right-click menu, and in the Shape pick-up's tool tip.

Display only: the values live in `StatisticsSettings` (owner `AnalysisSettingsModule`, saved with the session); this file reads and writes them there.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QMenu, QSpinBox, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.baseline_drift import ALL_ROIS, METHOD_ANCHORS, METHOD_WINDOW, MIN_ANCHORS, SHAPES, TRIM_RANGE, points_for, uses_anchor_steps
from ...analysis.settings import StatisticsSettings
from ...analysis.settings_module import AnalysisSettingsModule
from ..spectra.pick_menu import PickMenu, PictureFor
from ..ribbon_fields import style_ribbon_number_field
from ..spectra.view_menu import IconToggle
from .stat_icons import _pen, _picture

METHOD_LABELS = {METHOD_WINDOW: "Range", METHOD_ANCHORS: "Anchor points"}
METHOD_TIPS = {
    METHOD_WINDOW: "The mean of each trace inside the baseline range (the selector band) is taken off: removes an offset. With the pencil on, right-click "
    "plan steps to make them baseline anchors: then the curve of the chosen shape through those steps is taken off instead (removes a drift too).",
    METHOD_ANCHORS: "A curve through the anchor points you click on the plot (e.g. at the end of the buffer step at the start and at the end) is taken "
    "off. Removes the drift too.",
}
SHAPE_LABELS = {"constant": "Constant", "linear": "Linear", "quadratic": "Quadratic", "cubic": "Cubic", "pchip": "PCHIP (smooth, through every anchor)"}
SHAPE_TIPS = {  # "anchors": anchor points, or the Range method's anchor steps
    "constant": "The mean level of every cube in the anchors (needs 1 anchor).",
    "linear": "A straight line fitted through every cube of the anchors (needs 2 anchors), so it follows the trend inside them too. Extrapolated beyond "
    "the first and last anchor.",
    "quadratic": "A parabola fitted through every cube of the anchors (needs 2 anchors). With only two, its curvature comes from the trends inside "
    "them, which is noisy: check the result. Extrapolated beyond the first and last anchor.",
    "cubic": "A cubic fitted through every cube of the anchors (needs 2 anchors). With only two, its shape comes from the trends inside them, which is "
    "noisy: check the result. Extrapolated beyond the first and last anchor.",
    "pchip": "A smooth curve through the mean of each anchor that never overshoots between them (needs 2). Held flat beyond the first and last "
    "anchor. It stays on the means: it does not follow a trend inside an anchor.",
}
SHAPE_NAMES = {key: label.split(" (")[0] for key, label in SHAPE_LABELS.items()}  # the label without its explanation: "PCHIP"
SHAPE_BUTTON_TIP = (
    "Shape of the baseline curve through the anchors (anchor points, or the plan steps marked as anchors). A shape that needs more anchors than "
    "there are leaves the line uncorrected."
)
SCOPE_ALL, SCOPE_SHOWN = "all", "shown"  # whom a new anchor point applies to
HALFWIDTH_MAX = 50


def pencil_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """The baseline's edit toggles (anchor points, and the Range method's selector band): the standard pencil, in ``on_color`` while editing."""
    return load_tabler_icon("pencil", color=on_color if on else off_color, size=24)


def method_picture(kind: str) -> PictureFor:
    """``window``: one shaded range at the start with a flat dashed line on from it; ``anchors``: a hollow square near each end and a sloped dashed line joining them."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        if kind == METHOD_WINDOW:
            shade = QColor(theme.accent_blue)
            shade.setAlpha(90)
            painter.fillRect(QRectF(1.5, 3.0, 5.0, 15.0), shade)
            painter.setPen(_pen(ink, 1.3, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(1.5, 12.0), QPointF(18.5, 12.0))
            return
        painter.setPen(_pen(ink, 1.3, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(1.5, 14.0), QPointF(18.5, 9.0))
        painter.setPen(_pen(QColor(theme.accent_gold), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for x, y in ((4.0, 13.3), (16.0, 9.7)):
            painter.drawRect(QRectF(x - 2.0, y - 2.0, 4.0, 4.0))  # the hollow squares the plot draws at the anchors

    return _picture(draw_into)


_ANCHOR_DOTS = ((3.0, 13.0), (10.0, 7.0), (17.0, 11.0))  # three anchor levels, in 20 x 20 shown pixels


def shape_picture(kind: str) -> PictureFor:
    """Three anchor dots and the curve of the shape through them (constant: their mean; linear: a sloped line; quadratic: an arch; cubic: an S; PCHIP: through every dot)."""

    def draw_into(painter: QPainter, ink: QColor, theme) -> None:
        painter.setPen(_pen(QColor(theme.accent_gold), 1.4))
        path = QPainterPath()
        if kind == "constant":
            path.moveTo(1.0, 10.3)
            path.lineTo(19.0, 10.3)
        elif kind == "linear":
            path.moveTo(1.0, 11.8)
            path.lineTo(19.0, 8.8)
        elif kind == "quadratic":
            path.moveTo(1.0, 15.0)
            path.quadTo(10.0, 0.5, 19.0, 13.5)
        elif kind == "cubic":
            path.moveTo(1.0, 16.0)
            path.cubicTo(7.0, -2.0, 13.0, 18.0, 19.0, 6.0)
        else:
            path.moveTo(1.0, 13.0)
            path.lineTo(3.0, 13.0)
            path.cubicTo(6.0, 13.0, 7.0, 7.0, 10.0, 7.0)
            path.cubicTo(13.0, 7.0, 14.0, 11.0, 17.0, 11.0)
            path.lineTo(19.0, 11.0)
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x, y in _ANCHOR_DOTS:
            painter.drawEllipse(QPointF(x, y), 1.6, 1.6)

    return _picture(draw_into)


_SUMMARY_NOTES_MAX = 4  # lines named one by one in the Anchors menu; the rest are counted


def drift_summary(series: Sequence) -> list[str]:
    """What the last fit said, for the bottom of the Anchors menu: the anchor scatter left after correction, and the lines that could not be corrected (why)."""
    rms = [float(s.anchor_rms) for s in series if np.isfinite(s.anchor_rms)]
    lines: list[str] = []
    if rms:
        lines.append(f"Left at the anchors after correction: median {np.median(rms):.4f} nm RMS over {len(rms)} line{'s' if len(rms) != 1 else ''}")
    failed = [s for s in series if s.baseline_note]
    for s in failed[:_SUMMARY_NOTES_MAX]:
        lines.append(f"{s.label}: {s.baseline_note}")
    if len(failed) > _SUMMARY_NOTES_MAX:
        lines.append(f"... and {len(failed) - _SUMMARY_NOTES_MAX} more lines without a baseline")
    return lines


def anchor_count(stats: StatisticsSettings) -> int:
    """How many anchor points there are (a point at one cube counts once, shared or not)."""
    return len({c for _r, c in stats.baseline_anchor_points})


class AnchorsButton(QToolButton):
    """The Anchors menu. Rebuilt every time it opens, so it counts the ROIs plotted as they are at that moment."""

    def __init__(self, controls: DriftBaselineControls, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controls = controls
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._rebuild)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)  # the anchor icon and the number of points
        self.setIconSize(QSize(18, 18))
        self.setToolTip(
            "Anchor points (the number is how many): whom new points apply to, how many cubes each averages, clearing them, and what the last fit "
            "left at the anchors."
        )
        self.refresh_theme()

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.setIcon(load_tabler_icon("anchor", color=theme.text_primary, size=18))
        self.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; padding: 0 12px 0 6px; color: {theme.text_primary}; font-weight: 400; }}"
            f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
            "QToolButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; right: 2px; }"
        )

    def _rebuild(self) -> None:
        self._menu.clear()
        self._controls.fill_anchor_menu(self._menu)


class DriftBaselineControls(QObject):
    """The controls of the drift baseline (`widgets`), and the edits behind them. `shown_rois`: the ROIs plotted now."""

    def __init__(
        self,
        parent: QWidget,
        settings: AnalysisSettingsModule,
        shown_rois: Callable[[], Sequence[int]],
        cube_axis: Callable[[], tuple[np.ndarray, np.ndarray] | None] = lambda: None,
    ) -> None:
        """`cube_axis`: (the plot's x of each cube, the cube number of each) of the scan drawn now, None without one; the anchor points need it."""
        super().__init__(parent)
        self._settings = settings
        self._shown_rois = shown_rois
        self._cube_axis = cube_axis
        self.point_scope = SCOPE_ALL
        self.scope_changed = None  # set by the panel: called with the new scope, to remember it
        # set by the panel: `say` shows a short note at the pointer; `steps_in_plan` counts the marked anchor steps the plan still has (None: every mark counts)
        self.say: Callable[[str], None] | None = None
        self.steps_in_plan: Callable[[], int] | None = None
        self._syncing = False
        self._summary: list[str] = []
        self._uncorrected = ""  # why the last drawn fit left lines uncorrected ("" = none were)
        self.method = PickMenu(parent, name_on_face=False)
        for key in (METHOD_WINDOW, METHOD_ANCHORS):
            self.method.add_choice(key, METHOD_LABELS[key], METHOD_TIPS[key], method_picture(key))
        self.method.finish()
        self.method.setToolTip("How the baseline is made.")
        self.shape = PickMenu(parent, name_on_face=False)
        for key in SHAPES:
            self.shape.add_choice(key, SHAPE_LABELS[key], SHAPE_TIPS[key], shape_picture(key))
        self.shape.finish()
        self.shape.setToolTip(SHAPE_BUTTON_TIP)
        self.anchors = AnchorsButton(self, parent)
        self.edit = IconToggle(
            pencil_icon,
            "Anchor points: while this is on, a left click on the plot adds an anchor point at the nearest cube, a right click on a point's square "
            "removes it, and a right click anywhere else opens a menu (add here, clear all, ...). Each point is the mean of the cubes around it "
            "(Anchors menu). The cube cursor waits until this is off.",
            parent,
        )
        # the Range method's border offset: cubes left out at each end of every anchor step (shown while steps are marked)
        self.trim = QSpinBox(parent)
        self.trim.setRange(*TRIM_RANGE)
        self.trim.setPrefix("offset ")
        self.trim.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
        self.trim.setToolTip(
            "Cubes left out at both borders of every anchor step: right after a solution change (and right before the next) the signal is not "
            "baseline yet. 0 = the whole step."
        )
        style_ribbon_number_field(self.trim, f"offset {TRIM_RANGE[1]}")
        self.method.currentIndexChanged.connect(self._on_edited)
        self.shape.currentIndexChanged.connect(self._on_edited)
        self.trim.valueChanged.connect(lambda value: None if self._syncing else self.set_step_trim(value))

    def widgets(self) -> tuple[QWidget, ...]:
        return (self.method, self.shape, self.trim, self.edit, self.anchors)

    def refresh_theme(self) -> None:
        self.method.refresh_theme()
        self.shape.refresh_theme()
        self.anchors.refresh_theme()
        self.edit.refresh_theme()

    # -- settings <-> controls ------------------------------------------------------------------------------------------------

    def sync(self, stats: StatisticsSettings) -> None:
        self._syncing = True
        try:
            self.method.setCurrentIndex(max(self.method.findData(stats.baseline_method), 0))
            self.shape.setCurrentIndex(max(self.shape.findData(stats.baseline_shape), 0))
            self.trim.setValue(int(stats.baseline_step_trim))
            anchors = stats.baseline_method == METHOD_ANCHORS
            steps = uses_anchor_steps(stats)  # Range with marked anchor steps
            for widget in (self.anchors, self.edit):
                widget.setVisible(anchors)
            self.shape.setVisible(anchors or steps)
            self.trim.setVisible(steps)
            if not anchors and self.edit.isChecked():
                self.edit.setChecked(False)  # no anchors to edit with the window method
            self.anchors.setText(str(anchor_count(stats)))  # the button's face: the anchor icon and the number of points
        finally:
            self._syncing = False

    def _on_edited(self, *_args: object) -> None:
        if self._syncing:
            return
        self._settings.set_statistics_settings(replace(
            self._settings.statistics_settings(),
            baseline_method=self.method.currentData(), baseline_shape=self.shape.currentData(),
        ))
        self._say_steps_note()  # a shape that needs more anchor steps than are marked: said at once

    def set_fit_summary(self, lines: Sequence[str], uncorrected: str = "") -> None:
        """What the last drawn fit said (shown at the bottom of the Anchors menu). `uncorrected`: why lines were left uncorrected ("" = none were);
        it goes into the Shape pick-up's tool tip and the step menu, the places the Range method's anchor steps have (no Anchors menu there)."""
        self._summary = list(lines)
        self._uncorrected = str(uncorrected)
        self.shape.setToolTip(SHAPE_BUTTON_TIP + (f"\n\nNot corrected now: {self._uncorrected}." if self._uncorrected else ""))

    # -- the anchors menu ------------------------------------------------------------------------------------------------------

    def fill_anchor_menu(self, menu: QMenu) -> None:
        """One compact row of icon buttons (maintainer, 2026-11-09): new points for all ROIs / for the lines shown, ± cubes each point averages,
        clear every point. Below it one small dim line: the point counts and what the last fit said. Every button explains itself in its tooltip."""
        stats = self._settings.statistics_settings()
        theme = get_active_theme()
        holder = QWidget(menu)
        holder.setStyleSheet(
            f"QToolButton {{ border: none; border-radius: 3px; padding: 3px; background: transparent; }}"
            f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
            f"QToolButton:checked {{ background: {theme.control_bg_hover}; border: 1px solid {theme.accent_blue}; }}"
            f"QLabel#anchor_notes {{ color: {theme.text_dim}; font-size: 8pt; }}"
        )
        column = QVBoxLayout(holder)
        column.setContentsMargins(6, 4, 6, 4)
        column.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(2)
        column.addLayout(row)

        def icon_button(name: str, icon: str, tip: str, checkable: bool = False) -> QToolButton:
            button = QToolButton(holder)
            button.setObjectName(name)
            button.setIcon(load_tabler_icon(icon, color=theme.text_primary, size=18))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tip)
            button.setCheckable(checkable)
            row.addWidget(button)
            return button

        count_icon = QLabel(holder)
        count_icon.setPixmap(load_tabler_icon("anchor", color=theme.text_primary, size=18).pixmap(18, 18))
        row.addWidget(count_icon)
        count = QLabel(str(anchor_count(stats)), holder)  # the number of anchor points, at the head of the row
        count.setObjectName("anchor_count")
        count.setToolTip("Number of anchor points.")
        count.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600; padding-right: 6px;")
        row.addWidget(count)
        scope_group = QButtonGroup(holder)
        for scope, name, icon, tip in (
            (SCOPE_ALL, "anchor_scope_all", "stack-3", "New points apply to all ROIs (shared points)."),
            (SCOPE_SHOWN, "anchor_scope_shown", "eye", "New points apply to the lines shown only (their own points)."),
        ):
            button = icon_button(name, icon, tip, checkable=True)
            button.setChecked(self.point_scope == scope)
            button.clicked.connect(lambda _c=False, picked=scope: self.set_point_scope(picked))
            scope_group.addButton(button)
        row.addSpacing(6)
        box = QSpinBox(holder)
        box.setObjectName("anchor_halfwidth")
        box.setRange(0, HALFWIDTH_MAX)
        box.setPrefix("± ")
        box.setValue(int(stats.baseline_point_halfwidth))
        box.setKeyboardTracking(False)
        box.setToolTip("Each point averages this many cubes on each side of it (0 = the point's own cube only).")
        box.valueChanged.connect(self.set_halfwidth)
        row.addWidget(box)
        row.addSpacing(6)
        clear = icon_button("anchor_clear", "trash-x", "Clear every anchor point (shared and own).")
        clear.setEnabled(bool(stats.baseline_anchor_points))
        clear.clicked.connect(self.clear_points)
        clear.clicked.connect(menu.close)
        row.addStretch(1)

        n_shared = sum(1 for r, _c in stats.baseline_anchor_points if r == ALL_ROIS)
        n_own = len(stats.baseline_anchor_points) - n_shared
        n = anchor_count(stats)
        notes = [f"{n} anchor point{'s' if n != 1 else ''}: {n_shared} shared, {n_own} own"] if n else ["No anchor points yet."]
        notes += self._summary
        label = QLabel("\n".join(notes), holder)
        label.setObjectName("anchor_notes")
        label.setWordWrap(True)
        column.addWidget(label)
        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        menu.addAction(action)

    def fill_context_menu(self, menu: QMenu, x: float) -> None:
        """The anchor edit's right-click menu (a right click away from every anchor square; on a square the click removes it): add a point here,
        remove the nearest, whom new points apply to, clear all, stop editing."""
        theme = get_active_theme()

        def icon(name: str) -> QIcon:
            return load_tabler_icon(name, color=theme.text_primary, size=18)

        stats = self._settings.statistics_settings()
        menu.addAction(icon("circle-plus"), "Add anchor point here").triggered.connect(lambda: self.add_point_at(x))
        nearest = menu.addAction(icon("minus"), "Remove the nearest anchor point")
        nearest.setEnabled(bool(self.shown_points()))
        nearest.triggered.connect(lambda: self.remove_point_at(x))
        menu.addSeparator()
        for scope, name, text in ((SCOPE_ALL, "stack-3", "New points apply to all ROIs"), (SCOPE_SHOWN, "eye", "New points apply to the lines shown only")):
            action = menu.addAction(icon(name), text)
            action.setCheckable(True)
            action.setChecked(self.point_scope == scope)
            action.triggered.connect(lambda _c=False, picked=scope: self.set_point_scope(picked))
        menu.addSeparator()
        clear = menu.addAction(icon("trash-x"), "Clear all anchor points")
        clear.setEnabled(bool(stats.baseline_anchor_points))
        clear.triggered.connect(self.clear_points)
        menu.addAction(icon("x"), "Stop editing anchor points").triggered.connect(lambda: self.edit.setChecked(False))

    # -- the Range method's anchor steps ------------------------------------------------------------------------------------

    def set_anchor_steps(self, refs: Sequence[tuple], anchored: bool) -> None:
        """Mark these plan steps (`EffectiveStep.ref`) as Range baseline anchors, or take them out."""
        stats = self._settings.statistics_settings()
        current = set(stats.baseline_anchor_steps)
        changed = current | {tuple(r) for r in refs} if anchored else current - {tuple(r) for r in refs}
        self._settings.set_statistics_settings(replace(stats, baseline_anchor_steps=tuple(changed)))
        self._say_steps_note()  # e.g. the first mark with the Linear shape: one more step is needed before anything is taken off

    def steps_needed(self) -> tuple[int, int]:
        """(anchor steps the chosen shape needs, marked steps the plan has) for the Range method with marked steps; (0, 0) when it is not in use."""
        stats = self._settings.statistics_settings()
        if not uses_anchor_steps(stats):
            return 0, 0
        marked = len(stats.baseline_anchor_steps) if self.steps_in_plan is None else int(self.steps_in_plan())
        return MIN_ANCHORS.get(stats.baseline_shape, 0), marked

    def steps_note(self, short: bool = False) -> str:
        """What the chosen shape still needs of the Range method's anchor steps, "" when enough are marked. Nothing is switched for the user (a shape
        is never swapped for a simpler one): the note says what to do. `short`: the one line of the step menu."""
        need, marked = self.steps_needed()
        if marked >= need:
            return ""
        name = SHAPE_NAMES.get(self._settings.statistics_settings().baseline_shape, "The shape")
        needs = f"{name} needs {need} anchor step{'s' if need != 1 else ''}"
        if short:
            return f"{needs}: {marked} marked"
        simpler = ", or choose the Constant shape" if marked >= 1 else ""
        return f"{needs}, {marked} marked: mark {need - marked} more{simpler}. Until then the lines are not corrected."

    def _say_steps_note(self) -> None:
        note = self.steps_note()
        if note and self.say is not None:
            self.say(note)

    def clear_anchor_steps(self) -> None:
        self._settings.set_statistics_settings(replace(self._settings.statistics_settings(), baseline_anchor_steps=()))

    def set_step_trim(self, cubes: int) -> None:
        self._settings.set_statistics_settings(replace(self._settings.statistics_settings(), baseline_step_trim=int(cubes)))

    def fill_step_menu(self, menu: QMenu, steps: Sequence) -> None:
        """A right-click on a plan step with the Range pencil on (2026-11-09): the items at the top of the step's menu that make the step (or the selected
        steps) baseline anchors, take them out, or clear every anchor step. `steps`: the `EffectiveStep`s the click acts on (the selection when the
        clicked step is in it). The step menu's own title and items stay around them. When lines are uncorrected, a first greyed line says why
        (too few steps for the shape, or what the last fit said, e.g. no cube left after the offset)."""
        theme = get_active_theme()
        stats = self._settings.statistics_settings()
        marked = set(stats.baseline_anchor_steps)
        refs = [tuple(s.ref) for s in steps]
        many = len(refs) > 1
        why = self.steps_note(short=True) or (f"Not corrected: {self._uncorrected}" if marked and self._uncorrected else "")
        if why:
            menu.addAction(load_tabler_icon("alert-triangle", color=theme.text_dim, size=18), why).setEnabled(False)
        make = menu.addAction(
            load_tabler_icon("anchor", color=theme.text_primary, size=18),
            f"Make the {len(refs)} steps baseline anchors" if many else "Make this step a baseline anchor",
        )
        make.setEnabled(any(r not in marked for r in refs))
        make.triggered.connect(lambda: self.set_anchor_steps(refs, True))
        remove = menu.addAction(
            load_tabler_icon("minus", color=theme.text_primary, size=18),
            "Remove the steps from the baseline anchors" if many else "Remove this step from the baseline anchors",
        )
        remove.setEnabled(any(r in marked for r in refs))
        remove.triggered.connect(lambda: self.set_anchor_steps(refs, False))
        if marked:
            clear = menu.addAction(load_tabler_icon("trash-x", color=theme.text_primary, size=18), f"Clear all anchor steps ({len(marked)})")
            clear.triggered.connect(self.clear_anchor_steps)

    # -- the anchor points -------------------------------------------------------------------------------------------------

    def set_point_scope(self, scope: str) -> None:
        """Whom a new anchor point applies to: every ROI (`SCOPE_ALL`) or only the ROIs plotted now (`SCOPE_SHOWN`)."""
        self.point_scope = SCOPE_SHOWN if scope == SCOPE_SHOWN else SCOPE_ALL
        if self.scope_changed is not None:
            self.scope_changed(self.point_scope)

    def set_halfwidth(self, cubes: int) -> None:
        self._settings.set_statistics_settings(replace(self._settings.statistics_settings(), baseline_point_halfwidth=int(cubes)))

    def clear_points(self) -> None:
        self._settings.set_statistics_settings(replace(self._settings.statistics_settings(), baseline_anchor_points=()))

    def _nearest(self, x: float, cubes: Sequence[int] | None = None) -> int | None:
        """The cube number nearest to plot x (among `cubes`, or among every cube of the scan). None without a scan or a finite x."""
        axis = self._cube_axis()
        if axis is None:
            return None
        xs, numbers = np.asarray(axis[0], dtype=np.float64), np.asarray(axis[1])
        keep = np.isfinite(xs)
        if cubes is not None:
            keep &= np.isin(numbers, np.asarray(list(cubes)))
        if not keep.any():
            return None
        candidates = np.flatnonzero(keep)
        return int(numbers[candidates[np.argmin(np.abs(xs[candidates] - float(x)))]])

    def shown_points(self) -> tuple[int, ...]:
        """The cube numbers of the anchor points of the ROIs plotted now (the shared ones and their own)."""
        points = self._settings.statistics_settings().baseline_anchor_points
        return tuple(sorted({c for r in self._shown_rois() for c in points_for(r, points)} | {c for r, c in points if r == ALL_ROIS}))

    def points_needed(self) -> int:
        """How many more anchor points the chosen shape needs before the lines shown can be corrected (0 = enough): the shape's minimum minus the
        points of the line that has fewest (shared + its own, only points on cubes of the scan shown)."""
        stats = self._settings.statistics_settings()
        need = MIN_ANCHORS.get(stats.baseline_shape, 0)
        axis = self._cube_axis()
        in_scan = set(int(c) for c in np.asarray(axis[1])) if axis is not None else None
        rois = tuple(int(r) for r in self._shown_rois()) or (ALL_ROIS,)
        fewest = min(sum(1 for c in points_for(r, stats.baseline_anchor_points) if in_scan is None or c in in_scan) for r in rois)
        return max(0, need - fewest)

    def add_point_at(self, x: float) -> int | None:
        """A left click in the edit mode: an anchor point at the cube nearest to x, for every ROI or for the ROIs shown (`point_scope`). Returns the cube."""
        cube = self._nearest(x)
        if cube is None:
            return None
        owners = (ALL_ROIS,) if self.point_scope == SCOPE_ALL else tuple(int(r) for r in self._shown_rois())
        stats = self._settings.statistics_settings()
        self._settings.set_statistics_settings(replace(stats, baseline_anchor_points=(*stats.baseline_anchor_points, *((r, cube) for r in owners))))
        return cube

    def remove_point_at(self, x: float) -> int | None:
        """A right click in the edit mode: the anchor point of the lines shown nearest to x goes, wherever it is kept (the shared one and the shown ROIs' own
        at that cube): what is drawn is what goes. Returns the cube, None when there was none."""
        cube = self._nearest(x, self.shown_points())
        if cube is None:
            return None
        shown = {int(r) for r in self._shown_rois()} | {ALL_ROIS}
        stats = self._settings.statistics_settings()
        kept = tuple((r, c) for r, c in stats.baseline_anchor_points if not (c == cube and r in shown))
        self._settings.set_statistics_settings(replace(stats, baseline_anchor_points=kept))
        return cube
