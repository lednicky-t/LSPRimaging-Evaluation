from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QStyleOptionProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    collapsible_pin_stylesheet,
    collapsible_toggle_stylesheet,
    get_active_theme,
    hex_to_rgba,
    tabler_icon_svg,
    transparent_icon_button_stylesheet,
)
from lspr_imaging_app.gui.main_window_icons import MainWindowIcons


class ResponsiveDoubleSpinBox(QDoubleSpinBox):
    def stepBy(self, steps: int) -> None:  # type: ignore[override]
        step = float(self.singleStep()) if float(self.singleStep()) > 0 else 1.0
        target = float(self.value()) + float(steps) * step
        minimum = float(self.minimum())
        maximum = float(self.maximum())
        self.setValue(float(np.clip(target, minimum, maximum)))


class CollapsibleSection(QWidget):
    expanded_changed = pyqtSignal(bool)
    pin_changed = pyqtSignal(bool)
    apply_changed = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = True,
        applied: bool | None = None,
        apply_tooltip: str | None = None,
        help_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._toggle = QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setAutoRaise(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.NoArrow)
        self._toggle.setIcon(PanelContainer._make_chevron_icon(self, expanded))
        self._toggle.setIconSize(QSize(12, 12))
        self._toggle.setStyleSheet(collapsible_toggle_stylesheet())
        self._toggle.toggled.connect(self._set_expanded)

        self._pin_button = QToolButton(self)
        self._pin_button.setText("")
        self._pin_button.setCheckable(True)
        self._pin_button.setChecked(False)
        self._pin_button.setAutoRaise(True)
        self._pin_button.setIcon(PanelContainer._make_pin_icon(self, False))
        self._pin_button.setIconSize(QSize(16, 16))
        self._pin_button.setFixedSize(22, 22)
        self._pin_button.setToolTip("Pin this panel open so it is not auto-collapsed by the accordion.")
        self._pin_button.setStyleSheet(collapsible_pin_stylesheet())
        self._pin_button.toggled.connect(self.pin_changed.emit)
        self._pin_button.toggled.connect(self._update_pin_icon)

        self._apply_button: QToolButton | None = None
        if applied is not None:
            self._apply_button = QToolButton(self)
            self._apply_button.setText("")
            self._apply_button.setCheckable(True)
            self._apply_button.setChecked(bool(applied))
            self._apply_button.setAutoRaise(True)
            self._apply_button.setIcon(self._make_apply_icon(bool(applied)))
            self._apply_button.setIconSize(QSize(18, 18))
            self._apply_button.setFixedSize(22, 22)
            self._apply_button.setToolTip(apply_tooltip or "Toggle whether this panel's calculations are applied.")
            self._apply_button.setStyleSheet(transparent_icon_button_stylesheet())
            self._apply_button.toggled.connect(self._set_applied)

        self._help_button = QToolButton(self)
        self._help_button.setText("")
        self._help_button.setAutoRaise(True)
        self._help_button.setCheckable(False)
        self._help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._help_button.setIcon(MainWindowIcons._make_help_icon())
        self._help_button.setIconSize(QSize(16, 16))
        self._help_button.setFixedSize(22, 22)
        self._help_button.setToolTip("Show panel shortcuts and help.")
        self._help_button.setStyleSheet(transparent_icon_button_stylesheet())
        help_message = help_text or "No additional panel help is available yet."
        self._help_button.clicked.connect(lambda *_: QMessageBox.information(self, self._toggle.text(), help_message))

        self._content = content
        self._content.setParent(self)
        self._content.setVisible(expanded)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(self._toggle, 1)
        right_controls = QWidget(header)
        right_layout = QHBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(self._help_button, 0, Qt.AlignmentFlag.AlignVCenter)
        if self._apply_button is not None:
            right_layout.addWidget(self._apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self._pin_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(right_controls, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(header)
        layout.addWidget(self._content)

    def _set_expanded(self, expanded: bool) -> None:
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        self._content.setVisible(expanded)
        self.expanded_changed.emit(expanded)

    def _update_pin_icon(self, pinned: bool) -> None:
        self._pin_button.setIcon(self._make_pin_icon(pinned))

    def _set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setIcon(self._make_apply_icon(applied))
        self.apply_changed.emit(applied)

    def _make_chevron_icon(self, expanded: bool) -> QIcon:
        return PanelContainer._make_chevron_icon(self, expanded)

    def _make_pin_icon(self, pinned: bool) -> QIcon:
        return PanelContainer._make_pin_icon(self, pinned)

    def _make_apply_icon(self, applied: bool) -> QIcon:
        return PanelContainer._make_apply_icon(self, applied)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(bool(expanded))

    def is_pinned(self) -> bool:
        return self._pin_button.isChecked()

    def set_pinned(self, pinned: bool) -> None:
        self._pin_button.setChecked(bool(pinned))

    def has_apply_toggle(self) -> bool:
        return self._apply_button is not None

    def is_applied(self) -> bool:
        return True if self._apply_button is None else self._apply_button.isChecked()

    def set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setChecked(bool(applied))

    def set_apply_enabled(self, enabled: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setEnabled(bool(enabled))


class PanelContainer(QDockWidget):
    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._title = title
        self._content = content
        self.setObjectName(f"{title.replace(' ', '')}Panel")
        self.setWidget(content)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # While floating, docking is disabled by default (see _on_top_level_changed) so
        # dragging the floating window around/over the main app doesn't trigger Qt's
        # dock-target hint overlay or risk an accidental snap-back. The float button
        # re-arms docking on click, tracked here so it can also gate a second click.
        self._dock_armed = False

        theme = get_active_theme()
        self.setStyleSheet(f"QDockWidget {{ color: {theme.text_primary}; }}")
        # QDockWidget's built-in close/float buttons hard-code their icon's pixel
        # size in Qt's C++ paint code; neither the stylesheet's width/height nor
        # a QSS "icon-size"/"image" override nor setIconSize() on the internal
        # qt_dockwidget_*button widgets has any effect on it. A custom title bar
        # with real QToolButtons is the only way to make the glyphs bigger.
        self.setTitleBarWidget(self._build_title_bar(title, theme))
        self.topLevelChanged.connect(self._on_top_level_changed)

    def _build_title_bar(self, title: str, theme) -> QWidget:
        bar = QWidget(self)
        bar.setStyleSheet(f"background: {theme.window_bg};")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QWidget(bar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(2)
        outer.addWidget(row)

        label = QLabel(title, row)
        label.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600; background: transparent;")
        layout.addWidget(label)
        layout.addStretch(1)

        float_button = QToolButton(row)
        float_button.setIcon(self._make_float_icon())
        float_button.setIconSize(QSize(18, 18))
        float_button.setFixedSize(24, 24)
        float_button.setAutoRaise(True)
        float_button.setToolTip("Undock this panel into a floating window.")
        float_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_blue, 0.22))
            + "QToolButton:hover { border-radius: 4px; }"
        )
        float_button.clicked.connect(self._on_float_button_clicked)
        layout.addWidget(float_button)
        self._float_button = float_button

        close_button = QToolButton(row)
        close_button.setIcon(self._make_close_icon())
        close_button.setIconSize(QSize(18, 18))
        close_button.setFixedSize(24, 24)
        close_button.setAutoRaise(True)
        close_button.setToolTip("Close this panel.")
        close_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_red, 0.22))
            + "QToolButton:hover { border-radius: 4px; }"
        )
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        # A plain widget (not a border-bottom in the stylesheet) - Qt mis-renders a QSS
        # border-bottom set on a QDockWidget custom title bar as a short underline hugging
        # the label text instead of a full-width rule, so the separator is drawn explicitly.
        separator = QWidget(bar)
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {theme.toolbar_border};")
        outer.addWidget(separator)

        return bar

    def _on_float_button_clicked(self) -> None:
        if not self.isFloating():
            self.setFloating(True)
            return
        # Already floating: the click toggles whether docking is armed rather than
        # changing floating state again (see _on_top_level_changed for why docking
        # starts disabled here).
        self._dock_armed = not self._dock_armed
        self.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
            if self._dock_armed
            else Qt.DockWidgetArea.NoDockWidgetArea
        )
        self._update_float_button_appearance()

    def _on_top_level_changed(self, floating: bool) -> None:
        # Disable docking the moment the panel becomes floating - regardless of
        # whether that happened via the float button or the user dragging the title
        # bar out - so moving the floating window around/over the main app doesn't
        # trigger Qt's dock-target hint overlay or accidentally snap it back in.
        # Re-docking (floating -> docked, by any means) always restores all areas.
        self._dock_armed = False
        self.setAllowedAreas(
            Qt.DockWidgetArea.NoDockWidgetArea if floating else Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self._update_float_button_appearance()

    def _update_float_button_appearance(self) -> None:
        if not self.isFloating():
            self._float_button.setIcon(self._make_float_icon())
            self._float_button.setToolTip("Undock this panel into a floating window.")
        elif self._dock_armed:
            self._float_button.setIcon(self._make_float_icon("#22c55e"))
            self._float_button.setToolTip(
                "Docking enabled - drag this window onto a dock area to dock it, "
                "or click again to disable docking."
            )
        else:
            self._float_button.setIcon(self._make_float_icon())
            self._float_button.setToolTip(
                "Docking disabled while floating, so this window won't snap back when "
                "moved over the main app. Click to re-enable docking."
            )

    @staticmethod
    def _make_close_icon(color: str = "#cbd5e1") -> QIcon:
        svg = tabler_icon_svg("x", color=color, stroke_width=2.2)
        if svg:
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            if renderer.isValid():
                pixmap = QPixmap(20, 20)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter, QRectF(1.0, 1.0, 18.0, 18.0))
                painter.end()
                return QIcon(pixmap)
        return QIcon()

    @staticmethod
    def _make_float_icon(color: str = "#cbd5e1") -> QIcon:
        svg = tabler_icon_svg("external-link", color=color, stroke_width=2.2)
        if svg:
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            if renderer.isValid():
                pixmap = QPixmap(20, 20)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter, QRectF(1.0, 1.0, 18.0, 18.0))
                painter.end()
                return QIcon(pixmap)
        return QIcon()

    def _make_chevron_icon(self, expanded: bool) -> QIcon:
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cbd5e1"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        if expanded:
            path.moveTo(3.5, 5.0)
            path.lineTo(7.0, 8.5)
            path.lineTo(10.5, 5.0)
        else:
            path.moveTo(5.0, 3.5)
            path.lineTo(8.5, 7.0)
            path.lineTo(5.0, 10.5)
        painter.drawPath(path)
        painter.end()
        return QIcon(pixmap)

    def _make_pin_icon(self, pinned: bool) -> QIcon:
        color = "#22c55e" if pinned else "#f8fafc"
        icon = MainWindowIcons._tabler_icon("pin-filled" if pinned else "pin", color, 24, stroke_width=2.2)
        if not icon.isNull():
            return icon

        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(color) if pinned else QColor(0, 0, 0, 0))
        painter.drawRoundedRect(QRectF(4.2, 1.8, 7.0, 3.4), 1.7, 1.7)
        painter.drawLine(QPointF(7.7, 5.2), QPointF(7.7, 10.2))
        painter.drawLine(QPointF(5.0, 6.0), QPointF(10.4, 6.0))
        painter.drawLine(QPointF(7.7, 10.2), QPointF(6.0, 13.2))
        painter.end()
        return QIcon(pixmap)

    def _make_apply_icon(self, applied: bool) -> QIcon:
        icon_name = "link" if applied else "link-off"
        stroke = "#22c55e" if applied else "#ef4444"
        svg = tabler_icon_svg(icon_name, color=stroke, stroke_width=2.2)
        if svg:
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            if renderer.isValid():
                pixmap = QPixmap(20, 20)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter, QRectF(0.0, 1.0, 20.0, 18.0))
                painter.end()
                return QIcon(pixmap)

        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(stroke), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(QPainterPath())
        painter.drawEllipse(QRectF(3.0, 3.0, 7.5, 7.5))
        painter.drawEllipse(QRectF(9.5, 9.5, 7.5, 7.5))
        painter.drawLine(QPointF(8.2, 8.2), QPointF(11.8, 11.8))
        if not applied:
            painter.drawLine(QPointF(4.0, 15.0), QPointF(16.0, 5.0))
        painter.end()
        return QIcon(pixmap)


class CompactWedgeSlider(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self.setMinimumSize(24, 12)
        self.setMaximumHeight(12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Opacity")

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), self._minimum + 1)
        self.setValue(self._value)

    def setValue(self, value: int) -> None:
        clamped = int(np.clip(int(value), self._minimum, self._maximum))
        if clamped == self._value:
            self.update()
            return
        self._value = clamped
        self.valueChanged.emit(self._value)
        self.update()

    def value(self) -> int:
        return int(self._value)

    def sizeHint(self) -> QSize:
        return QSize(24, 12)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        base_path = QPainterPath()
        base_path.moveTo(rect.left(), rect.bottom())
        base_path.lineTo(rect.right(), rect.top())
        base_path.lineTo(rect.right(), rect.bottom())
        base_path.closeSubpath()
        painter.setPen(QPen(QColor("#475569"), 1.0))
        painter.setBrush(QColor("#0f172a"))
        painter.drawPath(base_path)

        fraction = 0.0 if self._maximum <= self._minimum else (self._value - self._minimum) / float(self._maximum - self._minimum)
        fraction = float(np.clip(fraction, 0.0, 1.0))
        fill_right = rect.left() + rect.width() * fraction
        if fill_right > rect.left() + 0.5:
            fill_path = QPainterPath()
            fill_path.moveTo(rect.left(), rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom() - rect.height() * fraction)
            fill_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#38bdf8"))
            painter.drawPath(fill_path)

        handle_x = rect.left() + rect.width() * fraction
        painter.setPen(QPen(QColor("#f8fafc"), 1.4))
        painter.drawLine(QPointF(handle_x, rect.bottom()), QPointF(handle_x, rect.bottom() - rect.height() * max(fraction, 0.18)))
        painter.end()

    def _set_value_from_pos(self, position) -> None:
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if rect.width() <= 1.0:
            return
        fraction = (float(position.x()) - rect.left()) / rect.width()
        fraction = float(np.clip(fraction, 0.0, 1.0))
        value = int(round(self._minimum + fraction * (self._maximum - self._minimum)))
        self.setValue(value)


class BusySpinner(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._text_visible = False
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(22, 22)
        self.hide()

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        if self._maximum <= self._minimum:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        self._value = int(value)
        self.update()

    def setTextVisible(self, visible: bool) -> None:
        self._text_visible = bool(visible)
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % 12
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        cx = rect.center().x()
        cy = rect.center().y()
        radius = min(rect.width(), rect.height()) * 0.32
        segment_count = 12
        if self._maximum <= self._minimum:
            base_color = QColor("#38bdf8")
            for index in range(segment_count):
                progress = (index - self._phase) % segment_count
                alpha = int(40 + (215 * (segment_count - progress - 1) / max(segment_count - 1, 1)))
                color = QColor(base_color)
                color.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                angle = (360.0 / segment_count) * index
                radians = np.deg2rad(angle)
                x = cx + np.cos(radians) * radius
                y = cy + np.sin(radians) * radius
                painter.drawEllipse(QRectF(x - 1.8, y - 1.8, 3.6, 3.6))
        else:
            span = max(self._maximum - self._minimum, 1)
            fraction = float(np.clip((self._value - self._minimum) / span, 0.0, 1.0))
            painter.setPen(QPen(QColor("#334155"), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("#38bdf8"), 2.4))
            start_angle = 90 * 16
            span_angle = int(-360.0 * fraction * 16)
            painter.drawArc(rect, start_angle, span_angle)
        painter.end()


class ShineProgressBar(QProgressBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shine_phase = 0.0
        self._shine_timer = QTimer(self)
        self._shine_timer.setInterval(30)
        self._shine_timer.timeout.connect(self._advance_shine)

    def setRange(self, minimum: int, maximum: int) -> None:  # type: ignore[override]
        super().setRange(minimum, maximum)
        self._sync_shine_timer()

    def setValue(self, value: int) -> None:  # type: ignore[override]
        super().setValue(value)
        self._sync_shine_timer()

    def _sync_shine_timer(self) -> None:
        if self.maximum() > self.minimum() and self.value() > self.minimum() and self.value() < self.maximum() and self.isVisible():
            if not self._shine_timer.isActive():
                self._shine_timer.start()
        else:
            self._shine_timer.stop()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_shine_timer()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._shine_timer.stop()

    def _advance_shine(self) -> None:
        self._shine_phase = (self._shine_phase + 0.028) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = min(rect.height(), 9.0) / 2.0
        track_color = QColor("#1f2937")
        track_border = QColor("#475569")
        fill_color = QColor("#38bdf8")
        text_color = QColor("#f8fafc")
        shine_color = QColor(255, 255, 255, 120)

        painter.setPen(QPen(track_border, 1.0))
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, radius, radius)

        span = max(option.maximum - option.minimum, 1)
        fraction = float(np.clip((option.progress - option.minimum) / span, 0.0, 1.0))
        fill_width = rect.width() * fraction
        if fill_width > 0.0:
            fill_rect = QRectF(rect.left(), rect.top(), fill_width, rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(fill_rect, radius, radius)

            sheen_width = max(rect.width() * 0.28, 18.0)
            sheen_x = fill_rect.left() - sheen_width + (fill_rect.width() + sheen_width * 2.0) * self._shine_phase
            sheen_rect = QRectF(sheen_x, fill_rect.top(), sheen_width, fill_rect.height())
            painter.save()
            painter.setClipRect(fill_rect)
            gradient = QLinearGradient(sheen_rect.left(), sheen_rect.top(), sheen_rect.right(), sheen_rect.top())
            gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
            gradient.setColorAt(0.45, shine_color)
            gradient.setColorAt(0.55, shine_color)
            gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(sheen_rect, radius, radius)
            painter.restore()

        if option.textVisible:
            painter.setPen(QPen(text_color))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, option.text)

        painter.end()
