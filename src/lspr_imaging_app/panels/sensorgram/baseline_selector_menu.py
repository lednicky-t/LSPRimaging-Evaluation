"""Sensorgram ribbon, "View" group: the "Baseline selector" pick-up menu (2026-10-25).

Screen purpose: show or hide the draggable band that marks where the baseline is taken, and choose its colour and transparency.

One icon button (a plot frame with a short band along its bottom edge) that opens a small panel in the look of the Image ribbon's "ROI display" menu
(`image/roi_display_menu.py`): a captioned grid whose row holds the same three-part control as the ROI rows, a toggle, a colour swatch and a transparency
wedge (`image/roi_overlay_controls.py`, reused through its `OverlaySpec`). The panel is a `QWidgetAction` inside a `QMenu`: it opens on click and closes when
you click outside it.

Display only: the Sensorgram panel owns the values, remembers them and hands them to the plot. Showing or hiding the band changes no number; the baseline
that "Subtract" (or a double-click on the y axis label) applies is read from the band's position whether it is drawn or not.
"""

from __future__ import annotations

from PyQt6.QtCore import QLocale, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDoubleValidator, QFont, QFontInfo, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import GuiTheme, get_active_theme

from ..image.general_group import style_general_icon_button
from ..image.roi_overlay_controls import OverlaySpec, RoiOverlayControls
from ..ribbon_fields import style_ribbon_number_field
from ..ribbon_icon_size import ICON_SIZE

DEFAULT_COLOR = "#78a0ff"  # the band's colour before the user picks one (the blue it always had)
DEFAULT_ALPHA = 0.28  # its fill opacity before the user moves the wedge (0.16 was too faint on a projector)

_RENDER_SIZE = ICON_SIZE * 2  # drawn at twice the size and scaled down, as the other ribbon icons are
_STROKE_WIDTH = 2.1


def band_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A plot frame with a filled band (and its two edges) along the bottom: in ``on_color``; in ``off_color`` and crossed out when off."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(on_color if on else off_color)
    size = float(_RENDER_SIZE)
    frame = QColor(color)
    frame.setAlpha(150)
    pen = QPen(frame, _STROKE_WIDTH)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    margin = size * 0.12
    painter.drawRoundedRect(QRectF(margin, margin, size - 2 * margin, size - 2 * margin), size * 0.08, size * 0.08)
    left, right = size * 0.30, size * 0.70
    top, bottom = size * 0.56, size - margin
    fill = QColor(color)
    fill.setAlpha(110)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRect(QRectF(left, top, right - left, bottom - top))
    painter.setPen(QPen(color, _STROKE_WIDTH))
    painter.drawLine(QPointF(left, top), QPointF(left, bottom))
    painter.drawLine(QPointF(right, top), QPointF(right, bottom))
    if not on:
        slash = QPen(color, _STROKE_WIDTH * 1.4)
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(slash)
        edge = size * 0.12
        painter.drawLine(QPointF(size - edge, edge), QPointF(edge, size - edge))
    painter.end()
    return QIcon(pixmap)


SPEC: OverlaySpec = (
    band_icon, DEFAULT_COLOR,
    "Show or hide the baseline selector, the band along the bottom of the plot. Drag it, or drag its edges, to choose where the baseline is taken.",
    "Colour of the baseline selector.",
    "Baseline selector transparency (the fill; the edges keep their colour).",
    "Choose the baseline selector colour",
)


HEIGHT_RANGE = (2, 100)  # percent of the plot height
DEFAULT_HEIGHT = 10


def make_height_box(parent: QWidget) -> QSpinBox:
    box = QSpinBox(parent)
    box.setRange(*HEIGHT_RANGE)
    box.setValue(DEFAULT_HEIGHT)
    box.setSuffix(" %")
    box.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
    box.setToolTip("Height of the baseline selector, in percent of the plot height.")
    style_ribbon_number_field(box, f"{HEIGHT_RANGE[1]} %")
    return box


FONT_RANGE = (6, 24)  # points


def default_font_points() -> int:
    """The application font's size in whole points: what the range readout shows until a size is chosen (the look it always had)."""
    return min(max(round(QFontInfo(QApplication.font()).pointSizeF()), FONT_RANGE[0]), FONT_RANGE[1])


def make_font_box(parent: QWidget) -> QSpinBox:
    box = QSpinBox(parent)
    box.setRange(*FONT_RANGE)
    box.setValue(default_font_points())
    box.setSuffix(" pt")
    box.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
    box.setToolTip("Text size of the baseline range (\"Baseline [start, end]\") shown on the plot, in points.")
    style_ribbon_number_field(box, f"{FONT_RANGE[1]} pt")
    return box


class BaselineRangeReadout(QWidget):
    """The label "Baseline" with "[start, end]" under it, floating on the plot just above the baseline selector (the plot places it); editable, in the look of the Histogram's highlight range (`histogram/highlight_range_controls.py`): two
    frameless, centred fields of one fixed width (sized once for the worst case) between plain bracket labels. The unit is the plot's x axis (cube
    number, or seconds); the panel says how many decimals to show. ``range_edited(low, high)`` after a committed edit (Enter or leaving the field),
    sorted; ``set_range`` never emits."""

    range_edited = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("baselineRangeReadout")
        self.setAutoFillBackground(True)
        validator = QDoubleValidator(-1.0e9, 1.0e9, 3, self)
        validator.setLocale(QLocale(QLocale.Language.C))  # a decimal point, whatever the system language
        self._points = default_font_points()
        self._low, self._high = (self._field(self._field_width(QFontMetrics(self.font())), validator) for _ in range(2))
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 1, 4, 1)
        column.setSpacing(0)
        self._heading = QLabel("Baseline", self)
        self._heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._heading.setContentsMargins(0, 0, 0, 0)
        self._heading.setFixedHeight(QFontMetrics(self.font()).height())  # as tall as its text, no more
        column.addWidget(self._heading)
        row = QHBoxLayout()
        row.setSpacing(0)
        for piece in (QLabel("[", self), self._low, QLabel(",", self), self._high, QLabel("]", self)):
            row.addWidget(piece)
        column.addLayout(row)
        self.adjustSize()
        self.refresh_theme(get_active_theme())

    @staticmethod
    def _field_width(metrics: QFontMetrics) -> int:
        return metrics.boundingRect("9" * 7).width() + 6  # 99999.9 s, plus the same explicit margin the histogram fields have

    def set_font_size(self, points: int) -> None:
        """Text size of the whole box, in points. The heading and the fields are fixed-size from the font's metrics, so they are measured again here."""
        self._points = int(points)
        font = QFont(self.font())
        font.setPointSizeF(float(points))
        self.setFont(font)
        metrics = QFontMetrics(font)
        self._heading.setFixedHeight(metrics.height())
        for field in (self._low, self._high):
            field.setFixedWidth(self._field_width(metrics))
            field.setFixedHeight(metrics.height() + 2)
        self.refresh_theme(get_active_theme())  # the style sheet carries the size too, so an application-wide rule cannot override it
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def _field(self, width: int, validator: QDoubleValidator) -> QLineEdit:
        field = QLineEdit(self)
        field.setValidator(validator)
        field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        field.setFrame(False)
        field.setFixedWidth(width)
        field.setFixedHeight(QFontMetrics(self.font()).height() + 2)  # a frameless QLineEdit is still taller than its text
        field.editingFinished.connect(self._on_edited)
        return field

    def set_range(self, low: float, high: float, decimals: int) -> None:
        for field, value in ((self._low, low), (self._high, high)):
            field.blockSignals(True)
            field.setText(f"{value:.{decimals}f}")
            field.blockSignals(False)

    def _on_edited(self) -> None:
        try:
            low, high = (float(f.text().replace(",", ".")) for f in (self._low, self._high))
        except ValueError:  # a field can always be emptied: nothing to commit
            return
        self.range_edited.emit(min(low, high), max(low, high))

    def refresh_theme(self, theme: GuiTheme) -> None:
        self.setStyleSheet(
            f"#baselineRangeReadout {{ background: {theme.toolbar_section_bg}; border: 1px solid {theme.toolbar_border}; border-radius: 4px; }} "
            f"QLineEdit {{ color: {theme.text_primary}; background: transparent; border: none; padding: 0px; margin: 0px; font-size: {self._points}pt; }} "
            f"QLabel {{ color: {theme.text_primary}; background: transparent; font-size: {self._points}pt; }}"
        )


class BaselineSelectorMenu(QToolButton):
    """The View button: opens the panel that holds the controls it is given (it adds no state and no signals of its own)."""

    def __init__(self, controls: RoiOverlayControls, height_box: QWidget, font_box: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(
            "Baseline selector: show or hide the band that marks where the baseline is taken (it spans only the bottom tenth of the plot), "
            "and choose its colour and transparency."
        )
        panel = QWidget(self)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._caption = QLabel("Baseline selector", panel)
        grid.addWidget(self._caption, 0, 0)
        grid.addWidget(controls, 0, 1)  # reparents the control into the panel
        self._height_caption = QLabel("Height", panel)
        grid.addWidget(self._height_caption, 1, 0)
        grid.addWidget(height_box, 1, 1)
        self._font_caption = QLabel("Font size", panel)
        grid.addWidget(self._font_caption, 2, 0)
        grid.addWidget(font_box, 2, 1)
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.refresh_theme(get_active_theme())

    def refresh_theme(self, _theme: GuiTheme) -> None:
        style_general_icon_button(self)
        self.setIcon(band_icon(True, get_active_theme().text_dim, DEFAULT_COLOR))
