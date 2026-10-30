"""Every keyboard shortcut and mouse gesture of the app, in one table (2026-10-30).

Design record: ``docs/keyboard_shortcut_registry_design_2026-10-30.md``. The old app kept a hand-typed list of key texts for its F1
dialog and bound the keys somewhere else, so the two could drift apart. Here the table **is** the binding:

- a menu action gets its keys with :func:`bind_action` (it never carries a key string of its own);
- a key handler asks :func:`key_matches` / :func:`matches` instead of comparing ``event.key()`` with a literal;
- tooltips and the Help -> Keyboard shortcuts dialog read :func:`display` / :func:`all_bindings`.

So a key that works is a key that is listed, and the other way round. ``tests/unit/test_lspri_shortcuts.py`` checks that no two
bindings of one scope share a key and that every id used in the source exists in the table (and every table id is used).

**Matching is exact**: ``Ctrl+Delete`` is not ``Delete``. Only the keypad and group-switch modifier flags are ignored. A handler
that never looked at the modifiers (Esc cancels a tool whatever is held) passes ``NoModifier`` explicitly.

**Mouse gestures cannot be bound here** (the widgets own their mouse events), so they are text entries (``keys=()`` and a
``gesture``). They are the one part nothing can check: a new gesture adds its line to this table (``CLAUDE.md``).

Only ``QKeySequence`` and ``Qt`` are used (no ``StandardKey`` lookup, which needs a running application), so this imports and
works without a window.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence

EVERYWHERE = "Everywhere"
"""Scope of a binding that works anywhere in the main window (a menu action). Such a key may not be used by any other binding."""


@dataclass(frozen=True)
class Binding:
    id: str
    section: str  # the group in the dialog
    label: str  # what it does, in the user's words
    keys: tuple[str, ...] = ()  # portable key text ("Ctrl+Shift+L"), one entry per alternative
    gesture: str = ""  # mouse text, for an entry with no keys ("Middle-drag")
    scope: str = EVERYWHERE  # where it works; shown as "Where" in the dialog


# Panels, in the order of the View menu (the dock titles are the keys: ``app_rewrite._build_dock_layout``).
PANEL_TOGGLE_IDS: dict[str, str] = {
    "Workflow": "panels.workflow",
    "Image": "panels.image",
    "Histogram": "panels.histogram",
    "ROI / Groups": "panels.roi_groups",
    "Spectra": "panels.spectra",
    "Sensorgram": "panels.sensorgram",
}
# Layout presets (``panels/layout_presets.py`` ``PRESET_NAMES``).
PRESET_IDS: dict[str, str] = {
    "Image Tools": "presets.image_tools",
    "ROI": "presets.roi",
    "Analysis": "presets.analysis",
    "Visualization": "presets.visualization",
}

_ROI_TABLE = "ROI table"
_TOOLS = "Image view, with a tool active"
_VIEW = "Image view"
_SLIDERS = "Wavelength and cube sliders"
_PLAN_EDITOR = "Sensorgram step text editor"

_ARROWS = ("Left", "Right", "Up", "Down")

BINDINGS: tuple[Binding, ...] = (
    # -- menu actions ----------------------------------------------------------------------------
    # Undo / Redo / Copy / Paste: the keys Qt's ``StandardKey`` gives on Windows, written out (a ``StandardKey`` lookup needs a running
    # application, and this table must import without one). Not carried over: the dedicated Undo / Redo / Copy / Paste hardware keys.
    Binding("edit.undo", "Edit", "Undo the last edit", keys=("Ctrl+Z", "Alt+Backspace")),
    Binding("edit.redo", "Edit", "Redo the last undone edit", keys=("Ctrl+Y", "Alt+Shift+Backspace", "Ctrl+Shift+Z")),
    Binding("edit.preferences", "Edit", "Open Preferences", keys=("Ctrl+,",)),
    Binding("help.log_console", "Help", "Show or hide the Log Console", keys=("Ctrl+Shift+L",)),
    Binding("help.shortcuts", "Help", "Show the keyboard shortcuts", keys=("F1",)),
    *(
        Binding(binding_id, "Panels", f"Show or hide the {title} panel", keys=(f"Ctrl+{number}",))
        for number, (title, binding_id) in enumerate(PANEL_TOGGLE_IDS.items(), start=1)
    ),
    *(
        Binding(binding_id, "Panel layouts", f"Apply the {name} layout", keys=(f"Ctrl+Shift+{number}",))
        for number, (name, binding_id) in enumerate(PRESET_IDS.items(), start=1)
    ),
    # -- ROI table -------------------------------------------------------------------------------
    Binding("roi_table.copy", "ROI table", "Copy the value of the current cell", keys=("Ctrl+C", "Ctrl+Ins"), scope=_ROI_TABLE),
    Binding(
        "roi_table.paste", "ROI table", "Paste the copied value into the current column of the selected ROIs (x, y, diameter)",
        keys=("Ctrl+V", "Shift+Ins"), scope=_ROI_TABLE,
    ),
    Binding(
        "roi_table.fill_down", "ROI table", "Fill the top selected row's value down the other selected rows (x, y, diameter)",
        keys=("Ctrl+D",), scope=_ROI_TABLE,
    ),
    Binding("roi_table.delete", "ROI table", "Delete the selected ROIs", keys=("Delete", "Backspace"), scope=_ROI_TABLE),
    Binding(
        "roi_table.move", "ROI table", "Move the selected ROIs (or groups) up / down; they are renumbered",
        keys=("Alt+Up", "Alt+Down"), scope=_ROI_TABLE,
    ),
    Binding(
        "roi_table.cell_step", "ROI table", "While editing a number: step it up / down", keys=("Up", "Down"), scope=_ROI_TABLE,
    ),
    Binding(
        "roi_table.cell_step_large", "ROI table", "While editing a number: step it up / down in large steps",
        keys=("Shift+Up", "Shift+Down"), scope=_ROI_TABLE,
    ),
    # -- tools of the Image view -----------------------------------------------------------------
    Binding("tools.cancel", "Image tools", "Cancel what the Rotate, Crop or Measure tool is doing", keys=("Esc",), scope=_TOOLS),
    Binding(
        "tools.rotate_nudge", "Image tools", "Rotate tool: turn the image by 0.1 degree (Left / Down: back, Right / Up: forward)",
        keys=_ARROWS, scope=_TOOLS,
    ),
    Binding(
        "tools.rotate_nudge_medium", "Image tools", "Rotate tool: turn the image by 1 degree",
        keys=tuple(f"Ctrl+{arrow}" for arrow in _ARROWS), scope=_TOOLS,
    ),
    Binding(
        "tools.rotate_nudge_coarse", "Image tools", "Rotate tool: turn the image by 5 degrees",
        keys=tuple(f"Shift+{arrow}" for arrow in _ARROWS), scope=_TOOLS,
    ),
    # -- Sensorgram ------------------------------------------------------------------------------
    Binding("plan_text.cancel", "Sensorgram", "Close the step text editor without saving", keys=("Esc",), scope=_PLAN_EDITOR),
    # -- mouse gestures: text only (see the module docstring) ------------------------------------
    Binding("mouse.view.select", "Mouse: image view", "Select the ROI under the pointer", gesture="Click", scope=_VIEW),
    Binding(
        "mouse.view.select_more", "Mouse: image view", "Add the ROI under the pointer to the selection",
        gesture="Ctrl+click or Shift+click", scope=_VIEW,
    ),
    Binding(
        "mouse.view.menu", "Mouse: image view", "Open the menu of the ROIs tab, or of the tool that is active",
        gesture="Right-click", scope=_VIEW,
    ),
    Binding("mouse.view.zoom", "Mouse: image view", "Zoom the image", gesture="Wheel", scope=_VIEW),
    Binding("mouse.view.zoom_fine", "Mouse: image view", "Zoom the image in finer steps", gesture="Ctrl+wheel", scope=_VIEW),
    Binding("mouse.view.pan", "Mouse: image view", "Pan the image", gesture="Middle-drag", scope=_VIEW),
    Binding("mouse.slider.set", "Mouse: sliders", "Set the value", gesture="Click or drag", scope=_SLIDERS),
    Binding(
        "mouse.slider.step", "Mouse: sliders", "Step the value; one more turn at the end goes on past that end", gesture="Wheel",
        scope=_SLIDERS,
    ),
    Binding("mouse.slider.zoom", "Mouse: sliders", "Zoom the axis", gesture="Ctrl+wheel", scope=_SLIDERS),
    Binding("mouse.slider.pan_wheel", "Mouse: sliders", "Pan the zoomed axis", gesture="Shift+wheel or side wheel", scope=_SLIDERS),
    Binding("mouse.slider.pan", "Mouse: sliders", "Pan the zoomed axis", gesture="Middle-drag", scope=_SLIDERS),
    Binding("mouse.slider.reset", "Mouse: sliders", "Show the whole axis again", gesture="Middle double-click", scope=_SLIDERS),
    Binding("mouse.slider.marks", "Mouse: sliders", "Cube strip: hop to the previous / next mark", gesture="Wheel", scope="Cube strip"),
    Binding("mouse.slider.mark_menu", "Mouse: sliders", "Cube strip: open the menu of a mark", gesture="Right-click on a mark", scope="Cube strip"),
    Binding("mouse.play_speed", "Mouse: sliders", "Change the slideshow speed", gesture="Wheel on the speed button", scope="Slideshow controls"),
)

_BY_ID: dict[str, Binding] = {binding.id: binding for binding in BINDINGS}
if len(_BY_ID) != len(BINDINGS):  # a duplicated id would silently hide one binding
    raise RuntimeError("shortcuts.BINDINGS has a duplicated id")

_IGNORED_MODIFIERS = Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.GroupSwitchModifier


def binding(binding_id: str) -> Binding:
    try:
        return _BY_ID[binding_id]
    except KeyError:
        raise KeyError(f"No shortcut with the id {binding_id!r}: add it to lspr_imaging_app/shortcuts.py") from None


def all_bindings() -> tuple[Binding, ...]:
    return BINDINGS


def sections() -> tuple[str, ...]:
    """The sections in table order, each once."""
    return tuple(dict.fromkeys(item.section for item in BINDINGS))


def sequences(binding_id: str) -> tuple[QKeySequence, ...]:
    """The key sequences of a binding, in table order."""
    return tuple(QKeySequence(text) for text in binding(binding_id).keys)


def display(binding_id: str) -> str:
    """What to show for the keys: ``"Ctrl+Y or Ctrl+Shift+Z"``, or the mouse gesture for an entry with no keys."""
    item = binding(binding_id)
    texts = [sequence.toString(QKeySequence.SequenceFormat.NativeText) for sequence in sequences(binding_id)]
    return " or ".join(texts) if texts else item.gesture


def bind_action(action: QAction, binding_id: str, *, context: Qt.ShortcutContext | None = None) -> None:
    """Give a menu action the keys of ``binding_id``. ``context``: ``ApplicationShortcut`` for a key that must also work from a
    floating panel (a panel outside the main window is its own top-level window); None keeps Qt's default."""
    action.setShortcuts(list(sequences(binding_id)))
    if context is not None:
        action.setShortcutContext(context)


def key_matches(key: int, modifiers: Qt.KeyboardModifier, binding_id: str) -> bool:
    """Whether ``key`` with exactly ``modifiers`` is one of the keys of ``binding_id`` (keypad / group-switch flags ignored)."""
    held = modifiers & ~_IGNORED_MODIFIERS
    for sequence in sequences(binding_id):
        if sequence.count() == 1:
            combination = sequence[0]
            if int(combination.key()) == int(key) and combination.keyboardModifiers() == held:
                return True
    return False


def matches(event, binding_id: str) -> bool:
    """:func:`key_matches` for a ``QKeyEvent``."""
    return key_matches(event.key(), event.modifiers(), binding_id)


def find_conflicts(bindings: Iterable[Binding] | None = None) -> list[tuple[str, str, str]]:
    """Pairs ``(key text, id, id)`` that would fight for one key: two bindings of the same scope, or one of scope
    ``EVERYWHERE`` with any other (a menu shortcut takes the key before a widget sees it). ``bindings``: the table by default."""
    seen: dict[str, list[Binding]] = {}
    for item in BINDINGS if bindings is None else bindings:
        for text in item.keys:
            seen.setdefault(QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText), []).append(item)
    conflicts: list[tuple[str, str, str]] = []
    for text, holders in seen.items():
        for index, first in enumerate(holders):
            for second in holders[index + 1:]:
                if first.scope == second.scope or EVERYWHERE in (first.scope, second.scope):
                    conflicts.append((text, first.id, second.id))
    return conflicts


def shortcuts_as_text() -> str:
    """The whole table as plain text, grouped by section (the dialog's "Copy as text")."""
    lines: list[str] = []
    for section in sections():
        lines.append(section)
        for item in BINDINGS:
            if item.section == section:
                where = "" if item.scope == EVERYWHERE else f" ({item.scope})"
                lines.append(f"  {display(item.id)}: {item.label}{where}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
