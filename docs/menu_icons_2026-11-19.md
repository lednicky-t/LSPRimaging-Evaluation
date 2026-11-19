# Icons in the menus, Ctrl+G / Ctrl+Shift+G, no "Rectangle size...", entries left out when they cannot be used (2026-11-19)

Maintainer's request: the right-click menus (Image panel, ROI table, "and everywhere") had no icons next to their entries; add as many as possible and reuse the same icon for
the same option. The Image panel's "Rectangle size..." entry can go. Group and Ungroup get Ctrl+G and Ctrl+Shift+G.

## One table: `panels/menu_icons.py`

A menu names **what an entry does** (`give_icon(action, "delete")`), never an icon file. `ICONS` maps each command to a vendored Tabler icon (or to an icon the app already
draws: the sample disc, the reference ring, the plan bar's step icons), so Delete is the same bin in the ROI menu, the table's menu, a mask's menu and a plan step's menu.
The commands that have a ribbon button (Group, Ungroup, Link, Unlink, Lock, Colour, the Move arrows, Delete) wear the button's icon.

- `give_icon(target, key)`: for a menu built when it pops up (every right-click menu): it reads the theme's colour then. `target` is a `QAction`, or a `QMenu` for a submenu.
- `give_persistent_icon(target, key)`: for an entry that lives as long as the window (the menu bar, the ROI table's header menu, the plot export button's menu). It is remembered
  (weakly) and `refresh_menu_icons()` redraws it when the theme switches (`app_rewrite._wire_theme_menu`).
- An unknown key logs a warning and gives no icon (a menu that fails to open is worse than one without an icon). `tests/integration/test_lspri_menu_icons.py` reads every key written
  in the source (with `ast`) and the three small tables (`mask_rows.ENTRY_ICONS`, `interaction._TOOL_MENU_ICONS`, `menu_icons.PANEL_ICON_KEYS`), so a typo is a test failure.
- **A new menu that offers an existing command uses its key. A new command adds one line to `ICONS`.** A new Tabler icon is vendored first (`packages/lspr_ui/ICONS.md`).

Vendored for this (all plain Tabler outline files, nothing else changed in `lspr_ui`): `clipboard` (paste), `arrow-back-up` / `arrow-forward-up` (undo / redo), `droplet-off`
(clear / default colour), `snowflake-off` (unfreeze), `arrow-autofit-width` (fit columns), `keyboard`, `terminal-2` (log console), `door-exit` (exit), `table`, `wave-sine`,
`chart-line` (the ROI / Groups, Spectra and Sensorgram panel entries of the View menu).

## What got icons

- **Image panel**: the ROI menu (every entry and submenu: Add ROI > Circle / Rectangle / Current selection, Make this ROI > Sample / Reference, Link / Unlink, Group, Add to group,
  Ungroup, Remove this cube's edit, Delete, Deselect, Lock selection); the tools' menus (Cancel rotation / measurement / crop, Apply crop, Exit tool, Select ROIs in selection,
  Lock / Unlock, Invert, Deselect); the Mask rows' menus (Go to, Mask the particle events, Dismiss / Restore, Delete, Make individual / persistent); the ROI layout row's menu; the slider's
  Go to reference.
- **ROI / Groups table**: the row menu (Copy / Paste / Cancel cells, Group, Add to group, Remove from group, Set / Clear colour, Reset diameters, Shift position, Move, Delete), the group
  header menu (Expand, Collapse, Rename, Colour, Move group, Ungroup, Delete), the empty-area menu, the header menu (views, Hide / Show columns, Freeze / Unfreeze, Fit step columns), a
  comparison column's Remove.
- **Sensorgram**: the plan step menu (Edit text, Colour, Default colour, Reset, Remove / Add / Unify / Restore steps with the plan bar's own step icons, Use as, Use for S_bulk). The anchor
  menus already had icons (`baseline_controls.py`).
- **Menu bar and export button**: File (all entries), Edit (Undo, Redo, Preferences), View (Show / Hide all, the five panels, Panel Presets, Theme), Help; the plot export button's menu
  (Save figure, Copy figure, Save data).
- **Not touched**: the drop-down pick-ups that already draw their own pictures (selection mode, ROI display, Spectra / Sensorgram View menus, the canvas tool variants) and the radio entries
  of the Panel Presets and the Session list (a check mark and an icon would both claim the same column).

## Ctrl+G / Ctrl+Shift+G

Rows `groups.group` / `groups.ungroup` in `shortcuts.py` (section ROIs). `GroupActions` (`panels/image/group_actions.py`) owns two `QAction`s with `ApplicationShortcut` context, run the same
commands as the Group / Ungroup buttons, so they work from the ROI table too (the ROI selection is shared; a selected group header selects its members). The actions live on the **Image
panel** (`action_parent`), not on the ribbon button: a ribbon tab that is not the open one is hidden, and a hidden widget's shortcut does not fire. Ctrl+Shift+G with nothing grouped says so in the
status bar. The ROI menu and the table's row menu show the keys; the two buttons' tool tips too.

Known limit (same as Ctrl+L / Ctrl+K): the keys work while the Image panel is visible. A closed Image dock means no Ctrl+G.

One small difference between the table's own Ungroup button and Ctrl+Shift+G: the button also deletes a selected group header that has **no** members; the key acts on selected ROIs only.

## "Rectangle size..." removed

The entry is gone from the ROI menu (`roi_context_menu.RESIZE_RECTANGLE`, `selected_rectangles`, and the branch in `interaction.py`). The ROI table's W / H / angle columns and the rectangle's handles do
that job. **Left in place, now unreachable from the UI**: `panels/image/rectangle_size_dialog.py` and `RoiCreation.resize_rectangles` / `rectangles_among`; the dialog still has a test. Delete them when the
maintainer says so.

## Also changed in passing

The right-click menus that are children of a panel (`QMenu(parent)`) were never deleted: one was left behind per right-click. The ROI menu, the table's menu, the Mask rows' and the layout row's menus now
call `deleteLater()` after use (their icons made the leak a little bigger). Unseen by eye.

## Entries are left out when their prerequisite is not met (second request, 2026-11-19)

Maintainer: "options are not available when they do not have their prerequisites satisfied (some ROI selected, a cell selected, ...)": Unlink only when a selected ROI is linked,
Deselect only with a selection, and the same for every context menu. **The rule: a context menu offers only what can be done now; an entry that cannot is not in the menu (it used
to be greyed). A menu with nothing in it does not open.** The state the entry needs is already computed by the caller (the old `setEnabled(...)` argument), so each rule is one `if`.

**The Image panel's ROI menu** (`panels/image/roi_context_menu.py`) is now in four blocks: the **selection commands together on top**: Add ROI, Delete N ROIs, Select whole group, Deselect N ROIs
(was "Deselect"), Lock ROI selection; then Make this ROI, Link references, Unlink; then Group, Add to group, Ungroup; last Remove this cube's edit. Rules: Add ROI only if a circle / rectangle may
be added here or there is an area selection to make ROIs from (its entries likewise); Delete / Deselect / Make this ROI / Group need a selected ROI; Link needs a sample and a reference not all
linked yet, **Unlink something linked in the selection** (`roi.link_actions.can_link` / `can_unlink`); Ungroup a grouped ROI; Add to group needs a group; Select whole group a clicked group with members
not yet selected; Deselect and Select whole group are also left out while the ROI selection is locked. Lock ROI selection is always there, so the menu is never empty.

Other menus: the tools' menu (`context_menu.show_tool_context_menu`: Apply crop only with a pending edit; Lock / Unlock / Select ROIs in selection / Invert / Deselect only when they apply;
`None` and no menu when nothing is left); the Mask rows' and ROI layout row's menus (the first cube has only Go to); the slider's Go to reference (no menu without a reference tick); the ROI table's row
menu (Copy / Paste / Cancel cells, every ROI command needs a selected ROI, Remove from group a grouped one, **Clear color one with a colour of its own**, Move an ordering that can be moved), its group menu
(Expand not in the Groups-only view, Rename for one group, only the moves that would change something, no Move for one group or while the groups are sorted by name), its header menu (Hide column on a
column's header, Show hidden / Show all only while a column is hidden, Freeze on an ROI column other than the frozen one); the plan step menu (Default colour, Reset, Reset all, Unify, Restore, Add step,
Use as each need what they act on); the anchor menus (Remove the nearest, Apply to all ROIs, Clear all, Make / Remove anchor step); the signal map's legend menu (Refresh, and a submenu whose drop-down is off).

**Deliberately unchanged**: the menu bar (File / Edit / View / Help keep the convention of greyed entries: Undo, Redo, Show Dataset Folder, ...), the slider's frame-tools menu (the same icon buttons as
the ribbon, where a greyed button is the convention), and the pick-up menus of the ribbon (they are settings, not commands). **A refusal that explains itself stays an entry**: Reset diameters and Shift position
stay in the table menu whenever a ROI is selected (the toolbox says why in the title bar when it refuses a pixel mask or a rectangle).

Cost of the rule: the tool tips that explained *why* an entry was greyed ("Cube 0 holds the start layout...", "No column is hidden") are gone with the entries.

**Deselect area (third request, 2026-11-19).** With no tool armed, a right-click on the ROIs tab opens the ROI menu, which had no way to drop the pixel (area) selection: only the armed tools' menus
(and, on other tabs, a right-click inside the selection) had "Deselect". The ROI menu now has **Deselect area** next to "Deselect N ROIs" whenever there is an area selection (`can_deselect_area`,
`AreaSelectionModule.has_selection()`); one undo step. Still missing from that menu, all in the armed tools' menu: Invert selection, Lock / Unlock, Select ROIs in selection. Off the ROIs tab with no
tool, the plain menu still opens only for a right-click inside the selection.
