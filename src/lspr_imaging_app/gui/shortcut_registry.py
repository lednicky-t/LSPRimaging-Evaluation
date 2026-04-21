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
            "Ctrl+Shift+PageUp / Ctrl+Shift+PageDown: Previous / next time frame",
            "F1: Show this shortcut list",
        ),
    ),
    ShortcutSection(
        key="dataset",
        title="Dataset / image navigation",
        lines=(
            "Shift+PageUp / Shift+PageDown: Previous / next wavelength image",
            "Ctrl+Shift+PageUp / Ctrl+Shift+PageDown: Previous / next time frame",
        ),
    ),
    ShortcutSection(
        key="spot_editor",
        title="Spot editor",
        lines=(
            "Ctrl+E: Toggle manual edit mode",
            "Ctrl+Shift+A: Toggle Add mode",
            "Ctrl+Shift+M: Toggle Move mode",
            "Ctrl+Shift+G: Group selected spots",
            "Delete / Backspace: Remove selected spots",
            "Left-click: Select a spot",
            "Shift+Left-click: Add a spot to the selection",
            "Double-left-click outside a spot: Clear the selection",
            "Left-drag: Draw a selection box",
            "Right-drag: Move selected spots when Move is active",
            "Middle-drag: Pan the image view",
            "Arrow keys: Move selected spots while Move is active",
            "Ctrl+Arrow: Move selected spots faster",
            "Shift+Arrow: Select neighboring spot in the array",
        ),
    ),
    ShortcutSection(
        key="array_detect",
        title="Array detect",
        lines=(
            "Ctrl+L: Show or hide the spot list table",
            "Matrix: Reorder detected spots by image position",
        ),
    ),
    ShortcutSection(
        key="table",
        title="Spot table",
        lines=(
            "PageUp / PageDown: Move selected spot rows up or down and renumber their IDs",
            "Ctrl+C: Copy selected spot properties",
            "Ctrl+V: Paste spot properties to the selected row(s)",
            "Delete / Backspace: Remove selected spots",
            "Ctrl+Z: Undo the last edit",
            "Ctrl+Shift+Z / Ctrl+Y: Redo the last undone edit",
            "Double-click: Edit a spot name, color, or geometry cell",
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
