"""``PanelContainer`` - a ``QDockWidget`` with a custom title bar (float/
maximize/close buttons, an optional help button, an optional subtitle).

Ported from the stable app's ``gui/widgets.py`` (2026-09-24, GUI shell
design pass - see ``docs/rewrite_gui_shell_design_2026-09.md``), which has
used this exact component for every panel in production for a while: real
undock/float/resize, a fix for Qt's own bug where a restored floating panel
can land off-screen, and a deliberate "docking disabled while floating"
safety (see ``_on_top_level_changed``) so dragging a floating panel over the
main window doesn't trigger an accidental snap-back.

**Two deliberate deviations from the source, everything else copied
verbatim** (float/maximize/close behavior, the docking-disabled-while-
floating safety, the paintEvent-drawn floating border, the subtitle label):

1. The source's help-button icon goes through ``MainWindowIcons``, which
   falls back to the ``lucide`` icon library - a dependency this app's icon
   policy deliberately avoids (see ``packages/lspr_ui/ICONS.md`` - icons
   come from ``lspr_ui``'s own vendored set, not a third-party icon
   package). ``_make_help_icon`` below renders the vendored ``info-circle``
   tabler icon instead, using the exact same inline SVG-to-QIcon pattern
   this class already uses for its close/float/maximize icons - no new icon
   or dependency added.
2. The source's ``title_options`` clickable-segment title (e.g. "ROI" /
   "Group") is back in a small form (2026-10-24, ``set_title_choices``): the
   ROI / Groups panel's title is two words, the one in effect highlighted, the
   other picked by a click or a double-click. Its ``_make_chevron_icon`` /
   ``_make_apply_icon`` helpers are still left out.

**Two additions the source doesn't have at all**:

- The optional ``collapsible`` constructor flag (design doc §4) -
  click-to-collapse to a thin strip, not the source's static title bar.
  The width the panel had is remembered and given back on expand.
- The optional ``min_width`` constructor flag: a floor for the *expanded*
  width, so the panel cannot be dragged (or restored from a saved layout
  that was taken while it was collapsed) to a sliver. It is a floor, not a
  clamp - the panel is freely resizable above it.

Both are used only by the Workflow panel today (``app_rewrite.
build_main_window``); every other panel passes the defaults
(``collapsible=False``, ``min_width=None``) and is unaffected.

**History (2026-10-18, maintainer decision):** Workflow used to be locked to
an exact ``fixed_width`` (340 px), with the dock separator beside it made
inert, on the reasoning that a tool panel should never be squeezed until its
controls clip. The maintainer reversed that: Workflow is resizable like every
other panel. What replaced the clamp is a horizontal scroll bar inside the
Workflow panel (``panels/workflow/panel.py``) that appears whenever a row needs
more room than the dock has, so narrowing it hides nothing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    get_active_theme,
    hex_to_rgba,
    tabler_icon_svg,
    transparent_icon_button_stylesheet,
)

from .subtitle_column import SubtitleColumn

# Neutral translucent grey, not a theme color (2026-09-27, VS Code style
# pass) - matches dock_separator_stylesheet's own rgba(128,128,128,...)
# reasoning in gui/app_theme.py: VS Code's real toolbar-icon hover
# (`toolbar.hoverBackground`) is itself a semi-transparent grey, not a
# tinted accent color, and titling every title-bar icon (help/collapse/
# float/maximize) in accent_blue on hover was part of what made this read
# as generic app chrome instead of VS Code's specific, restrained look.
# The close button keeps its own accent_red hover (matches OS/VS Code
# convention for a close control specifically, not a generic mismatch).
_TITLE_BAR_ICON_HOVER = "rgba(128, 128, 128, 0.20)"

# Card-style docked-panel framing (2026-09-27, VS Code style pass - the
# maintainer's own screenshot of VS Code's real docked-panel look: each
# panel its own rounded-corner outline with a visible gap/canvas-colored
# space between adjacent panels, not panels touching edge to edge). See
# PanelContainer._apply_frame_style/paintEvent.
# Gap halved (2026-09-27, follow-up, maintainer request) from an original
# 5px.
_CARD_GAP_PX = 3
_CARD_RADIUS_PX = 6
_CARD_BOTTOM_EXTRA_PX = 1  # content sits one pixel higher at the bottom: its square corners used to nick the rounded border (2026-10-24)
_CARD_RIGHT_EXTRA_PX = 1  # and one pixel further from the right edge, for the same reason

# The title bar's status fields (`set_subtitle_columns`, 2026-10-16): the gap between two
# fields, and a few spare pixels on a fixed-width field so its widest text never touches the edge.
_SUBTITLE_COLUMN_GAP_PX = 14
_SUBTITLE_TITLE_GAP_PX = 8  # between the title and the first field
_SUBTITLE_COMMENT_MIN_PX = 40  # the comment field is shown only if at least this much of it fits
_SUBTITLE_COLUMN_SLACK_PX = 3

_QWIDGETSIZE_MAX = 16777215  # Qt's "no maximum"
_RESTORE_WIDTH_MAX_STEPS = 8  # see `PanelContainer._apply_width_after_collapse`


def _widest_text_width(label: QLabel, widest: str) -> int:
    """Pixel width of *widest* in *label*'s font, every digit counted as the font's widest digit
    (a proportional font may give "1" and "8" different widths; the shown text must never be wider)."""
    label.ensurePolished()
    metrics = label.fontMetrics()
    digit = max("0123456789", key=metrics.horizontalAdvance)
    return metrics.horizontalAdvance(re.sub(r"\d", digit, widest)) + _SUBTITLE_COLUMN_SLACK_PX


class _ElidedLabel(QLabel):
    """A label that never asks for more room than it is given: text that does not fit is cut with
    "..." at the right (the full text stays in `full_text()`, and is the tooltip if none is set)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self._tooltip = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str, tooltip: str = "") -> None:
        self._full = text
        self._tooltip = tooltip
        self._elide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 0))
        if self.text() != shown:
            self.setText(shown)
        tip = self._tooltip or (self._full if shown != self._full else "")
        if self.toolTip() != tip:
            self.setToolTip(tip)


def _render_tabler_icon(name: str, color: str, *, stroke_width: float = 2.2) -> QIcon:
    """Shared 20x20 render used by every title-bar icon below - matches
    the source class's own inline pattern exactly (same size, same 1px
    inset) so ported and non-ported icons look identical."""
    svg = tabler_icon_svg(name, color=color, stroke_width=stroke_width)
    if not svg:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(1.0, 1.0, 18.0, 18.0))
    painter.end()
    return QIcon(pixmap)


class _VerticalLabel(QWidget):
    """Plain text rotated 90 degrees, read bottom-to-top - the collapsed
    strip (36px wide, see ``PanelContainer._collapsed_width``) has no
    horizontal room for its title, only vertical; this is what makes a
    collapsed panel self-explanatory instead of just a bare arrow (2026-09-27,
    found via the maintainer collapsing/hiding Workflow and it no longer
    being obvious what that strip was)."""

    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._color = QColor(color)
        # Passive display only - clicks must fall through to the strip
        # widget underneath (_ClickableStrip), which is what actually
        # expands the panel; without this, a click landing on this label's
        # own rect would be swallowed here and never reach it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        # Swapped on purpose: the label's on-screen *width* is the rotated
        # text's height (font height), and its *height* is the text's
        # unrotated length - matches the coordinate swap paintEvent performs.
        return QSize(metrics.height() + 4, metrics.horizontalAdvance(self._text) + 8)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setPen(self._color)
        # Rotate around the strip's own center, not the text's - the
        # untranslated origin sits at this widget's top-left, so the origin
        # has to move to the middle of the (still-unrotated) width before
        # rotating, then again to the vertical center of the (rotated)
        # width, which becomes the label's height.
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(self._text)
        painter.drawText(
            QRect(int(-text_width / 2), int(-metrics.height() / 2), text_width, metrics.height()),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )
        painter.end()


class _ColumnsHost(QWidget):
    """The row of the title bar's status fields. Its minimum width is 0 (2026-10-24): a dock narrower than all the fields shows
    the first ones and drops the rest whole, from the right (`on_resize` decides which), instead of keeping the dock wide."""

    def __init__(self, parent: QWidget, on_resize) -> None:
        super().__init__(parent)
        self._on_resize = on_resize

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._on_resize()


@dataclass(frozen=True)
class TitleChoice:
    """One word of a title that doubles as a switch (``PanelContainer.set_title_choices``)."""

    text: str
    tooltip: str
    on_double_click: bool = False  # True: a stray single click does nothing (the pick has a big effect)


class _TitleWord(QLabel):
    """A word in the title bar that reports clicks. It accepts the mouse events itself, because an unaccepted double click on a dock's
    title bar makes Qt float the dock."""

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        event.accept()


class _ClickableStrip(QWidget):
    """A whole-widget clickable area - `QToolButton` can't lay out an
    icon-above-rotated-text combination the way `_build_collapsed_strip`
    needs, so this is a plain `QWidget` (children laid out via a normal
    `QVBoxLayout`) with its own click handling instead, keeping the
    original "no dead area to miss a click on" property that class's
    docstring calls out."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PanelContainer(QDockWidget):
    title_choice_picked = pyqtSignal(int)
    """The user picked word ``i`` of a switch title (``set_title_choices``). Nothing changes until the owner calls ``set_title_current``."""

    def __init__(
        self,
        title: str,
        content: QWidget,
        parent: QWidget | None = None,
        *,
        help_text: str | None = None,
        collapsible: bool = False,
        min_width: int | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._title = title
        self._content = content
        self._help_text = help_text
        self._subtitle_text = ""
        self._subtitle_tooltip = ""
        self._subtitle_label: QLabel | None = None
        # Status fields at fixed places (`set_subtitle_columns`); when set, they replace the centred text.
        self._subtitle_columns: tuple[SubtitleColumn, ...] = ()
        self._subtitle_centered: QWidget | None = None
        self._subtitle_columns_host: QWidget | None = None
        self._subtitle_column_labels: list[QLabel] = []
        self._subtitle_column_keys: list[tuple[str, str | None, str]] = []
        self._muted_color = ""
        self._title_choices: tuple[TitleChoice, ...] = ()
        self._title_current = 0
        self._title_words: list[QLabel] = []
        self._title_separators: list[QLabel] = []
        self.setObjectName(f"{title.replace(' ', '')}Panel")
        self._frame_border_color: str | None = None
        self._frame_fill_color: str | None = None
        self._frame_radius: int = 0
        # Click-to-collapse to a thin strip (design doc §4) - Workflow only
        # today. `_collapsed_width` is deliberately generous next to VS
        # Code's ~48px sidebar rail: this strip has to fit a whole button
        # with real click/hover affordance, not just an icon glyph.
        self._collapsible = collapsible
        self._collapsed = False
        self._collapsed_width = 36
        # Floor for the expanded width (a floor, not a clamp) - see module
        # docstring. `_width_before_collapse` is what `_set_collapsed(False)`
        # gives the panel back: Qt does not regrow a dock by itself once its
        # fixed collapsed width is released.
        self._min_width = min_width
        self._width_before_collapse: int | None = None
        self._restore_tries_left = 0
        self.setWidget(content)
        if self._min_width is not None:
            self.setMinimumWidth(self._min_width)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # While floating, docking is disabled by default (see
        # _on_top_level_changed) so dragging the floating window around/over
        # the main app doesn't trigger Qt's dock-target hint overlay or risk
        # an accidental snap-back. The float button re-arms docking on
        # click, tracked here so it can also gate a second click.
        self._dock_armed = False
        # Maximize state, floating-only (see _build_title_bar /
        # _on_maximize_button_clicked): whether this panel currently fills
        # its screen's available geometry, and the geometry to restore when
        # un-maximized.
        self._maximized = False
        self._pre_maximize_geometry: QRect | None = None

        theme = get_active_theme()
        self._apply_frame_style(theme)
        # QDockWidget's built-in close/float buttons hard-code their icon's
        # pixel size in Qt's C++ paint code; a custom title bar with real
        # QToolButtons is the only way to make the glyphs bigger.
        self.setTitleBarWidget(self._build_title_bar(title, theme))
        self.topLevelChanged.connect(self._on_top_level_changed)

    def refresh_theme(self) -> None:
        """Rebuild the custom title bar with the currently active theme.

        The title bar's colors are plain per-widget setStyleSheet() calls
        baked in at construction time, not QSS resolved through the
        QApplication-level stylesheet/palette - so unlike most of the app,
        they don't just pick up a live theme switch on their own and need to
        be rebuilt explicitly. Call this on every ``PanelContainer`` after a
        theme switch. While collapsed, rebuilds the collapsed strip/title
        bar instead - _build_title_bar's buttons (`_float_button`, etc.)
        only exist on the expanded title bar, so blindly rebuilding that one
        while collapsed would leave `_on_top_level_changed` reaching for
        attributes a collapsed title bar was never given."""
        theme = get_active_theme()
        self._apply_frame_style(theme)
        if self._collapsed:
            self.setWidget(self._build_collapsed_strip(theme))
            self.setTitleBarWidget(self._build_collapsed_title_bar(theme))
        else:
            self.setTitleBarWidget(self._build_title_bar(self._title, theme))

    def _apply_frame_style(self, theme) -> None:
        """A floating PanelContainer is a real top-level OS window, but with
        no native title bar (setTitleBarWidget replaces it) the OS-drawn
        window border is often just a 1px, barely-visible line. Drawing a
        single stroke directly on ``self`` in paintEvent() avoids the doubled
        -line artifacts a QSS-based border produced (see source history) -
        there's exactly one rectangle, and it can't misalign with itself.
        ``setContentsMargins`` reserves the ring so the title bar/content
        children don't paint over it.

        Docked panels get real card framing (2026-09-27, VS Code style pass)
        - a rounded-corner outline with a visible gap around it, not just a
        floating window's thin 1px edge. Left as the simple sharp-rect/1px
        style for a *floating* panel: it's a real top-level OS window with
        its own rectangular shape at the compositor level, so true rounded
        exterior corners there would need masking the window's shape, not
        just painting a rounded line inside a still-rectangular window
        (which would leave the window's actual four corners a visibly
        different, unrounded color poking out past the drawn curve) -
        floating panels are a separate, real piece of work, not covered by
        this pass."""
        self.setStyleSheet(f"QDockWidget {{ color: {theme.text_primary}; }}")
        if self.isFloating():
            self.setContentsMargins(1, 1, 1, 1)
            self._frame_border_color = theme.toolbar_border
            self._frame_fill_color = None
            self._frame_radius = 0
        else:
            self.setContentsMargins(
                _CARD_GAP_PX, _CARD_GAP_PX, _CARD_GAP_PX + _CARD_RIGHT_EXTRA_PX, _CARD_GAP_PX + _CARD_BOTTOM_EXTRA_PX
            )
            self._frame_border_color = theme.toolbar_border
            # theme.toolbar_bg, not theme.window_bg (2026-09-27, follow-up -
            # maintainer screenshot comparison against real VS Code): a
            # panel's own interior is meant to be a *darker* shade than the
            # canvas/gap around it (real VS Code: #1e1e1e editor vs #252526
            # sidebar) - window_bg governs the gap itself (via the
            # QApplication-wide palette, showing through outside this
            # rounded rect) and is deliberately the *lighter* value in the
            # VS Code Dark theme now, so filling this card with window_bg
            # too would make the whole panel the wrong (lighter) shade.
            # toolbar_bg is already what ImagePanel uses for its plot
            # canvas fill - same "darker interior" role, reused here rather
            # than introducing a third color concept.
            self._frame_fill_color = theme.toolbar_bg
            self._frame_radius = _CARD_RADIUS_PX
            # The content widget itself is a plain QWidget with no
            # stylesheet of its own - without this, it inherits window_bg
            # from the app-wide QSS rule (QWidget { background-color: ... })
            # and paints that *over* the darker card fill just drawn above,
            # since a child widget's own paint happens after its parent's.
            # Pinning it to the same toolbar_bg here is what actually makes
            # the interior read as the darker shade instead of the lighter
            # gap color bleeding through.
            self._content.setStyleSheet(f"background-color: {theme.toolbar_bg};")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._frame_border_color is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(self._frame_border_color)))
        painter.setBrush(QColor(self._frame_fill_color) if self._frame_fill_color is not None else Qt.BrushStyle.NoBrush)
        if self._frame_radius:
            # Border line centered in the reserved gap (half the gap
            # between the widget's outer edge and the inset border, half
            # between the border and the content it encloses).
            inset = _CARD_GAP_PX // 2
            rect = self.rect().adjusted(inset, inset, -1 - inset, -1 - inset)
            painter.drawRoundedRect(rect, self._frame_radius, self._frame_radius)
        else:
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _build_title_bar(self, title: str, theme) -> QWidget:
        bar = QWidget(self)
        # Transparent, not theme.window_bg (2026-09-27, VS Code style pass -
        # found via a card border that visibly failed to close along its
        # top edge). QDockWidget positions a custom title bar widget flush
        # against the dock's absolute top-left corner, *ignoring* the
        # contentsMargins reserved for the card gap in _apply_frame_style
        # (confirmed by inspecting its geometry directly: title bar y=0 even
        # with a 5px top margin set) - so an opaque bar background paints
        # straight over the top-left arc of the rounded border
        # PanelContainer.paintEvent draws *underneath* it, erasing that
        # portion of the loop. Transparent lets the parent's own paintEvent
        # fill (same window_bg color) and border stroke show through
        # instead, which is what actually closes the loop - not a
        # difference in what color gets painted, since both were window_bg,
        # just which widget paints it and in what order.
        bar.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QWidget(bar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(2)
        outer.addWidget(row)

        label = self._build_title_label(title, row, theme)
        # font-weight 400, not 600 (2026-09-27, VS Code style pass) - VS
        # Code's own editor-group/tab titles are regular weight, not
        # semi-bold; the heavier weight was part of what made this read as
        # a generic app chrome rather than VS Code's specific look.
        if isinstance(label, QLabel):
            label.setStyleSheet(f"color: {theme.text_primary}; font-weight: 400; background: transparent;")
        layout.addWidget(label)

        # Centered between the title and the button row (2026-09-30,
        # maintainer request, Image panel's Cube/wavelength readout) - a
        # stretch of equal weight on both sides of the label puts it at the
        # midpoint of whatever space is left between them, rather than
        # crowding it right up against the title the way a single trailing
        # stretch would.
        centered = QWidget(row)
        centered_layout = QHBoxLayout(centered)
        centered_layout.setContentsMargins(0, 0, 0, 0)
        centered_layout.setSpacing(0)
        centered_layout.addStretch(1)
        subtitle_label = QLabel(self._subtitle_text, centered)
        subtitle_label.setStyleSheet(f"color: {theme.text_muted}; font-weight: 400; background: transparent;")
        subtitle_label.setToolTip(self._subtitle_tooltip)
        subtitle_label.setVisible(bool(self._subtitle_text))
        centered_layout.addWidget(subtitle_label)
        centered_layout.addStretch(1)
        layout.addWidget(centered, 1)
        self._subtitle_label = subtitle_label
        self._subtitle_centered = centered

        # Status fields (`set_subtitle_columns`): left-aligned right after the title, each at a
        # fixed place; the last, unsized one (the comment) takes all the room that is left.
        columns_host = _ColumnsHost(row, self._fit_subtitle_columns)
        columns_layout = QHBoxLayout(columns_host)
        columns_layout.setContentsMargins(_SUBTITLE_TITLE_GAP_PX, 0, 0, 0)
        columns_layout.setSpacing(_SUBTITLE_COLUMN_GAP_PX)
        layout.addWidget(columns_host, 1)
        self._subtitle_columns_host = columns_host
        self._subtitle_column_labels = []
        self._subtitle_column_keys = []
        self._muted_color = theme.text_muted
        self._apply_subtitle_columns()

        if self._help_text:
            help_text = self._help_text
            help_button = QToolButton(row)
            help_button.setIcon(self._make_help_icon(theme))
            help_button.setIconSize(QSize(18, 18))
            help_button.setFixedSize(24, 24)
            help_button.setAutoRaise(True)
            help_button.setToolTip("Show panel help.")
            help_button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER)
                + "QToolButton:hover { border-radius: 4px; }"
            )
            help_button.clicked.connect(lambda *_: QMessageBox.information(self, title, help_text))
            layout.addWidget(help_button)

        if self._collapsible:
            collapse_button = QToolButton(row)
            collapse_button.setIcon(_render_tabler_icon("chevron-left", theme.text_muted))
            collapse_button.setIconSize(QSize(18, 18))
            collapse_button.setFixedSize(24, 24)
            collapse_button.setAutoRaise(True)
            collapse_button.setToolTip(f"Collapse {title} to a thin strip.")
            collapse_button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER)
                + "QToolButton:hover { border-radius: 4px; }"
            )
            collapse_button.clicked.connect(lambda: self._set_collapsed(True))
            layout.addWidget(collapse_button)

        float_button = QToolButton(row)
        float_button.setIcon(self._make_float_icon())
        float_button.setIconSize(QSize(18, 18))
        float_button.setFixedSize(24, 24)
        float_button.setAutoRaise(True)
        float_button.setToolTip("Undock this panel into a floating window.")
        float_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER)
            + "QToolButton:hover { border-radius: 4px; }"
        )
        float_button.clicked.connect(self._on_float_button_clicked)
        layout.addWidget(float_button)
        self._float_button = float_button

        # Only meaningful once floating (there's no "screen" to fill while
        # docked into the main window) - starts hidden and is shown by
        # _on_top_level_changed.
        maximize_button = QToolButton(row)
        maximize_button.setIcon(self._make_maximize_icon())
        maximize_button.setIconSize(QSize(18, 18))
        maximize_button.setFixedSize(24, 24)
        maximize_button.setAutoRaise(True)
        maximize_button.setToolTip("Maximize this panel to fill the screen.")
        maximize_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER)
            + "QToolButton:hover { border-radius: 4px; }"
        )
        maximize_button.clicked.connect(self._on_maximize_button_clicked)
        maximize_button.setVisible(self.isFloating())
        layout.addWidget(maximize_button)
        self._maximize_button = maximize_button

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

        # No divider line under the title (2026-09-27, maintainer request) -
        # the title row is now visually part of the same card as the
        # content below it, matching VS Code's own panels, rather than a
        # separately-boxed header.

        return bar

    def _build_title_label(self, title: str, parent: QWidget, theme) -> QWidget:
        """The plain title text, or - with ``set_title_choices`` - the words of the switch title."""
        self._title_words = []
        self._title_separators = []
        if not self._title_choices:
            return QLabel(title, parent)
        host = QWidget(parent)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for index, choice in enumerate(self._title_choices):
            if index:
                separator = QLabel("/", host)
                layout.addWidget(separator)
                self._title_separators.append(separator)
            word = _TitleWord(choice.text, host)
            word.setToolTip(choice.tooltip)
            word.setCursor(Qt.CursorShape.PointingHandCursor)
            signal = word.double_clicked if choice.on_double_click else word.clicked
            signal.connect(lambda picked=index: self._on_title_word_activated(picked))
            layout.addWidget(word)
            self._title_words.append(word)
        self._style_title_words(theme)
        return host

    def _style_title_words(self, theme) -> None:
        for index, word in enumerate(self._title_words):
            on = index == self._title_current
            word.setStyleSheet(
                f"color: {theme.accent_blue if on else theme.text_dim}; font-weight: {600 if on else 400}; background: transparent;"
            )
        for separator in self._title_separators:
            separator.setStyleSheet(f"color: {theme.text_dim}; font-weight: 400; background: transparent;")

    def set_title_choices(self, choices: Sequence[TitleChoice], current: int = 0) -> None:
        """Turn the title into a switch of words (e.g. ``ROI / Groups``): ``current`` is highlighted, a click (or, for a choice with
        ``on_double_click``, a double click) on another word emits ``title_choice_picked``."""
        self._title_choices = tuple(choices)
        self._title_current = current
        self.refresh_theme()

    def set_title_current(self, index: int) -> None:
        """Highlight word ``index`` (no signal)."""
        self._title_current = index
        self._style_title_words(get_active_theme())

    def _on_title_word_activated(self, index: int) -> None:
        if index != self._title_current:
            self.title_choice_picked.emit(index)

    def set_subtitle(self, text: str, tooltip: str = "") -> None:
        """Short status text shown after the panel title, e.g. a live ROI/
        cube count a panel wants visible without stealing space from its own
        content."""
        self._subtitle_text = text
        self._subtitle_tooltip = tooltip
        if self._subtitle_label is not None:
            self._subtitle_label.setText(text)
            self._subtitle_label.setToolTip(tooltip)
            self._subtitle_label.setVisible(bool(text))

    def set_subtitle_columns(self, columns: Sequence[SubtitleColumn]) -> None:
        """Status fields after the panel title (2026-10-16, maintainer request: "fields will have
        fixed position"): left-aligned in the order given, a column with a `widest` text has exactly
        that width, the one without takes the rest. A field that is empty keeps its place. An empty
        *columns* goes back to the centred `set_subtitle` text."""
        columns = tuple(columns)
        if columns == self._subtitle_columns:
            return
        self._subtitle_columns = columns
        self._apply_subtitle_columns()

    def _apply_subtitle_columns(self) -> None:
        """Show `_subtitle_columns` in the title bar (called on every frame step during Play, so
        only what changed is touched; the labels are made again only when the shape changes)."""
        host = self._subtitle_columns_host
        if host is None or self._subtitle_centered is None:
            return
        columns = self._subtitle_columns
        self._subtitle_centered.setVisible(not columns)
        host.setVisible(bool(columns))
        if not columns:
            return
        layout = host.layout()
        shape = [bool(column.widest) for column in columns]  # True = fixed width, False = takes the rest
        if shape != [not isinstance(label, _ElidedLabel) for label in self._subtitle_column_labels]:
            for label in self._subtitle_column_labels:
                layout.removeWidget(label)
                label.deleteLater()
            self._subtitle_column_labels = []
            self._subtitle_column_keys = []
            for sized in shape:
                label = QLabel(host) if sized else _ElidedLabel(host)
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                layout.addWidget(label, 0 if sized else 1)
                self._subtitle_column_labels.append(label)
                self._subtitle_column_keys.append(("", None, ""))
        for index, (label, column) in enumerate(zip(self._subtitle_column_labels, columns)):
            key = (column.widest, column.color, self._muted_color)
            if self._subtitle_column_keys[index] != key:
                self._subtitle_column_keys[index] = key
                label.setStyleSheet(
                    f"color: {column.color or self._muted_color}; font-weight: 400; background: transparent;"
                )
                if column.widest:
                    label.setFixedWidth(_widest_text_width(label, column.widest))
            if isinstance(label, _ElidedLabel):
                label.set_full_text(column.text, column.tooltip)
            else:
                if label.text() != column.text:
                    label.setText(column.text)
                if label.toolTip() != column.tooltip:
                    label.setToolTip(column.tooltip)
        self._fit_subtitle_columns()

    def _fit_subtitle_columns(self) -> None:
        """Show the status fields that fit the room the title bar has, from the left, and hide the rest (a field is shown whole
        or not at all). The comment, which has no fixed width, needs `_SUBTITLE_COMMENT_MIN_PX`."""
        host = self._subtitle_columns_host
        if host is None or not self._subtitle_column_labels:
            return
        layout = host.layout()
        labels = self._subtitle_column_labels
        # A fixed-width field at the very end that is empty takes no room (2026-10-24): nothing comes after it, so
        # no other field can move, and the comment before it gets the width instead of being cut short beside a blank.
        empty_tail = 0
        for label in reversed(labels):
            if isinstance(label, _ElidedLabel) or label.text():
                break
            empty_tail += 1
        x = layout.contentsMargins().left()
        fits = True
        for index, label in enumerate(labels):
            need = _SUBTITLE_COMMENT_MIN_PX if isinstance(label, _ElidedLabel) else label.minimumWidth()
            if index >= len(labels) - empty_tail:
                label.setVisible(False)  # never guarded by isHidden(): a new label is "hidden" without being hidden *explicitly*, and Qt shows it later
            elif fits and x + need <= host.width():
                if not label.isVisibleTo(host):
                    label.setVisible(True)
                x += need + layout.spacing()
            else:
                fits = False
                label.setVisible(False)

    def _set_collapsed(self, collapsed: bool) -> None:
        """Click-to-collapse/expand (design doc §4 - the maintainer chose
        this over a hover-to-peek flyout: simpler, deterministic, no new
        overlay/mouse-tracking machinery, at the cost of the panel
        reclaiming layout space only on an explicit click rather than
        auto-retracting)."""
        if not self._collapsible or collapsed == self._collapsed:
            return
        theme = get_active_theme()
        outgoing_widget = self.widget()
        self._collapsed = collapsed
        if collapsed:
            if self.width() > self._collapsed_width:  # not while a pending expand has yet to widen the dock
                self._width_before_collapse = self.width()
            self.setWidget(self._build_collapsed_strip(theme))
            self.setTitleBarWidget(self._build_collapsed_title_bar(theme))
            self.setFixedWidth(self._collapsed_width)
        else:
            # Undoes setFixedWidth's implicit clamp - Qt has no single call
            # to "unfix" a width once fixed. The minimum goes back to the
            # panel's own floor, not to 0.
            self.setMinimumWidth(self._min_width or 0)
            self.setMaximumWidth(_QWIDGETSIZE_MAX)
            self.setWidget(self._content)
            self._content.show()
            self.setTitleBarWidget(self._build_title_bar(self._title, theme))
            self._restore_width_after_collapse()
        # Explicitly hidden before the event loop can repaint: a widget
        # QDockWidget.setWidget() just displaced becomes parentless, and a
        # visible parentless widget is a real top-level OS window for
        # however long it stays that way (see CLAUDE.md's documented
        # startup-flicker pitfall - the same trap, encountered here on
        # swap-out rather than construction). Hiding it in the same call
        # stack that displaces it closes that window before Qt ever paints
        # it.
        if outgoing_widget is not None and outgoing_widget is not self.widget():
            outgoing_widget.hide()

    def _restore_width_after_collapse(self) -> None:
        """Give back the width the panel had when it was collapsed. Releasing the
        collapsed width's clamp leaves the dock at the strip's 36 px; only an
        explicit resize makes it wide again."""
        if self._width_before_collapse is None:
            return
        if self.isFloating():
            width, self._width_before_collapse = self._width_before_collapse, None
            self.resize(width, self.height())
            return
        self._restore_tries_left = _RESTORE_WIDTH_MAX_STEPS
        QTimer.singleShot(0, self._apply_width_after_collapse)

    def _apply_width_after_collapse(self) -> None:
        """One step of giving the width back. `QMainWindow.resizeDocks` closes only part of the gap
        per call (measured: asked 357 from a 36 px strip, got 313; asked again, 357), so this repeats
        once per event-loop turn until the dock is within a pixel or `_RESTORE_WIDTH_MAX_STEPS` is
        used up (the width may be unreachable, e.g. the window shrank meanwhile). A bound-method slot
        is dropped by Qt if the dock is deleted first."""
        width = self._width_before_collapse
        window = self.parentWidget()
        if width is None or self._collapsed or not isinstance(window, QMainWindow):
            return  # collapsed again in the meantime: keep the width for the next expand
        if abs(self.width() - width) <= 1 or self._restore_tries_left <= 0:
            self._width_before_collapse = None
            return
        self._restore_tries_left -= 1
        window.resizeDocks([self], [width], Qt.Orientation.Horizontal)
        QTimer.singleShot(0, self._apply_width_after_collapse)

    def _build_collapsed_strip(self, theme) -> QWidget:
        """The entire collapsed dock is one big clickable area - deliberately
        not just an icon glued to the top, so there's no dead area to miss
        a click on in a 36px-wide strip. The chevron alone used to be the
        whole strip; a rotated title label underneath it (2026-09-27,
        maintainer request) makes a collapsed panel self-explanatory instead
        of reading as a stray arrow with no indication of what it expands."""
        strip = _ClickableStrip()
        strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        strip.setToolTip(f"Expand {self._title}.")
        strip.setCursor(Qt.CursorShape.PointingHandCursor)
        strip.setStyleSheet(
            f"_ClickableStrip {{ background: {theme.window_bg}; }}"
            f"_ClickableStrip:hover {{ background: {hex_to_rgba(theme.accent_blue, 0.14)}; }}"
        )
        strip.clicked.connect(lambda: self._set_collapsed(False))

        layout = QVBoxLayout(strip)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)

        icon_label = QLabel(strip)
        icon_label.setPixmap(_render_tabler_icon("chevron-right", theme.text_muted).pixmap(QSize(18, 18)))
        # Same click-passthrough reasoning as _VerticalLabel above.
        icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(_VerticalLabel(self._title, theme.text_muted, strip), 1, Qt.AlignmentFlag.AlignHCenter)

        return strip

    def _build_collapsed_title_bar(self, theme) -> QWidget:
        """A bare divider line, not the normal title bar - at 36px wide
        there's no room for a title label plus buttons, and the whole point
        of collapsing is to give the click-to-expand button (the dock's
        `widget()`, above) the full height to be clicked on."""
        bar = QWidget()
        bar.setFixedHeight(1)
        bar.setStyleSheet(f"background: {theme.toolbar_border};")
        return bar

    def _on_float_button_clicked(self) -> None:
        if not self.isFloating():
            self.setFloating(True)
            return
        # Already floating: the click toggles whether docking is armed
        # rather than changing floating state again (see
        # _on_top_level_changed for why docking starts disabled here).
        self._dock_armed = not self._dock_armed
        self.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
            if self._dock_armed
            else Qt.DockWidgetArea.NoDockWidgetArea
        )
        self._update_float_button_appearance()

    def _on_top_level_changed(self, floating: bool) -> None:
        # Disable docking the moment the panel becomes floating - regardless
        # of whether that happened via the float button or the user dragging
        # the title bar out - so moving the floating window around/over the
        # main app doesn't trigger Qt's dock-target hint overlay or
        # accidentally snap it back in. Re-docking always restores all
        # areas.
        self._dock_armed = False
        self.setAllowedAreas(
            Qt.DockWidgetArea.NoDockWidgetArea if floating else Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self._apply_frame_style(get_active_theme())
        if self._collapsed:
            # The collapsed title bar (_build_collapsed_title_bar) has none
            # of the buttons the lines below reach for - Qt can still float
            # a collapsed panel via its native drag-the-title-bar gesture
            # (the collapsed title bar is a real, if 1px-tall, title bar
            # area), so this has to be reachable while collapsed rather
            # than assumed impossible.
            return
        self._update_float_button_appearance()
        # Maximizing only makes sense while floating - hide the button once
        # docked, and forget any stale "maximized"/pre-maximize geometry so
        # a later re-float starts fresh.
        self._maximize_button.setVisible(floating)
        if not floating:
            self._maximized = False
            self._pre_maximize_geometry = None
            self._update_maximize_button_appearance()

    def _on_maximize_button_clicked(self) -> None:
        if not self.isFloating():
            return
        if self._maximized:
            self._maximized = False
            if self._pre_maximize_geometry is not None:
                self.setGeometry(self._pre_maximize_geometry)
            self._pre_maximize_geometry = None
        else:
            self._pre_maximize_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.availableGeometry())
            self._maximized = True
        self._update_maximize_button_appearance()

    def _update_maximize_button_appearance(self) -> None:
        self._maximize_button.setIcon(self._make_maximize_icon(self._maximized))
        self._maximize_button.setToolTip(
            "Restore this panel to its previous size." if self._maximized else "Maximize this panel to fill the screen."
        )

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
    def _make_close_icon(color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("x", color)

    @staticmethod
    def _make_float_icon(color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("external-link", color)

    @staticmethod
    def _make_maximize_icon(maximized: bool = False, color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("minimize" if maximized else "maximize", color)

    @staticmethod
    def _make_help_icon(theme=None) -> QIcon:
        theme = theme or get_active_theme()
        return _render_tabler_icon("info-circle", theme.text_primary)
