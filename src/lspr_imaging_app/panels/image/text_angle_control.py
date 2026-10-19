"""The group-label text direction as one picture and one button: the word "label" shown at the current angle,
and next to it a rotate arrow that turns the text a further 45 degrees (counter-clockwise, so it reads upwards
first) on every press, round and back to horizontal after eight.

Used by the Groups label menu (`group_label_menu.py`). Display only: it holds the angle and reports it.
``set_angle(.., emit=False)`` shows a value without reporting it (a copy following the original, a restored
value). The angle is a plain number of degrees, 0 to 315 in steps of `ANGLE_STEP_DEG`.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QTransform
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ..ui_state import UiStateStore
from .group_label_icons import SIZE as _PICTURE_SIZE
from .group_label_icons import orientation_pixmap
from .group_label_overlay import ANGLE_STEP_DEG, LEGACY_DIRECTION_ANGLES

from .general_group import BUTTON_SIZE as _BUTTON_SIZE  # the ribbon's one icon-button size
_ICON_RENDER = 44


def _is_angle(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value % ANGLE_STEP_DEG == 0


class TextAngleControl(QWidget):
    angle_changed = pyqtSignal(int)

    def __init__(self, angle: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not _is_angle(angle):
            raise ValueError(f"angle must be a multiple of {ANGLE_STEP_DEG} degrees, got {angle!r}")
        self._angle = angle % 360
        self._picture = QLabel(self)  # the word "label" at the current angle (not a button: the arrow turns it)
        self._picture.setFixedSize(_PICTURE_SIZE + 2, _PICTURE_SIZE + 2)
        self._picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._picture.setToolTip("Text direction of the group labels. Turn it with the arrow.")
        self._rotate = QToolButton(self)
        self._rotate.setAutoRaise(True)
        self._rotate.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._rotate.setIconSize(QSize(_BUTTON_SIZE - 6, _BUTTON_SIZE - 6))
        self._rotate.setToolTip(f"Turn the group-label text {ANGLE_STEP_DEG} degrees counter-clockwise (every press).")
        self._rotate.clicked.connect(lambda _checked=False: self.set_angle(self._angle + ANGLE_STEP_DEG))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._picture)
        layout.addWidget(self._rotate)
        self.refresh_theme(get_active_theme())

    def angle(self) -> int:
        return self._angle

    def rotate_button(self) -> QToolButton:
        """The arrow (``.click()`` turns the text, as a mouse click would)."""
        return self._rotate

    def picture(self) -> QLabel:
        return self._picture

    def set_angle(self, angle: int, *, emit: bool = True) -> None:
        """Turn the text to ``angle`` degrees (any multiple of 45; 360 is 0). ``emit=False`` shows it without reporting."""
        if not _is_angle(angle):
            raise ValueError(f"angle must be a multiple of {ANGLE_STEP_DEG} degrees, got {angle!r}")
        angle %= 360
        if angle == self._angle:
            return
        self._angle = angle
        self._refresh_picture(get_active_theme())
        if emit:
            self.angle_changed.emit(angle)

    def sync_from(self, other: TextAngleControl) -> None:
        """Show another control's angle without reporting it (the same menu sits on two ribbon tabs)."""
        self.set_angle(other.angle(), emit=False)

    def bind_ui_state(self, store: UiStateStore, key: str, legacy_key: str) -> None:
        """Put the angle back as last left, then save each change. A file from before the angle (``legacy_key``
        held "horizontal" / "vertical" / "diagonal") is still read; a value that is not an angle is ignored."""
        saved = store.get(key)
        legacy = store.get(legacy_key)
        if _is_angle(saved):
            self.set_angle(saved)
        elif isinstance(legacy, str) and legacy in LEGACY_DIRECTION_ANGLES:
            self.set_angle(LEGACY_DIRECTION_ANGLES[legacy])
        self.angle_changed.connect(lambda angle: store.set(key, angle))

    def _refresh_picture(self, theme: GuiTheme) -> None:
        self._picture.setPixmap(orientation_pixmap(self._angle, theme.accent_blue))

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._rotate.setStyleSheet(transparent_icon_button_stylesheet(hover=theme.control_bg_hover))
        # The vendored arrow turns clockwise; the text turns the other way, so the arrow is mirrored to match.
        arrow = load_tabler_icon("rotate-clockwise-2", color=theme.text_primary, size=_ICON_RENDER, stroke_width=2.1)
        self._rotate.setIcon(QIcon(arrow.pixmap(_ICON_RENDER, _ICON_RENDER).transformed(QTransform().scale(-1.0, 1.0))))
        self._refresh_picture(theme)
