from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShortcutSection:
    key: str
    title: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join((self.title, *self.lines))


SHORTCUT_SECTIONS: tuple[ShortcutSection, ...] = (
    ShortcutSection(
        key="main",
        title="Main shortcuts",
        lines=(
            "Ctrl+Z: Undo last recorded edit",
            "Ctrl+Shift+Z / Ctrl+Y: Redo the last undone edit",
            "Shift+PageUp / Shift+PageDown: Previous / next wavelength image",
            "Ctrl+Shift+PageUp / Ctrl+Shift+PageDown: Previous / next spectral cube",
            "F1: Show this shortcut list",
        ),
    ),
    ShortcutSection(
        key="dataset",
        title="Dataset / image navigation",
        lines=(
            "Shift+PageUp / Shift+PageDown: Previous / next wavelength image",
            "Ctrl+Shift+PageUp / Ctrl+Shift+PageDown: Previous / next spectral cube",
        ),
    ),
    ShortcutSection(
        key="roi_editor",
        title="ROI editor",
        lines=(
            "Ctrl+E: Toggle ROI edit mode",
            "Ctrl+Shift+A: Toggle Add mode",
            "Ctrl+Shift+M: Toggle Move mode",
            "Ctrl+G: Group selected ROIs",
            "Ctrl+Shift+G: Ungroup selected ROIs",
            "Delete / Backspace: Remove selected ROIs",
            "Left-click: Select an ROI",
            "Shift+Left-click: Add an ROI to the selection",
            "Double-left-click outside an ROI: Clear the selection",
            "Left-drag: Draw a selection box",
            "Right-drag: Move selected ROIs when Move is active",
            "Middle-drag: Pan the image view",
            "Arrow keys: Move selected ROIs while Move is active",
            "Ctrl+Arrow: Move selected ROIs faster",
            "Shift+Arrow: Select neighboring ROI in the array",
        ),
    ),
    ShortcutSection(
        key="array_detect",
        title="Array detect",
        lines=(
            "Ctrl+L: Show or hide the ROI table",
            "Matrix: Reorder detected ROIs by image position",
        ),
    ),
    ShortcutSection(
        key="table",
        title="ROI table",
        lines=(
            "PageUp / PageDown: Move selected ROI rows up or down and renumber their IDs",
            "Ctrl+C: Copy selected ROI properties",
            "Ctrl+V: Paste ROI properties to the selected row(s)",
            "Ctrl+G: Group selected ROIs",
            "Ctrl+Shift+G: Ungroup selected ROIs",
            "Delete / Backspace: Remove selected ROIs",
            "Ctrl+Z: Undo the last edit",
            "Ctrl+Shift+Z / Ctrl+Y: Redo the last undone edit",
            "Double-click: Edit an ROI name, color, or geometry cell",
        ),
    ),
    ShortcutSection(
        key="chromatic",
        title="Chromatic reference-point editing",
        lines=(
            "PageUp / PageDown: Switch reference point",
            "Shift+PageUp / Shift+PageDown: Previous / next sampled wavelength image",
            "Arrow keys: Move selected reference point",
            "Ctrl+Arrow: Move reference point faster",
        ),
    ),
    ShortcutSection(
        key="measure",
        title="Measurement tool",
        lines=(
            "Ctrl+Shift+R: Toggle Measure",
            "Arrow keys: Adjust rotation",
            "Ctrl+Arrow: 1 degree step",
            "Shift+Arrow: 5 degree step",
        ),
    ),
)


def shortcut_sections_by_key() -> dict[str, ShortcutSection]:
    return {section.key: section for section in SHORTCUT_SECTIONS}


def shortcuts_text() -> str:
    return "\n\n".join(section.text for section in SHORTCUT_SECTIONS)


def shortcuts_text_for_panel(panel_key: str) -> str:
    sections = shortcut_sections_by_key()
    section = sections.get(panel_key, sections["main"])
    return section.text


def panel_help_text(title: str, description: str, panel_key: str) -> str:
    return (
        f"{title}\n"
        f"What it does\n"
        f"{description}\n\n"
        f"Shortcuts\n"
        f"{shortcuts_text_for_panel(panel_key)}"
    )
