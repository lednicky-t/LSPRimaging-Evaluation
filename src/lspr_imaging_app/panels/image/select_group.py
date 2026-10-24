"""The "Select" group at the left end of the Mask and ROIs ribbon tabs (2026-10-23, maintainer request).

The area-selection picker (`general_group.AreaSelectionPicker`) used to sit in an always-visible "General"
group beside the tabs. It now appears as the first group of every tab whose tools it limits (Mask, ROIs; on
the View tab it is an icon of the "Image" group). Each tab has its own picker; they all read and set the one
`AreaSelectionModule` and `ActiveToolModule`, so the copies show the same pick.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ..ribbon_group import group_label_style, labeled_icon_group, ribbon_separator


class SelectGroup:
    """The caption and divider added to a tab, kept so the tab can restyle them on a theme switch."""

    def __init__(self, label: QLabel, separator: QFrame) -> None:
        self._label = label
        self._separator = separator

    def refresh_theme(self, border_color: str) -> None:
        self._label.setStyleSheet(group_label_style())
        self._separator.setStyleSheet(f"color: {border_color};")


def insert_select_group(tab: QWidget, picker: QWidget) -> SelectGroup:
    """Put ``picker`` in a captioned "Select" group, with a divider after it, at the left end of ``tab``'s row."""
    group, label = labeled_icon_group(tab, picker, "Select")
    separator = ribbon_separator(tab)
    layout = tab.layout()
    assert isinstance(layout, QHBoxLayout)
    layout.insertWidget(0, group)
    layout.insertWidget(1, separator)
    return SelectGroup(label, separator)
