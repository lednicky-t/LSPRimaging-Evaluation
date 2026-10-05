"""Shared crosshair cursor-readout toggle for plot panels (Histogram,
Image) - maintainer's request, 2026-09-29: "implement... cursor icon on
both histogram and image panel... copy all functions, as hiding, showing
values" from the stable app's cursor-toggle half of
`gui/plot_overlay_controller.py`. Its separate "stats" overlay (peak/range
readout) is not ported - not requested.

A small crosshair-icon `QToolButton` toggles two `InfiniteLine`s plus a live
text readout under the mouse (rate-limited via `pg.SignalProxy`, same as
stable). Lives in `panels/` directly rather than under `histogram/` or
`image/` - a cross-panel shared widget, same convention `dock_container.py`/
`panel_visibility.py` already establish at this level.

**What this class does not know**: how to interpret a raw view-space point.
Histogram wants "snap to the nearest bin, read that curve's value";
Image wants "floor to the pixel under the cursor, read its intensity" -
genuinely different per-panel logic, so it is supplied as `value_at`
rather than guessed at here. Positioning is the same story: each panel
already repositions its own floating overlays (the settings gear, the
Highlight readout, the crop/measure controls) from its own `resizeEvent`/
`sigTransformChanged` handlers, so this class only exposes the button
itself (`icon_label`) for the owner to `.move()`/`.raise_()` wherever it
decides the icon belongs, rather than hard-coding a corner that might
collide with a sibling overlay (Histogram's settings gear already occupies
the top-right corner).
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import GuiTheme, load_tabler_icon, tint_tabler_icon, transparent_icon_button_stylesheet

_TOOLTIP = "Cursor readout under the mouse pointer. Click to show/hide."


class CursorOverlay(QObject):
    """Toggleable crosshair + value readout for one pyqtgraph plot."""

    toggled = pyqtSignal(bool)  # the new on/off state, after every toggle

    def __init__(
        self,
        *,
        scene_view,
        plot_item: pg.PlotItem,
        overlay_parent: QWidget,
        value_at: Callable[[float, float], tuple[float, float, str] | None],
        theme: GuiTheme,
        on_changed: Callable[[], None] | None = None,
        readout_sink: Callable[[str], None] | None = None,
    ) -> None:
        """`scene_view` is the `QGraphicsView` the plot lives in (a
        `pg.PlotWidget` or `pg.GraphicsLayoutWidget` - both qualify, both
        expose `.scene()`/`.sceneBoundingRect()`); `plot_item` is the
        `pg.PlotItem` to add the crosshair lines to and read `.vb` from for
        scene<->view coordinate mapping. `value_at(view_x, view_y)` resolves
        a raw mouse position to `(snapped_x, snapped_y, label_text)`, or
        `None` to show nothing (e.g. the cursor is off the data). `on_changed`
        fires after every toggle and every mouse-move update - the label's
        size changes between the small icon and (usually wider) live text,
        so the owner needs a chance to `adjustSize()`/reposition each time,
        the same "called on every update" shape stable's own `reposition()`
        calls in `_handle_mouse_moved`/`_toggle_cursor` follow.

        `readout_sink` (2026-10-03, Image panel): when given, the live text
        goes to `readout_sink(text)` (an empty string = hide) instead of into
        the button, which then stays a fixed-size icon that only shows an
        on/off tint - so the button can sit in a left-aligned ribbon group
        without its width changing with every mouse move."""
        super().__init__(overlay_parent)
        self._scene_view = scene_view
        self._plot_item = plot_item
        self._value_at = value_at
        self._on_changed = on_changed
        self._readout_sink = readout_sink
        self._enabled = False

        self._vline = pg.InfiniteLine(angle=90, movable=False)
        self._hline = pg.InfiniteLine(angle=0, movable=False)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        plot_item.addItem(self._vline, ignoreBounds=True)
        plot_item.addItem(self._hline, ignoreBounds=True)

        # A real `QToolButton`, not a `QLabel` with a hand-rolled click
        # handler - CLAUDE.md's own GUI-testability rule ("prefer real
        # QAbstractButton subclasses... a QLabel-based 'button' has no
        # .click() method and is unreachable by UI-automation tooling"),
        # and the pattern this rewrite already uses everywhere else a small
        # icon needs to be clickable (`workflow/collapsible_section.py`,
        # `workflow/reference_frame_row.py`). `setCheckable` gives `.click()`
        # a real toggle to drive; `toggle()` below stays the single source
        # of truth for `_enabled` and keeps the button's own checked-state
        # in sync, rather than trusting two independent state trackers to
        # agree.
        # No `setFixedSize` here, deliberately - unlike the settings gear
        # button (always icon-sized), this one must be free to grow when
        # `toggle()`/`_on_mouse_moved` switch it to showing live text (e.g.
        # "42345 DN, 12.3"), which would clip against an icon-sized fixed
        # box. The owning panel's `adjustSize()` + reposition (`on_changed`)
        # is what makes that resize actually take effect on screen.
        self._icon_button = QToolButton(overlay_parent)
        self._icon_button.setCheckable(True)
        self._icon_button.setAutoRaise(True)
        self._icon_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._icon_button.setIconSize(QSize(theme.compact_icon_inner, theme.compact_icon_inner))
        self._icon_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._icon_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_button.setToolTip(_TOOLTIP)
        self._icon_button.clicked.connect(self.toggle)

        self.refresh_theme(theme)

        # rateLimit=60 matches stable's own choice (plot_overlay_controller.py) -
        # a live per-pixel readout with no throttling floods the event loop
        # for no visible benefit above 60Hz.
        self._proxy = pg.SignalProxy(scene_view.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved)

    @property
    def icon_label(self) -> QToolButton:
        """For the owning panel's own positioning code to `.move()`/
        `.adjustSize()`/`.raise_()` - see the module docstring on why
        positioning is not this class's job. Named for the role it plays
        (a small floating readout), not its current widget class."""
        return self._icon_button

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._theme = theme
        pen = pg.mkPen(theme.text_muted, width=1)
        self._vline.setPen(pen)
        self._hline.setPen(pen)
        if not self._enabled or self._readout_sink is not None:
            self._show_off_icon()

    def _show_off_icon(self) -> None:
        # `ToolButtonIconOnly` vs `ToolButtonTextOnly` (toggled here and in
        # `toggle()`) is what actually makes icon/text mutually exclusive -
        # a `QToolButton` shows both at once by default if both are set;
        # merely setting an empty icon or empty text is not enough on its
        # own to switch which one the current style renders.
        self._icon_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        # With a readout sink the icon is the whole button, so "on" is shown
        # by tint (accent) instead of by text replacing the icon.
        color = self._theme.accent_blue if self._enabled and self._readout_sink is not None else self._theme.text_muted
        icon = tint_tabler_icon(load_tabler_icon("crosshair"), QColor(color))
        self._icon_button.setIcon(icon)

    def toggle(self) -> None:
        """Matches stable's own `_toggle_cursor`: turning on shows a "-"
        placeholder immediately (not the icon) so the label's occupied space
        is already text-shaped before the first mouse-move update arrives;
        turning off reverts to the icon and stays there - the readout does
        not fall back to the icon just because the mouse leaves the plot,
        only when explicitly toggled off. Single source of truth for
        `_enabled` regardless of entry point (a real click, or a direct
        `.toggle()` call) - explicitly syncs the button's own checked-state
        rather than trusting Qt's automatic click-driven toggle alone."""
        self._enabled = not self._enabled
        self._icon_button.setChecked(self._enabled)
        self._vline.setVisible(self._enabled)
        self._hline.setVisible(self._enabled)
        if self._readout_sink is not None:
            self._show_off_icon()
            if not self._enabled:
                self._readout_sink("")
        elif self._enabled:
            self._icon_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._icon_button.setIcon(QIcon())
            self._icon_button.setText("-")
        else:
            self._show_off_icon()
        if self._on_changed is not None:
            self._on_changed()
        self.toggled.emit(self._enabled)

    def set_enabled(self, enabled: bool) -> None:
        if bool(enabled) != self._enabled:
            self.toggle()

    def _on_mouse_moved(self, event: tuple) -> None:
        if not self._enabled:
            return
        pos = event[0]
        if not self._plot_item.vb.sceneBoundingRect().contains(pos):
            return
        mouse_point = self._plot_item.vb.mapSceneToView(pos)
        result = self._value_at(float(mouse_point.x()), float(mouse_point.y()))
        if result is None:
            return
        x, y, text = result
        self._vline.setPos(x)
        self._hline.setPos(y)
        if self._readout_sink is not None:
            self._readout_sink(text)
        else:
            self._icon_button.setText(text)
        if self._on_changed is not None:
            self._on_changed()
