"""``CollapsibleSection`` - a titled, expand/collapse widget for grouping
settings inside a Workflow stage tab.

Ported from the stable app's ``gui/widgets.py:CollapsibleSection``
(2026-09-24, ``docs/rewrite_gui_shell_design_2026-09.md`` §4a), which uses
this exact pattern in production for the same purpose (a vertical accordion
of settings groups in its left panel). **One deliberate deviation: the pin
button is dropped.** Investigation found it vestigial in the source - every
section's pinned state is persisted and its icon toggles, but nothing reads
``is_pinned()`` anywhere; there is no real accordion/auto-collapse behavior
left in that app to pin *against* (see the design doc §4a for the full
finding). Not porting it removes dead UI, not real function.

Everything else carries over: the expand/collapse chevron (real), an
optional **apply** toggle (meant to mirror whether a section's setting is
actually applied to processing - not wired to any real state yet since no
stage has real controls ported in today, see ``panel.py``), an optional
**help** button (a static popup), and an optional ``header_extra`` widget
slot for compact inline indicators next to the title.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QToolButton, QVBoxLayout, QWidget

from lspr_ui import collapsible_toggle_stylesheet, get_active_theme, transparent_icon_button_stylesheet

from ..dock_container import _TITLE_BAR_ICON_HOVER, _render_tabler_icon


class CollapsibleSection(QWidget):
    expanded_changed = pyqtSignal(bool)
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
        title_color: str | None = None,
        header_extra: QWidget | None = None,
        show_help: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title_color = title_color
        self._toggle = QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setAutoRaise(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.NoArrow)
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        self._toggle.setIconSize(QSize(12, 12))
        self._apply_toggle_style()
        self._toggle.toggled.connect(self._set_expanded)

        self._apply_button: QToolButton | None = None
        if applied is not None:
            self._apply_button = QToolButton(self)
            self._apply_button.setCheckable(True)
            self._apply_button.setChecked(bool(applied))
            self._apply_button.setAutoRaise(True)
            self._apply_button.setIcon(self._make_apply_icon(bool(applied)))
            self._apply_button.setIconSize(QSize(18, 18))
            self._apply_button.setFixedSize(22, 22)
            self._apply_button.setToolTip(apply_tooltip or "Toggle whether this section's setting is applied.")
            self._apply_button.setStyleSheet(transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER))
            self._apply_button.toggled.connect(self._set_applied)

        self._help_button: QToolButton | None = None
        if show_help:
            self._help_button = QToolButton(self)
            self._help_button.setAutoRaise(True)
            self._help_button.setCheckable(False)
            self._help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self._help_button.setIcon(_render_tabler_icon("info-circle", get_active_theme().text_primary))
            self._help_button.setIconSize(QSize(16, 16))
            self._help_button.setFixedSize(22, 22)
            self._help_button.setToolTip("Show section help.")
            self._help_button.setStyleSheet(transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER))
            help_message = help_text or "No help written for this section yet."
            self._help_button.clicked.connect(lambda *_: QMessageBox.information(self, title, help_message))

        self._content = content
        self._content.setParent(self)
        self._content.setVisible(expanded)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(self._toggle, 0)
        if header_extra is not None:
            header_extra.setParent(header)
            header_layout.addWidget(header_extra, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)
        right_controls = QWidget(header)
        right_layout = QHBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        if self._help_button is not None:
            right_layout.addWidget(self._help_button, 0, Qt.AlignmentFlag.AlignVCenter)
        if self._apply_button is not None:
            right_layout.addWidget(self._apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
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

    def _set_applied(self, applied: bool) -> None:
        if self._apply_button is None:
            return
        self._apply_button.setIcon(self._make_apply_icon(applied))
        self.apply_changed.emit(applied)

    def _apply_toggle_style(self) -> None:
        stylesheet = collapsible_toggle_stylesheet()
        if self._title_color is not None:
            stylesheet += (
                f"\nQToolButton {{ color: {self._title_color}; }}"
                f"\nQToolButton:checked {{ color: {self._title_color}; }}"
            )
        self._toggle.setStyleSheet(stylesheet)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)

    def has_apply_toggle(self) -> bool:
        return self._apply_button is not None

    def is_applied(self) -> bool:
        return False if self._apply_button is None else self._apply_button.isChecked()

    def set_applied(self, applied: bool) -> None:
        if self._apply_button is not None:
            self._apply_button.setChecked(bool(applied))

    def refresh_theme(self) -> None:
        """Re-applies theme-dependent styling - call on every
        ``CollapsibleSection`` after a live theme switch, matching
        ``PanelContainer.refresh_theme``'s role for docks (neither picks up
        a switch on its own; both bake per-widget stylesheets in at
        construction time)."""
        self._apply_toggle_style()
        theme = get_active_theme()
        if self._help_button is not None:
            self._help_button.setIcon(_render_tabler_icon("info-circle", theme.text_primary))
            self._help_button.setStyleSheet(transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER))
        if self._apply_button is not None:
            self._apply_button.setIcon(self._make_apply_icon(self._apply_button.isChecked()))
            self._apply_button.setStyleSheet(transparent_icon_button_stylesheet(hover=_TITLE_BAR_ICON_HOVER))

    @staticmethod
    def _make_chevron_icon(expanded: bool) -> QIcon:
        name = "chevron-down" if expanded else "chevron-right"
        return _render_tabler_icon(name, get_active_theme().text_muted)

    @staticmethod
    def _make_apply_icon(applied: bool) -> QIcon:
        theme = get_active_theme()
        color = theme.accent_green if applied else theme.accent_red
        return _render_tabler_icon("link" if applied else "link-off", color)
