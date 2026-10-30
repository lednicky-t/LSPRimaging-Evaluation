# Keyboard shortcut registry: one place for every key, and the list the user reads

Status: **built 2026-10-30** (maintainer: "go", with the recommended answers to the four decisions in section 7). Section 8 lists where the build differs from this proposal.

## 1. What the maintainer asked for

Cut-over decision (2026-10-29): drop the old app's shortcut dialog, but keep "the idea and implementation to have all in one place".

## 2. What the old app did, and why it drifted

In tag `legacy-final`: `gui/shortcut_registry.py` (125 lines) was a hand-written list of text lines ("Ctrl+Z: Undo last recorded edit"), shown by F1. The keys themselves were bound somewhere else (`shortcut_manager.py`, `QAction.setShortcut` calls, key handlers). So the text and the binding were two copies of one fact; nothing made them agree, so any change to one could silently leave the other behind. The idea worth keeping is the single list; the implementation to change is that **the list must be the binding**.

## 3. What the rewrite binds today (inventory, 2026-10-30)

| Where | Keys | Code |
|---|---|---|
| Edit menu | Ctrl+Z; Ctrl+Y / Ctrl+Shift+Z | `app_rewrite._wire_edit_menu` (`QKeySequence.StandardKey`) |
| Help menu | Ctrl+Shift+L (Log Console) | `app_rewrite.py` |
| Edit menu | Ctrl+, (Preferences) | `app_rewrite.py` |
| View menu | Ctrl+1 ... Ctrl+6 (show / hide a panel, by dock order) | `panels/panel_visibility.py` `_PANEL_SHORTCUTS` |
| View menu | Ctrl+Shift+1 ... Ctrl+Shift+N (layout presets) | `panels/layout_presets.py` |
| ROI table | Ctrl+C, Ctrl+V, Ctrl+D (fill down), Delete / Backspace, Alt+Up / Alt+Down (move), Up / Down in an edited cell (Shift: large step) | `panels/roi_table/view.py`, `delegate.py` |
| Image tools | Esc (cancel the rotate, crop or measure tool); arrows nudge the rotate line (Left / Down -, Right / Up +) | `panels/image/rotate_line_tool.py`, `crop_tool.py`, `measure_line_tool.py`, routed by an event filter in `panels/image/panel.py` |
| Sensorgram plan editor | Esc (close the step text editor) | `panels/sensorgram/plan_text_editor.py` |
| Mouse (not keys) | wheel on the wavelength / cube sliders, middle-drag pans a slider axis, middle double-click shows the whole axis, right-click menus on ROIs / the image / the plan bar, middle-drag pans the image | `panels/image/data_axis_slider.py`, `timeline_strip.py`, `interaction.py`, `image_controls.py`, `slideshow_ribbon_controls.py` |

About 25 keys in 8 places, each written as a literal string next to the code that reacts.

## 4. Proposed design

**One module, `shortcuts.py`** (top level of `lspr_imaging_app`; only `PyQt6.QtGui.QKeySequence` and the standard library, so it imports without a window). It holds a table of `Binding(id, section, label, keys, scope)`:

```text
Binding("edit.undo",        "Edit",   "Undo the last edit",           ("Ctrl+Z",),                "Application")
Binding("edit.redo",        "Edit",   "Redo",                         ("Ctrl+Y", "Ctrl+Shift+Z"), "Application")
Binding("roi_table.fill_down", "ROI table", "Fill the cell down",     ("Ctrl+D",),                "ROI table")
Binding("mouse.slider.pan", "Mouse",  "Pan the slider axis",          (),  gesture="Middle-drag", scope="Sliders")
```

Three functions are the whole interface:

- `bind_action(action, "edit.undo")`: sets the action's shortcuts from the table. A menu action never carries a key of its own.
- `matches(event, "roi_table.fill_down")`: for a key handler. The handler asks the table instead of comparing `event.key()` with a literal.
- `keys_text("edit.undo")`: for tooltips and menu hints ("Undo (Ctrl+Z)").

The dialog (section 5) reads the same table, so **a key that works is a key that is listed, and the other way round**. Mouse gestures cannot be bound by the table (the widgets own their mouse events), so they are *text entries*: they live in the same file, one line each, so the list stays in one place, but they are the part that can still drift. A test cannot catch that; the code review rule is "a new gesture adds its line to `shortcuts.py`" (one sentence in `CLAUDE.md`).

**Tests** (pure, no window):
1. No two bindings of the same scope share a key (a real conflict check the old app never had; today Ctrl+D, Ctrl+C, Ctrl+V are safe only because nobody has looked).
2. Every `bind_action("...")` / `matches(..., "...")` / `keys_text("...")` id in the source exists in the table, and every table id with keys is used by some code (found by a source scan, like `tools/import_reachability.py` does for imports).
3. The dialog lists every binding (counts rows).

## 5. The new screen (GUI rules, `.claude/rules/gui.md`)

```text
Screen purpose:   Show every keyboard shortcut and mouse gesture of the app in one searchable list. Read only.
Main user actions:
  - Open it: Help -> Keyboard shortcuts, or F1.
  - Type in the search box to narrow the list (matches the action text, the keys and the section).
  - Copy the whole list as plain text (for notes or a bug report).
  - Close it (Esc, the Close button, or F1 again).
Layout (a small non-modal window, 560 x 520 px, resizable):
  - Top: search field ("Search actions or keys"), focus on open.
  - Left panel: none.
  - Center panel: a two-level tree. Level 1 = section (Edit, Panels, Layout presets, ROI table, Image tools,
    Sensorgram, Mouse). Level 2 = one row per binding: Action | Keys (e.g. "Ctrl+Y or Ctrl+Shift+Z") | Where
    ("Everywhere", "ROI table", "Image view while a tool is active"). Sections start expanded; a search expands
    what it finds and hides the rest.
  - Right panel: none.
  - Bottom: "Copy as text" (left), "Close" (right). No status line.
Important states:
  - Empty search result: one muted line "No shortcut matches 'xyz'." in place of the tree.
  - Key text uses the platform's names (Ctrl on Windows and Linux).
  - A binding with no key (a mouse gesture) shows its gesture text in the Keys column.
  - The dialog remembers its size and position and the expanded sections (UI-state rule, root `CLAUDE.md`);
    the search text is NOT remembered.
  - Theme: the app's themed palette (no hard-coded colours).
Validation rules: none (nothing is editable).
Error messages: none. If a binding in the table is malformed (a test catches this), the row shows the raw text.
```

Not in scope: changing a key (rebinding). The table makes it possible later (the user's choice would override `keys` per id and be saved in `ui_state`); the maintainer has not asked for it.

## 6. Work plan (one commit, eva repo)

1. `shortcuts.py` with the table and the three functions; unit tests (section 4).
2. Migrate the 8 places in section 3 to `bind_action` / `matches` / `keys_text` (behaviour must not change; each place keeps its existing test).
3. `panels/shortcuts_dialog.py` and the Help-menu entry (F1); one integration test that opens it, searches, copies.
4. `CLAUDE.md`: the rule "a new key or gesture is added to `shortcuts.py` first".

Size: about 150 lines for the table and functions, 150 for the dialog, 40 changed lines in the 8 places. Risk: low (no scientific code); the one thing to watch is **the Undo / Redo keys**, which today use Qt's platform standard keys (Ctrl+Y on Windows, Ctrl+Shift+Z on macOS); the table fixes them to Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z everywhere, which is what the app already does through its extra Ctrl+Shift+Z binding. The panel toggles (Ctrl+1 ... Ctrl+6) are currently tied to the dock order; the table names each dock explicitly, so reordering docks no longer renumbers shortcuts silently (a behaviour change worth knowing: the keys stay as they are today).

## 7. Decisions needed from the maintainer

1. **Include mouse gestures in the list?** Recommended: yes, as text entries (the list you open is complete); the cost is that they are the one part nothing can check. Alternative: keys only.
2. **F1 for the dialog?** Recommended: F1 plus the Help menu entry (the old app used F1).
3. **Migrate all 8 places in one go?** Recommended: yes (a half-migrated table would be the old drift again).
4. **Rebinding by the user?** Recommended: not now.

## 8. What was built, and where it differs from the proposal

Files: `src/lspr_imaging_app/shortcuts.py` (the table and its functions), `panels/shortcuts_dialog.py` (the dialog), `app_rewrite._wire_shortcuts_dialog` (Help -> Keyboard shortcuts, F1: a second press closes it). The 8 places of section 3 were migrated; tests: `tests/unit/test_lspri_shortcuts.py` (conflicts, the source and the table agree, exact matching), `tests/integration/test_lspri_shortcuts_dialog.py` (the dialog, and the real menus' keys).

Differences:
- **Undo / Redo / Copy / Paste keys are written out, not read from Qt's `StandardKey`.** A `StandardKey` lookup needs a running application (it crashed the table's own import in a unit test), and the table must work without one. On Windows they are Ctrl+Z or Alt+Backspace, Ctrl+Y or Alt+Shift+Backspace or Ctrl+Shift+Z, Ctrl+C or Ctrl+Ins, Ctrl+V or Shift+Ins: the same keys as before. Dropped: the dedicated Undo / Redo / Copy / Paste *hardware* keys Qt also lists.
- **Matching is exact, so a few unusual combinations no longer do what they did**, because a key that works must be a key that is listed: Shift / Ctrl / Alt + Delete or Backspace in the ROI table no longer deletes; Alt+Shift+Up / Down no longer moves a ROI row; Ctrl+Up / Down in an edited cell no longer steps the number (plain and Shift do); in the Rotate tool an arrow with Alt held still nudges by 0.1 degree, Shift still wins over Ctrl. Esc still cancels a tool and closes the step editor whatever else is held (those handlers never looked at the modifiers).
- **The mouse list covers the image view and the sliders only** (verified against the handlers on 2026-10-30). Gestures elsewhere (the Histogram plot's right-click menu and axis double-click, a dock's title bar, the ROI table's mouse use, the plan bar) are not listed yet: each adds its line to `shortcuts.py` when it is next touched.
- **Dialog size** is 760 x 540 (the long action texts need the width); the position is not remembered, only the size and the collapsed sections.
- **Binding id** of the step editor's Esc is `plan_text.cancel` (a `sensorgram.` prefix collided with the Sensorgram's log-event names in the test that scans the source for ids).
