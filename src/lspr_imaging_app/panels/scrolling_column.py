"""``ScrollingColumn`` - a column of controls that scrolls up and down when its panel is shorter than the column (2026-11-15).

The vertical twin of `ScrollingRibbon` (`scrolling_ribbon.py`), made for the selection toolbar beside the image. A column of buttons is as
tall as the sum of its buttons, and Qt does not let a panel be shorter than its tallest child: with the 13 buttons of the toolbar (measured
2026-11-15: 352 px) the Image panel could not go below 573 px, against 289 px without the column. This wrapper reports a *small* minimum
height instead and, when the column does not fit, shows a chevron at the top and at the bottom that scrolls it. Nothing is clipped without a
way to reach it, nothing is re-laid-out, and there is no scroll bar (the column never gets wider).

* **Arrows** show only while the column overflows. A click moves a step, holding the button repeats, and an arrow is dimmed at its end. The
  mouse wheel over an empty part of the column scrolls too (a button that uses the wheel itself, the wand's and "Select by value"'s method
  switch, keeps it).
* **Width** is the column's own (`sizeHint`); the owner fixes it.
* **`ensure_visible(widget)`** brings a button into view.

The column keeps its own minimum height (`content.minimumSizeHint()`): that is the scrollable height.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

ARROW_HEIGHT = 14
_MIN_VIEW_HEIGHT = 24  # the least of the column that stays visible between the two arrows
_STEP_PX = 56
_REPEAT_DELAY_MS = 300
_REPEAT_INTERVAL_MS = 45


class _Area(QScrollArea):
    """The scroll area itself: no frame, no bars, transparent, and a wheel that moves the column up and down."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)  # the column still stretches over spare room; it never gets shorter than its minimum
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, _MIN_VIEW_HEIGHT)

    def sizeHint(self) -> QSize:
        widget = self.widget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            event.ignore()
            return
        units = event.angleDelta().y()
        if units == 0:
            event.ignore()
            return
        bar.setValue(bar.value() - int(units))  # one wheel notch is 120 units: about 120 px of travel
        event.accept()


class ScrollingColumn(QWidget):
    """*content* (any widget; it becomes the scroll area's widget) with an arrow at each end while it overflows."""

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = content
        self._area = _Area(self)
        self._area.setWidget(content)
        content.setAutoFillBackground(False)  # QScrollArea.setWidget switches it on; the column is drawn on the panel's own background
        self._up = self._make_arrow("chevron-up", -1)
        self._down = self._make_arrow("chevron-down", +1)
        self._overflowing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._up)
        layout.addWidget(self._area, 1)
        layout.addWidget(self._down)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        bar = self._area.verticalScrollBar()
        bar.valueChanged.connect(self._refresh_arrows)
        bar.rangeChanged.connect(lambda _lo, _hi: self._refresh_arrows())
        content.installEventFilter(self)  # a layout change inside the column may change the height it needs
        self.refresh_theme()
        self._update_overflow()

    # -- public ----------------------------------------------------------------------------

    def is_overflowing(self) -> bool:
        """True while the column is taller than the room it has, i.e. the arrows are shown."""
        return self._overflowing

    def scroll_value(self) -> int:
        return self._area.verticalScrollBar().value()

    def scroll_maximum(self) -> int:
        return self._area.verticalScrollBar().maximum()

    def scroll_by(self, pixels: int) -> None:
        bar = self._area.verticalScrollBar()
        bar.setValue(bar.value() + pixels)

    def ensure_visible(self, widget: QWidget) -> None:
        """Scroll just far enough to show *widget* (a child of the column) completely."""
        if self._overflowing and self.content.isAncestorOf(widget):
            self._area.ensureWidgetVisible(widget, 0, 8)

    def up_arrow(self) -> QToolButton:
        return self._up

    def down_arrow(self) -> QToolButton:
        return self._down

    def area(self) -> QScrollArea:
        """The scroll area (tests send it wheel events)."""
        return self._area

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style = transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
        for button, name in ((self._up, "chevron-up"), (self._down, "chevron-down")):
            button.setStyleSheet(style)
            button.setIcon(load_tabler_icon(name, color=theme.text_primary, size=20, stroke_width=2.6))

    # -- sizes -----------------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        return self.content.sizeHint()

    def minimumSizeHint(self) -> QSize:
        # Two arrows and a sliver of the column; the width is the column's own.
        return QSize(self.content.sizeHint().width(), 2 * ARROW_HEIGHT + _MIN_VIEW_HEIGHT)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_overflow()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # (`getattr`: while Qt tears the widget down the Python attributes may already be gone; the same message appears in `ScrollingRibbon`)
        if watched is getattr(self, "content", None) and event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.updateGeometry()
            self._update_overflow()
        return super().eventFilter(watched, event)

    # -- internals -------------------------------------------------------------------------

    def _make_arrow(self, name: str, direction: int) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedHeight(ARROW_HEIGHT)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setIconSize(QSize(12, 12))
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(_REPEAT_DELAY_MS)
        button.setAutoRepeatInterval(_REPEAT_INTERVAL_MS)
        button.setToolTip("Scroll the toolbar up." if direction < 0 else "Scroll the toolbar down.")
        button.clicked.connect(lambda _checked=False, sign=direction: self.scroll_by(sign * _STEP_PX))
        button.setVisible(False)
        return button

    def _update_overflow(self) -> None:
        """The column needs `minimumSizeHint().height()`; when that is more than the column's whole height, show the arrows. Decided
        on the whole height (not the viewport's, which the arrows themselves shrink), so showing them cannot flip the decision back
        and forth."""
        need = self.content.minimumSizeHint().height()
        overflowing = need > self.height()
        if overflowing != self._overflowing:
            self._overflowing = overflowing
            self._up.setVisible(overflowing)
            self._down.setVisible(overflowing)
        self._refresh_arrows()

    def _refresh_arrows(self) -> None:
        bar = self._area.verticalScrollBar()
        self._up.setEnabled(bar.value() > bar.minimum())
        self._down.setEnabled(bar.value() < bar.maximum())
