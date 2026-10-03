"""Image panel's tool ribbon (2026-09-30): a two-row structure above the
canvas - a top row of category tabs and a bottom row that swaps to show
the active category's tools, Office/Inkscape/Photoshop-style (maintainer's
own reference points, 2026-09-30).

Replaces the single-row bar `panel.py` used to embed directly: `panel.py`
now builds one `ImageToolRibbon` with four seeded categories - "Image
tools", "Mask", "Histogram", "ROIs" - and embeds that instead. "Mask" holds
`MaskOverlayControls` (show/hide + color + transparency for the mask-overlay
tint) - it used to share "Image tools" with `TransformsSection`, separated
by a vertical divider, until it got its own tab (2026-10-01, maintainer
request - keep the mask icons out of "Image tools"). `CanvasToolsBar`
(`canvas_tools.py`) is unchanged and is now just the "ROIs" category's
content widget: Select and Add ROI are both ROI actions (Select's own
tooltip is "Left-click an ROI to select it, drag to move it"), so they
landed under "ROIs" rather than the more generic "Image tools" - a labeling
call, easy to revisit, not a behavior change; `panel.py` still reaches the
same `CanvasToolsBar` instance through its own `_canvas_tools` attribute,
so nothing that already drives its buttons directly (tests included) needed
to change.

**"Image tools" holds a second `TransformsSection` instance** (2026-09-30,
maintainer request - "duplicate transform tools and put them in image
tools of image panel"): `panel.py` constructs it wired to the same
`GeometryModule`/`ActiveToolModule` the Workflow panel's own instance
uses, so the two rows stay in sync automatically - see
`transforms_settings.py`'s module docstring for why this needed no new
plumbing, just a second widget instance.

**"Histogram" holds `HistogramHighlightOverlayControls`** (show/hide + color
+ transparency for the histogram intensity-selection tint, 2026-10-02 -
maintainer request, "add the toggle to show/hide histogram selection in
image... similar to mask icons") - added the same way the "Mask" tab's
overlay controls were, as its own single captioned group (`panel.py`'s
`_labeled_icon_group`). It was a seeded placeholder ("we will fill as we go"
- maintainer, 2026-09-30) until this. `_category_placeholder` below remains
for any future category that still has nothing to show - none do today, all
four tabs have real content. Subcategories (a third row, or per-category
sub-tabs within the bottom row) are not built - the maintainer's plan is to
add them once a category actually has enough tools to need grouping within
itself, not to guess at that structure now.

**The bottom row is a fixed height** regardless of which category is
showing (`_ROW_HEIGHT` - the tallest of `CanvasToolsBar`'s,
`TransformsSection`'s, and the "Mask" tab's captioned-icon-groups' own
natural heights), so switching tabs never resizes the Image panel's top bar
- `QStackedWidget` would otherwise report whichever page happens to be the
tallest.

**Tabs are plain checkable `QToolButton`s in a `QButtonGroup`**, not a
`QTabBar`/`QTabWidget` - this ribbon's whole point is a category row wired
to an *independent* fixed-height content row underneath (see above), which
a real `QTabWidget` does not give you (its tab-page area sizes to the
current page, exactly the resize-on-switch problem this avoids). A real,
directly-checkable button matches this app's own GUI-testability rule
(CLAUDE.md: prefer widgets with a real `.click()` over a hand-rolled
click target) and this bar's own sibling widgets already use the same
QButtonGroup/QToolButton shape (`_ToolGroupButton` in `canvas_tools.py`).
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import GuiTheme, get_active_theme

from ..workflow.transforms_settings import ROW_HEIGHT as _TRANSFORMS_ROW_HEIGHT
from .canvas_tools import _BAR_MARGIN, _BUTTON_SIZE

_CANVAS_TOOLS_ROW_HEIGHT = _BUTTON_SIZE + 2 * _BAR_MARGIN
# The "Mask" and "Histogram" tabs' captioned icon groups (`ribbon_group.py`'s
# `labeled_icon_group`, 2026-10-02) each carry a small caption under their
# icons now - 28px icon row (`MaskScopeToggle`/`MaskOverlayControls`/
# `HistogramHighlightOverlayControls`'s own button size) + 2px group spacing
# + 12px for one line of 9px-font caption text (measured via
# `QLabel.sizeHint()`, not guessed - font rendering isn't just the pixel
# size). Both tabs land on the same 42px, so one constant covers both. Kept
# as a literal here rather than imported from `panel.py` - `panel.py`
# imports this module, so the reverse would be a real cycle.
_CAPTIONED_GROUP_ROW_HEIGHT = 42
# The tallest of the real categories' own natural heights - today that's
# the "Image tools" tab's `TransformsSection` (50px, since its own
# "Calibrate" group - 2026-10-02 - is a captioned group too, plus
# `TransformsSection`'s own 4px top/bottom margin) over the "Mask"/
# "Histogram" captioned-group height (42px) over the "ROIs" tab's
# `CanvasToolsBar` (26px). Shorter content just sits centered in the extra
# room - see module docstring for why one fixed height for every category,
# not a per-category one, is the point.
_ROW_HEIGHT = max(_CANVAS_TOOLS_ROW_HEIGHT, _TRANSFORMS_ROW_HEIGHT, _CAPTIONED_GROUP_ROW_HEIGHT)
_TAB_HEIGHT = 20
_TAB_FONT_SIZE_PX = 11


def _category_placeholder(name: str) -> QWidget:
    """Same "<name> - not built yet." wording `workflow/panel.py`'s
    `_section_placeholder` uses for an unbuilt stage - fixed to this bar's
    row height instead of that one's word-wrapped block, since this row
    never grows/shrinks with its content (see module docstring)."""
    label = QLabel(f"{name} - not built yet.")
    label.setFixedHeight(_ROW_HEIGHT)
    label.setStyleSheet(f"color: {get_active_theme().text_muted}; padding-left: 4px; font-size: {_TAB_FONT_SIZE_PX}px;")
    return label


def _tab_button_stylesheet(theme: GuiTheme) -> str:
    return f"""
        QToolButton {{
            color: {theme.text_muted};
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            padding: 2px 8px 0px 8px;
            font-size: {_TAB_FONT_SIZE_PX}px;
        }}
        QToolButton:hover {{
            color: {theme.text_primary};
        }}
        QToolButton:checked {{
            color: {theme.text_primary};
            border-bottom: 2px solid {theme.accent_blue};
        }}
    """


class ImageToolRibbon(QWidget):
    """Category tabs (top row) over a fixed-height content stack (bottom
    row). *categories* is an ordered ``(label, content)`` sequence; a
    ``None`` content gets the standard "not built yet" placeholder (see
    `_category_placeholder`). The first category starts active.

    `category_changed` (2026-10-03) announces the newly shown tab's label, so
    a panel elsewhere can react to which ribbon section is in focus - the
    Histogram plot only honours the area selection while "Histogram" is the
    shown tab (maintainer's spec)."""

    category_changed = pyqtSignal(str)

    def __init__(
        self,
        categories: Sequence[tuple[str, QWidget | None]],
        parent: QWidget | None = None,
        pinned: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pinned_separator: QFrame | None = None
        if not categories:
            raise ValueError("ImageToolRibbon needs at least one category")
        self.setObjectName("imageToolRibbon")

        self._stack = QStackedWidget(self)
        self._stack.setFixedHeight(_ROW_HEIGHT)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons: list[QToolButton] = []
        self._category_names = [name for name, _content in categories]

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(_BAR_MARGIN, 0, _BAR_MARGIN, 0)
        tab_row.setSpacing(2)

        for index, (name, content) in enumerate(categories):
            button = QToolButton(self)
            button.setText(name)
            button.setCheckable(True)
            button.setFixedHeight(_TAB_HEIGHT)
            button.clicked.connect(lambda _checked=False, i=index: self._show_category(i))
            self._tab_group.addButton(button)
            self._tab_buttons.append(button)
            tab_row.addWidget(button)
            self._stack.addWidget(content if content is not None else _category_placeholder(name))
        tab_row.addStretch(1)

        self._tab_buttons[0].setChecked(True)
        self._stack.setCurrentIndex(0)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addLayout(tab_row)
        layout.addWidget(self._stack)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        if pinned is not None:
            # Always-visible leading tab (2026-10-03): no caption; its
            # content sits on the content row, under a blank tab-strip slot.
            pinned_column = QVBoxLayout()
            pinned_column.setContentsMargins(_BAR_MARGIN, 0, 0, 0)
            pinned_column.setSpacing(1)
            pinned_column.addSpacing(_TAB_HEIGHT)
            pinned_column.addWidget(pinned, 0, Qt.AlignmentFlag.AlignTop)
            pinned_column.addStretch(1)
            outer.addLayout(pinned_column)
            self._pinned_separator = QFrame(self)
            self._pinned_separator.setFrameShape(QFrame.Shape.VLine)
            outer.addWidget(self._pinned_separator)
        outer.addLayout(layout)

        self.refresh_theme(get_active_theme())

    def current_category(self) -> str:
        return self._category_names[self._stack.currentIndex()]

    def _show_category(self, index: int) -> None:
        changed = index != self._stack.currentIndex()
        self._stack.setCurrentIndex(index)
        if changed:
            self.category_changed.emit(self._category_names[index])

    def refresh_theme(self, theme: GuiTheme) -> None:
        """Re-applies the tab strip's QSS (checked/hover state is handled
        by the stylesheet's own pseudo-selectors, not re-applied per click -
        see `_tab_button_stylesheet`). Content widgets are themed by
        whoever owns them (`panel.py` themes `CanvasToolsBar` directly
        through its own `_canvas_tools` reference); this method only
        touches the tabs themselves."""
        stylesheet = _tab_button_stylesheet(theme)
        for button in self._tab_buttons:
            button.setStyleSheet(stylesheet)
        if self._pinned_separator is not None:
            self._pinned_separator.setStyleSheet(f"color: {theme.control_border};")
