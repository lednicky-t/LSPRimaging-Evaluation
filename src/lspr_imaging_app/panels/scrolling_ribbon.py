"""``ScrollingRibbon`` - a ribbon row that scrolls sideways when its panel is narrower than the row (2026-10-24).

A control row (tabs, icon groups, fields) is as wide as the sum of its controls, and Qt does not let a dock be
narrower than its widest child, so before this the Image panel could not go below ~760 px and Spectra / Sensorgram
had a floor of their own. This wrapper reports a *small* minimum width instead and, when the row does not fit,
shows a chevron at each edge (like the tab strip of a browser) that scrolls the row. Nothing is clipped without a
way to reach it, nothing is re-laid-out, and there is no scroll bar (so the ribbon never gets taller).

* **Arrows** show only while the row overflows. A click moves a step, holding the button repeats, and an arrow is
  dimmed at its end. The mouse wheel over an empty part of the ribbon scrolls too (a drop-down or a number field
  under the pointer keeps its own wheel: it changes its value).
* **Height** follows the row's height (`sizeHint`), vertical policy fixed, so `RibbonDrawer` can still roll it up
  by animating `maximumHeight`.
* **`ensure_visible(widget)`** brings a child (the tab that was just chosen) into view.

The row keeps its own minimum width (`content.minimumSizeHint()`): that is the scrollable width.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

ARROW_WIDTH = 16
_MIN_VIEW_WIDTH = 24  # the least of the row that stays visible between the two arrows
_STEP_PX = 60
_REPEAT_DELAY_MS = 300
_REPEAT_INTERVAL_MS = 45


class _Area(QScrollArea):
    """The scroll area itself: no frame, no bars, transparent, and a wheel that moves the row sideways."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)  # the row still stretches over spare room; it never gets narrower than its minimum
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(_MIN_VIEW_WIDTH, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        widget = self.widget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def wheelEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        bar = self.horizontalScrollBar()
        if bar.maximum() <= 0:
            event.ignore()
            return
        delta = event.angleDelta()
        units = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        if units == 0:
            event.ignore()
            return
        bar.setValue(bar.value() - int(units))  # one wheel notch is 120 units: about 120 px of travel
        event.accept()


class ScrollingRibbon(QWidget):
    """*content* (any widget; it becomes the scroll area's widget) with an arrow at each edge while it overflows."""

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = content
        self._area = _Area(self)
        self._area.setWidget(content)
        content.setAutoFillBackground(False)  # QScrollArea.setWidget switches it on; the ribbon is drawn on the panel's own background
        self._left = self._make_arrow("chevron-left", -1)
        self._right = self._make_arrow("chevron-right", +1)
        self._overflowing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._left)
        layout.addWidget(self._area, 1)
        layout.addWidget(self._right)
        # Preferred across, fixed up and down: the height is the row's, the width is whatever the panel gives.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        bar = self._area.horizontalScrollBar()
        bar.valueChanged.connect(self._refresh_arrows)
        bar.rangeChanged.connect(lambda _lo, _hi: self._refresh_arrows())
        content.installEventFilter(self)  # a layout change inside the row may change the width it needs or its height
        self.refresh_theme()
        self._update_overflow()

    # -- public ----------------------------------------------------------------------------

    def is_overflowing(self) -> bool:
        """True while the row is wider than the room it has, i.e. the arrows are shown."""
        return self._overflowing

    def scroll_value(self) -> int:
        return self._area.horizontalScrollBar().value()

    def scroll_maximum(self) -> int:
        return self._area.horizontalScrollBar().maximum()

    def scroll_by(self, pixels: int) -> None:
        bar = self._area.horizontalScrollBar()
        bar.setValue(bar.value() + pixels)

    def ensure_visible(self, widget: QWidget) -> None:
        """Scroll just far enough to show *widget* (a child of the row) completely."""
        if self._overflowing and self.content.isAncestorOf(widget):
            self._area.ensureWidgetVisible(widget, 12, 0)

    def left_arrow(self) -> QToolButton:
        return self._left

    def right_arrow(self) -> QToolButton:
        return self._right

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style = transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
        for button, name in ((self._left, "chevron-left"), (self._right, "chevron-right")):
            button.setStyleSheet(style)
            button.setIcon(load_tabler_icon(name, color=theme.text_primary, size=20, stroke_width=2.6))

    # -- sizes -----------------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.content.sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        # Two arrows and a sliver of the row; the height is the row's (the policy is fixed up and down).
        return QSize(2 * ARROW_WIDTH + _MIN_VIEW_WIDTH, self.content.sizeHint().height())

    def resizeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._update_overflow()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if watched is self.content and event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.updateGeometry()  # the row's height may have changed
            self._update_overflow()
        return super().eventFilter(watched, event)

    # -- internals -------------------------------------------------------------------------

    def _make_arrow(self, name: str, direction: int) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedWidth(ARROW_WIDTH)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        button.setIconSize(QSize(12, 12))
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(_REPEAT_DELAY_MS)
        button.setAutoRepeatInterval(_REPEAT_INTERVAL_MS)
        button.setToolTip("Scroll the ribbon to the left." if direction < 0 else "Scroll the ribbon to the right.")
        button.clicked.connect(lambda _checked=False, sign=direction: self.scroll_by(sign * _STEP_PX))
        button.setVisible(False)
        return button

    def _update_overflow(self) -> None:
        """The row needs `minimumSizeHint().width()`; when that is more than the ribbon's whole width, show the arrows.
        Decided on the whole width (not the viewport's, which the arrows themselves shrink), so showing them cannot
        flip the decision back and forth."""
        need = self.content.minimumSizeHint().width()
        overflowing = need > self.width()
        if overflowing != self._overflowing:
            self._overflowing = overflowing
            self._left.setVisible(overflowing)
            self._right.setVisible(overflowing)
        self._refresh_arrows()

    def _refresh_arrows(self) -> None:
        bar = self._area.horizontalScrollBar()
        self._left.setEnabled(bar.value() > bar.minimum())
        self._right.setEnabled(bar.value() < bar.maximum())
