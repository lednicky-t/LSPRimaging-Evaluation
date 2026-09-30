"""Image panel's tool ribbon (2026-09-30): a two-row structure above the
canvas - a top row of category tabs and a bottom row that swaps to show
the active category's tools, Office/Inkscape/Photoshop-style (maintainer's
own reference points, 2026-09-30).

Replaces the single-row bar `panel.py` used to embed directly: `panel.py`
now builds one `ImageToolRibbon` with three seeded categories - "Image
tools", "Histogram", "ROIs" - and embeds that instead. `CanvasToolsBar`
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

**"Histogram" is still a seeded placeholder** ("we will fill as we go" -
maintainer, 2026-09-30) - no Histogram-category content exists yet. It
renders the same "<name> - not built yet." wording `workflow/panel.py`'s
`_section_placeholder` already uses for an unbuilt Workflow stage - same
convention, not a new one, so an empty category here reads the same way an
empty Workflow stage already does elsewhere in this app. Subcategories (a
third row, or per-category sub-tabs within the bottom row) are not built -
the maintainer's plan is to add them once a category actually has enough
tools to need grouping within itself, not to guess at that structure now.

**The bottom row is a fixed height** regardless of which category is
showing (`_ROW_HEIGHT` - the taller of `CanvasToolsBar`'s and
`TransformsSection`'s own natural heights), so switching tabs never resizes
the Image panel's top bar - `QStackedWidget` would otherwise report
whichever page happens to be the tallest.

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

from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QStackedWidget, QToolButton, QVBoxLayout, QWidget

from lspr_ui import GuiTheme, get_active_theme

from ..workflow.transforms_settings import ROW_HEIGHT as _TRANSFORMS_ROW_HEIGHT
from .canvas_tools import _BAR_MARGIN, _BUTTON_SIZE

_CANVAS_TOOLS_ROW_HEIGHT = _BUTTON_SIZE + 2 * _BAR_MARGIN
# The taller of the two real categories' own natural heights - today that's
# the "Image tools" tab's `TransformsSection` (36px: 28px buttons + 4px
# margins) over the "ROIs" tab's `CanvasToolsBar` (26px). Shorter content
# (a placeholder, or `CanvasToolsBar`) just sits centered in the extra room -
# see module docstring for why one fixed height for every category, not a
# per-category one, is the point.
_ROW_HEIGHT = max(_CANVAS_TOOLS_ROW_HEIGHT, _TRANSFORMS_ROW_HEIGHT)
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
    `_category_placeholder`). The first category starts active."""

    def __init__(self, categories: Sequence[tuple[str, QWidget | None]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not categories:
            raise ValueError("ImageToolRibbon needs at least one category")
        self.setObjectName("imageToolRibbon")

        self._stack = QStackedWidget(self)
        self._stack.setFixedHeight(_ROW_HEIGHT)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_buttons: list[QToolButton] = []

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(_BAR_MARGIN, 0, _BAR_MARGIN, 0)
        tab_row.setSpacing(2)

        for index, (name, content) in enumerate(categories):
            button = QToolButton(self)
            button.setText(name)
            button.setCheckable(True)
            button.setFixedHeight(_TAB_HEIGHT)
            button.clicked.connect(lambda _checked=False, i=index: self._stack.setCurrentIndex(i))
            self._tab_group.addButton(button)
            self._tab_buttons.append(button)
            tab_row.addWidget(button)
            self._stack.addWidget(content if content is not None else _category_placeholder(name))
        tab_row.addStretch(1)

        self._tab_buttons[0].setChecked(True)
        self._stack.setCurrentIndex(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addLayout(tab_row)
        layout.addWidget(self._stack)

        self.refresh_theme(get_active_theme())

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
