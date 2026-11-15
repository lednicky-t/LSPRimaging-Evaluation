# Can `panels/image/panel.py` be split, simplified, cleaned up? (investigation, 2026-11-15)

Maintainer asked: "investigate whether it can be split, simplify, clean up a bit to promote maintenance". Sections 1-6 are the investigation (done first, with
three zero-risk clean-ups). **Maintainer's answer: do seams 1, 2, 3 and 5, not 4 (the render pipeline); trim the comments without losing information; commit and push
everything. Section 7 records what was done.**

## 1. What it is today (measured with an AST script, 2026-11-15)

| | |
|---|---|
| file | 2,711 lines (3,492 before the audit's item D; 2,630 after it; 2,714 at the last commit; it grew again with the wand, lock, brush and value tools) |
| `ImagePanel` | one class, 121 methods, 113 attributes assigned on `self`, 17 signals, 77 classes constructed inside it, 97 import statements |
| code vs words | about 1,750 lines of code; 432 comment-only lines, 306 docstring lines, 225 blank. `_build_top_bar` is 48 % comment, `_build_canvas` 30 % |
| the 14 longest methods | 1,337 lines (half the file): `_build_navigation_bar` 202, `__init__` 184, `_build_canvas` 173, `_on_rendered` 136, `_build_toolbar_and_layout` (now ~100), `_build_top_bar` 107, `_redraw` 94, `_connect_modules` 70, `restore_ui_state` 67 |
| by role | widget building 10 `_build_*` methods, 695 lines; `__init__` 184; event handlers (`_on_*`) 33 methods, 361 lines; refresh / redraw / draw / render about 500 lines; restoring saved state 74 |

**The shared state is small.** Only 17 attributes are used by eight or more methods (`_dataset` 22, `_roi_overlay` 17, `_slider_marks` 14, `_active_tool`,
`_selection`, `_chromatic`, `_roi_toolbox`, `_slideshow`, `_tool_ribbon` 12-13, `_frame_text`, `_last_image_shape`, `_view` 11, `_geometry`, `_interaction`,
`_background`, `_decorations`, `_tints` 8-10). The other ~95 attributes are touched by one to three methods: widget handles kept on `self` only because the
builders and the handlers are methods of the same class. **The class is mostly a namespace**, which is why it can be cut along its builders, and why it
*feels* big: a reader cannot see which of 113 fields matter.

## 2. What was already done (do not redo)

Audit item D batches 1-3 (2026-11-02): 8 helper objects (`tint_overlays`, `decoration_overlays`, `frame_status_text`, `cursor_readout`, `roi_queries`,
`crop_measure_controls`, `slider_marks`, `slideshow_state`) took `panel.py` from 3,492 to 2,630 lines and 178 to 120 methods. The pattern: the helper does not
import the panel; it gets the few values it needs as small functions. The audit left, "deliberately", **construction** (`__init__`, `_build_*`, ~860 lines touching
105 of 118 fields: "needs a different design, a navigation-bar object that owns its widgets"), the Play handlers and the theme refresh.
Today's work followed the same pattern for the new features: `selection_regions.py`, `selection_tools.py` (the toolbar and its tools in one builder: 5 lines in
the panel), `lock_layer.py`, `tool_drag.py`, `selection_by_value.py`, `selection_modify.py`, `scrolling_column.py`. A new tool is now one file and a few lines of wiring.

## 3. Seams, in the order I would take them

| # | Seam | What moves | Lines | Risk | Why / guard |
|---|---|---|---|---|---|
| 1 | **Top bar and tabs** (`_build_top_bar`, `_build_image_tools_tab`, `_build_mask_tab`, `_build_roi_tab`, `_build_view_tab`, `_build_chromatic_and_background_tabs`) into an `ImageRibbon` builder that returns the tabs and the ribbon | construction only: no handler moves | ~210 | low | pure widget construction; the tab objects already exist (`MaskTab`, `RoiTab`, `ViewTab`, ...). Guard: `test_lspri_rewrite_image_panel.py` (109 tests), the tab files' own tests, the size budget |
| 2 | **Canvas layer stack** (`_build_canvas`): the pyqtgraph view, the image item and every tint / overlay item, in the paint order that decides what draws over what | construction of ~15 items, the `ImageViewBox` wiring | ~170 | medium | the *order of creation* is the paint order (a comment in the code says so): it must move as one block. 51 self-names are touched; 8 only here. Guard: overlay and render tests (`test_lspri_rewrite_overlay_*`, the tint tests), a look at the image |
| 3 | **Navigation bar** (`_build_navigation_bar` + the slider handlers `_refresh_navigation_ranges`, `_refresh_wavelength_range`, ...): an object that owns the Cube / Wavelength sliders, the strips, the slider ribbon, the Mask rows and emits signals | construction and ~12 handlers | ~400 | medium-high | the audit's "navigation-bar object that owns its widgets"; 43 self-names; Play and the slideshow depend on it. Needs a short design note first |
| 4 | **Render pipeline** (`_on_rendered` 136, `_redraw` 94, `_draw_*`, `_refresh_overlay_items`) | the code that decides what is on screen after a frame arrives | ~500 | **high** | this is the performance-critical path of the Play work (4K fps, render thread, GIL hand-offs: `docs/play_diagnosis_2026-11-11.md`); any move needs the Play timings before and after. I would **not** touch it unless that work needs it |
| 5 | **Constructor** (`__init__`, 184 lines) | split into `_make_modules` / `_make_state` / `_make_helpers` in the same class | 0 | very low | cosmetic: it makes the list of what the panel owns readable at a glance; no behaviour moves |

Rough result of 1 + 2 + 5: `panel.py` about 2,330 lines and ~110 methods; with 3 about 1,930 lines and ~90 methods. Method and field counts drop in step.

## 4. Things that make it harder to read, and cost nothing to fix

- **Narrative comments.** 432 comment lines, many of them dated history of one decision (`_build_top_bar` L1161-1208 is 45 lines about the cursor and "i" icons,
  `_build_canvas` L779-796 is 20 lines about the landmark overlay). The *reasoning* belongs in the docs and the commit; the code needs one line. Trimming
  would remove about 150-200 lines without changing a byte of behaviour. (Root rule: old file names in comments are left alone; this is about blocks that
  duplicate a doc.) Proposed as its own small commit, so it can be reviewed apart from any move.
- **33 one-line accessors** (`roi_display`, `edit_target`, `analysis_tab`, `tuning_tab`, `mask_export`, `mask_auto`, `feature_map`, `array_action`, ...):
  the panel's public face for the app shell. They are legitimate, but they add 33 of the 121 methods. They could become a small read-only `parts` object
  (`panel.parts.tuning_tab`), at the cost of touching every caller in `app_rewrite.py`. Not worth it alone; natural to do with seam 1.
- **Six attributes read only while building** (`_chromatic_auto`, `_current_display_image`, `_initial_chromatic_values`, `_initial_chromatic_view`,
  `_initial_ribbon_category`, `_mask_pointers`): they could be local variables or live in the builder from seam 1.
- **97 import statements** (91 `from` imports): they shrink by themselves when seams 1-3 move.

## 5. Done now (zero behaviour change)

- Six selection helpers left `ImagePanel` for `SelectionRegions` (earlier today); the toolbar's wiring (about 40 lines) left for `SelectionTools`.
- Removed the dead field `_drag_roi_id` and two wrappers only the tests called (`_on_crop_drag_event`, `_on_measure_drag_event`; the tests now call
  `CanvasInteraction.on_crop_drag` / `on_measure_drag`, the real owners). `_on_left_drag_event` stays: the view box is built before the interaction object
  exists, so it needs the late-binding method.
- `panel.py` 2,718 -> 2,711 lines (it was 2,714 at the last commit); `tests/unit/test_lspri_panel_size_budget.py`: methods 124 (met), lines **2,715** (was
  2,710; raised on purpose by 5 lines, reason in the test; to be confirmed by the maintainer, with the reason in the commit message).

## 6. Recommendation

1. Seam 1 (top bar and tabs) + seam 5 (constructor): lowest risk per line, about 210 lines out, no handler moves.
2. Then seam 2 (canvas layers) with a look at the image, since the paint order is visual.
3. Seam 3 (navigation bar) only after a one-page design note; seam 4 only if the Play work needs it.
4. The comment trim as a separate commit whenever convenient.
5. Keep the habit that stopped today's growth: a new feature is a file of its own plus a builder (like `SelectionTools`), and `test_lspri_panel_size_budget.py` makes any growth a decision.

Decisions for the maintainer: (a) go for 1 + 5? (b) is the +5 lines on the size budget (2,715) acceptable? (c) trim the narrative comments (about 150-200 lines)?

## 7. Done 2026-11-15 (maintainer's go: seams 1, 2, 3, 5; not 4; trim comments)

| | before | after |
|---|---|---|
| `panels/image/panel.py` | 2,723 lines (2,711 at the start of this section) | **2,016** |
| `ImagePanel` methods | 121 | **115** |
| `__init__` | 184 lines | **99** (two named steps taken out: `_init_display_state`, `_start_render_thread_and_timers`) |
| new files | | `canvas_build.py` 233, `ribbon_build.py` 264, `navigation_build.py` 237, `panel_constants.py` 38 |
| size budget (`test_lspri_panel_size_budget.py`) | 2,715 lines / 124 methods | **2,150 / 118** (about 3 % of room) |

**How:** a *mechanical* move, done by one script, nothing retyped: each `_build_x(self)` became `build_x(panel)` in a new module, the body being the old text with `self`
replaced by `panel` (seam 2 = the canvas, `build_canvas`; seam 1 = the ribbon tabs and the top bar, six functions; seam 3 = `build_navigation_bar`). The panel keeps every attribute
name, so nothing else in the panel and no test had to change. Each function's docstring lists what it creates on, reads from and calls on the panel (generated from the code).
The names the panel and the builders share moved to `panel_constants.py` (a builder cannot import them from the panel without a cycle). Seam 5 split `__init__` into named steps in the
same order. A stale text found on the way: the Mask tab's "i" tooltip still listed the five tools that moved to the selection toolbar; it was rewritten.

**Honest limit.** These builders still reach into the panel's private fields: the code is in files of its own and its dependencies are listed, but it is not decoupled.
The step after this is objects that own what they create (for the navigation bar: a `NavigationBar` that owns the sliders, rows and ribbon and exposes signals, so
`self._cube_slider` becomes `self._nav.cube_slider`), which needs the panel's handlers and the tests to follow. Not done: it changes names across the panel and ~30 test
files. Seam 4 (the render pipeline) was left alone on purpose.

**Comment trim.** 15 long narrative comment blocks (6-19 lines each) in the panel and the three builders were condensed to 2-6 lines each (about 130 lines in all): every
decision, reason and constraint was kept (dates of decisions too), what went is the story of how a line got there. One comment named a timer that does not exist
(`_view_range_debounce_timer`; the real one is `_view_range_persist_timer`): corrected.

**Checked:** full LSPRi test run green after the move (242 files; two load-sensitive files passed alone on retry), `lint-imports` 5 kept, 0 broken, ruff and pyflakes clean;
after the trim the image panel tests (111), the toolbar tests and the size budget again.
