# LSPRi Evaluation rewrite — GUI shell design (window chrome, panel presets, theming)

Dated design record, sibling to `rewrite_architecture_sketch_2026-09.md` (the
module/data architecture) and `rewrite_build_log_2026-09.md` (what's actually
been built). That sketch's §7 "Workflow shell" and §10 package layout settle
*what owns what state*; this document settles *what the window looks like and
how the user arranges it* — menu/status bar, dockable panels, named layout
presets, the Workflow panel's collapse behavior, and light/dark theming.
Reached by discussion with the maintainer, 2026-09-24. Not yet built — this is
the target to build toward, not a record of finished work (see the build log
for that).

**Nothing here is fixed in stone.** The panel-per-preset lists in particular
are a starting point, expected to change once the panels themselves exist and
get used for real work.

---

## 1. Prior art this design reuses, not invents

Two things the maintainer asked for turned out to already exist, proven, in
sibling apps — this design ports/extends them rather than designing from
scratch:

- **Docking is already real** in the *current stable* LSPRi Evaluation app
  (`apps/LSPRi/eva`, `develop` branch): `QMainWindow` + `QDockWidget` for
  every panel, including a Workflow panel docked left
  (`gui/main_window.py`, `gui/layout_state_controller.py:610`), with
  undock/float/resize all working, and whole-layout persistence via
  `QMainWindow.saveState()`/`restoreState()` into settings
  (`layout_state_controller.py:343-420`, key `layout/dock_state`) — including
  a fix for Qt's own bug where a restored floating panel can land off-screen.
  Today that's *one* saved blob (last-used arrangement), not named presets.
- **Named, user-editable layout presets are already real**, in singleLSPR
  Acquisition (`apps/sLSPR/acq`, `gui/main_window_state.py`, top ~600 lines).
  Fixed set of named slots (`LAYOUT_PRESET_KEYS`), each a JSON-serializable
  snapshot; `apply_layout_preset(window, key)` applies one,
  `save_current_layout_to_preset(window, key)` overwrites a slot with
  whatever the user currently has arranged, `reset_layout_presets_to_defaults`
  restores the built-ins, the current selection persists across restarts, and
  View-menu actions stay checked/synced to whichever preset is active. This
  is the "user-definable presets" pattern the maintainer asked to reuse.
- **Two themes are already built and already live-switch**, in
  `packages/lspr_ui/src/lspr_ui/theme.py` (`GuiTheme` dataclass,
  `GRAY_DARK_THEME`/`BRIGHT_THEME`, `apply_base_app_theme`) plus each app's
  own `apply_app_theme` wrapper (LSPRi's: `gui/app_theme.py`). The stable
  LSPRi app already wires this to a live Preferences toggle. Colors were
  tuned per-theme, not inverted/lightened from one another (see the
  dataclass's own docstring) — a light theme needs its own contrast
  calibration against white, not the dark theme's values flipped. ROI/mask/
  highlight colors are deliberately theme-*invariant* so a saved analysis's
  color coding doesn't shift when you switch theme.

The rewrite's own panel scaffolding (`panels/workflow/panel.py` etc., see the
build log) doesn't have any of this yet — `WorkflowPanel` is currently a bare
`QTabWidget`, not dock-wrapped.

---

## 2. Window shell

Standard menu bar (File / Edit / View / Options / Help, ...) plus a bottom
status bar — matches sketch §7's own description of the Workflow shell as "a
thin navigation/status host... hosts the status bar (state/performance only,
no hover-hint text)." No new decision here, just confirming it against what
was already settled.

The **View menu** is where panel presets and theme switching live (§4, §5).

---

## 3. Panel inventory and icon placement

Six panels total: **Workflow** (always present, §4), **Image**, **Histogram**,
**ROI table**, **Spectra**, **Sensorgram** (the five sketch §10 names,
each dock-wrapped like the stable app's panels already are).

Icon/action placement follows a principle already decided for the ROI Toolbox
in sketch §7 ("one backend, several front doors" — Workflow's sub-tabs,
Image panel, and ROI table panel all call the *same* command API, each
exposing the subset that suits its own form factor) — this just extends that
same idea to Image Tools, and states it as a general rule so the Workflow
panel doesn't regrow into a second god object:

| Lives in **Workflow panel** | Lives on the **panel where you see the effect** |
|---|---|
| Settings you set once and forget: detection thresholds, crop/rotate numeric fields, chromatic fit parameters, background model settings | Quick-action icons you use while looking at the result |
| ROI detection run button, group creation form | Image panel toolbar: crop/rotate/flip, "add ROI here," chromatic landmark placement, click-to-select/drag-to-move ROI |
| | ROI table panel: rename/recolor/reorder, bulk multi-select ops |

Rationale the maintainer gave for wanting this split: Workflow was
accumulating every action icon as well as every settings form, which is what
made it feel cluttered and want-to-be-wide. Moving the *quick* actions to
wherever their effect is visible leaves Workflow narrower (§4) and lets it
stay a settings-and-navigation host, not an everything-host.

The exact icon-by-icon inventory isn't final — needs a real pass once Image
and ROI table panels exist to port real toolbar code into, not just names.

---

## 4. Workflow panel: fixed-width, always visible, collapsible

- **Fixed(ish) width**: the sub-tab content described in sketch §7 (Finding
  ROIs / Editing ROIs / ROI Groups, plus Dataset/Image Tools/Analysis stage
  content) stays a reasonable, roughly-constant width rather than growing
  with content — this is what "fixed to width" solves, so it doesn't
  compete for screen space the display panels need.
- **Always present across every preset** (§5) — presets govern which
  *display* panels (Image/Histogram/ROI table/Spectra/Sensorgram) are shown,
  not Workflow itself.
- **Collapsible via auto-hide-to-strip with pin**, VS-Code/Visual-Studio
  style: collapses to a thin clickable edge strip, expands on click (or
  hover — TBD at implementation time), with a pin to keep it pinned open.
  **Flagged as genuinely new engineering, not a port**: neither this app's
  stable `develop` branch nor sLSPR acq has this pattern today — the
  existing dock infra (§1) only gives plain show/hide, not a collapsed
  strip state. A plain show/hide toggle would have been far cheaper to
  build; the maintainer chose the auto-hide-to-strip version anyway,
  explicitly accepting the extra cost for the nicer interaction. Likely
  implementation shape (not committed): a custom `QDockWidget` title-bar
  widget plus a swapped-in collapsed-state widget (a vertical label/pin
  button standing in for the dock's real content) — there's no built-in Qt
  behavior for this, it has to be assembled.

---

## 5. View/Panel presets

Four built-in presets (starting point, not final — see the file header):

| Preset | Panels shown | Notes |
|---|---|---|
| **Image Tools** | Image, Histogram | Also what the **Dataset** workflow stage uses — no separate Dataset preset; browsing/loading a dataset doesn't need a different arrangement than Image Tools work does. |
| **ROI** | Image, ROI table | |
| **Analysis** | Image, Spectra, Sensorgram, ROI table | |
| **Results** | Image, Spectra, Sensorgram | Same panel set as Analysis minus ROI table — deliberately close to it; the maintainer expects these two to converge/diverge further once actually used, not a settled distinction. |

Workflow panel is outside this table — always present, independent of preset
(§4).

**Application**:
- **Manual**: View menu lists the presets, each with a shortcut
  `Ctrl+Shift+1`...`Ctrl+Shift+4` (in the table's order) — chosen over plain
  `Ctrl+1..4` to avoid colliding with more ordinary bindings (e.g. tab
  switching). Worth a real conflict check against this app's actual
  `QAction` shortcut map once built; not verified yet.
- **Automatic**: a Preferences checkbox — *"Automatically apply layout
  preset when switching workflow stage"* — **off by default**. When on,
  clicking a Workflow stage tab also applies that stage's preset. Off by
  default because auto-applying can silently discard a hand-arranged layout
  the moment you change tabs, which cuts against giving the user real
  freedom to rearrange things (§1); manual application is always available
  either way, the toggle only decides whether tab-switching *also* triggers
  it.

**User-editable, per the sLSPR acq pattern (§1)**: each of the four named
slots can be overwritten with the current live arrangement ("save current
layout as this preset"), and reset back to the built-in default. Unlike
sLSPR acq's hand-rolled per-field snapshot dict (splitter sizes, panel
visibility flags — built before that app had real dock-widget layout), a
LSPRi preset snapshot can just be a named `QMainWindow.saveState()` blob:
the dock-based layout (§1) already serializes its own geometry/floating/
tabification state natively, so there's no need to reinvent per-field
capture the way sLSPR acq had to.

**Not decided, flagged for later**: whether users can create *additional*
named presets beyond these four fixed slots (sLSPR acq's pattern doesn't —
it's a fixed small set of editable slots, not arbitrary user-created ones).
Raised as a possible future extension by the maintainer, not committed here.

---

## 6. Theming

Infrastructure already exists and works (§1) — remaining work for the
rewrite is narrower than "build two themes":

1. Wire an actual View/Preferences toggle in the rewrite's own shell
   (nothing calls `apply_app_theme`/`set_active_theme` from a menu action
   yet — `app_rewrite.py` only sets the dark theme once at startup).
2. **pyqtgraph canvases need explicit styling.** `apply_app_theme` covers
   every standard Qt widget via QPalette + QSS, but pyqtgraph's
   `PlotWidget`/`ImageView` draw their own canvas and don't respond to QSS
   at all — background, axis, grid, and text colors have to be set from the
   active `GuiTheme` explicitly, and re-applied on every live switch. This
   touches Image, Histogram, Spectra, and Sensorgram panels. Confirmed as
   real, not-yet-done work — none of the current panel stubs reference
   `pyqtgraph` background colors.

Data-series colors (ROI markers, mask colors, fit curves) stay
theme-invariant per §1 — only chrome (backgrounds, axes, grid lines, text)
changes with the theme.

---

## Open questions

- Exact icon-by-icon placement (§3) — needs a real pass once Image/ROI
  table panels exist.
- Auto-hide-to-strip interaction details (§4) — hover-to-peek vs.
  click-to-expand, pin persistence across restarts.
- Keyboard shortcut collision check for `Ctrl+Shift+1..4` (§5) against this
  app's real `QAction` map, once one exists.
- Arbitrary user-created presets beyond the four fixed slots (§5) — raised,
  not committed.
- Analysis vs. Results preset convergence/divergence (§5) — expected to
  change once used for real work.
