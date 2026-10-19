"""Shared captioned-icon-group building blocks for ribbon-style rows of
icon buttons - a vertical divider line, and a small muted caption centered
under a row of icons.

Factored out of `panels/image/panel.py` (2026-10-02, where this pattern was
first built for the "Mask" tab's State/Visibility/Manual edit/Automatic
edit groups) into this neutral, `panels/`-level home when
`image/transforms_settings.py` needed the identical pattern for its own
new "Calibrate" group (Measure, split out from the rest of the Transforms
row - see that module's docstring) - a plain top-level import from
`panels/image/panel.py` would have been a real circular import
(`transforms_settings.py` is itself imported *by* `panel.py`), not just a
style preference, so these could not simply stay private to `panel.py` and
be reached into from there.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPalette, QPen
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from .ribbon_icon_size import BUTTON_SIZE

_GROUP_LABEL_FONT_SIZE_PX = 9


class RibbonSeparator(QFrame):
    """The thin divider between two captioned groups in the tool ribbon (the tabs under the Image panel's top
    bar). The widget fills the row's height like any layout child, but the line is drawn only as tall as an
    icon button (`BUTTON_SIZE`, the same proportion as the dividers of the slider ribbon below the image) and
    starts `top_offset` px from the top: that is where the icon row of a group is (its caption is under the
    icons), so the line spans the icons and shares their centre. Before 2026-10-18 it ran the full row height,
    caption included, which put its centre about 11 px below the icons'.

    A `QFrame` so a stylesheet `color:` still sets its colour (taken from the palette's foreground, the usual
    trick for line frames), as for `vertical_separator`."""

    def __init__(self, parent: QWidget | None = None, *, top_offset: int = 0) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"color: {get_active_theme().control_border};")  # a theme switch restyles it, as before
        self._top_offset = top_offset

    def line_span(self) -> tuple[int, int]:
        """``(top, bottom)`` of the drawn line in this widget's coordinates."""
        return self._top_offset, self._top_offset + BUTTON_SIZE

    def paintEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        top, bottom = self.line_span()
        painter = QPainter(self)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
        x = self.width() // 2
        painter.drawLine(x, top, x, bottom - 1)
        painter.end()


def ribbon_separator(parent: QWidget | None = None, *, top_offset: int = 0) -> RibbonSeparator:
    """A divider for a row of captioned groups in the tool ribbon: see `RibbonSeparator`. `top_offset`: px
    from the top of the row to the top of the icon row (0 when the groups start at the row's top)."""
    return RibbonSeparator(parent, top_offset=top_offset)


def vertical_separator(parent: QWidget) -> QFrame:
    """A thin vertical divider line between two captioned groups. `QFrame`'s
    line frames draw using the widget's foreground color, which the `color`
    stylesheet property sets - the usual Qt trick for recoloring a frame
    line. Expands to fill the row's height rather than a fixed pixel value,
    since a captioned group is two rows tall (icons plus a caption
    underneath) - `QHBoxLayout` stretches a child with an `Expanding`
    vertical policy to match the tallest sibling in the row for free, so
    this stays correct however tall the captioned groups end up being."""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    line.setStyleSheet(f"color: {get_active_theme().control_border};")
    return line


def group_label_style() -> str:
    """Small, muted caption style - quieter than a navigation label meant to
    be read; this is a caption meant to be noticed only on a second look.
    `text_dim` is this theme's one step darker/more muted than `text_muted`
    (see `lspr_ui`'s `GuiTheme`)."""
    return f"color: {get_active_theme().text_dim}; font-size: {_GROUP_LABEL_FONT_SIZE_PX}px;"


def labeled_icon_group(parent: QWidget, content: QWidget, label_text: str) -> tuple[QWidget, QLabel]:
    """Wraps an icon row with a small, muted caption centered underneath it
    (non-intrusive section labels - a group's own boundary is already
    implied by the tab's edge and/or a `|` divider between groups, so no
    extra bordered box is drawn here). Deliberately plain text below the
    row, not `lspr_ui`'s `toolbarSectionTitle` convention (sLSPR
    Evaluation's own `main_window.py`) - that one sits *above* a single
    control and reads as a form label; this one sits *below* a row of icons
    and reads as a caption, which is why it needs to stay quieter (smaller,
    `text_dim`, no bold) rather than reusing that style verbatim.

    Returns the wrapping group widget and the label itself - callers keep
    the label reference only to restyle it on a live theme switch; nothing
    reads its text back."""
    group = QWidget(parent)
    layout = QVBoxLayout(group)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
    label = QLabel(label_text, group)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    label.setStyleSheet(group_label_style())
    layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
    return group, label
