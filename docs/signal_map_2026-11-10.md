# Signal map (2026-11-10)

Maintainer request: instead of reading the response off the Sensorgram line by line, colour every ROI on the Image panel by how much it changed.
Toggle in the Image panel's **Analysis** tab, group "Signal map".

## What it shows

| Show | Value | Unit |
|------|-------|------|
| Shift S − B (default) | mean(S) − mean(B) of an evaluation pair | nm |
| S/N | (mean(S) − mean(B)) / noise of B (SD about a line through B) | – |
| One step's mean | mean of one step; a shift with the Sensorgram's Subtract switch on, a position with it off | nm |

Per: each ROI, or by group (every member gets its group's averaged line value; ROIs in no group share the "Ungrouped" line).
Colours: Blue–white–red and Purple–white–orange (diverging), Viridis, Cividis, Magma, Inferno. Range: Auto (symmetric around 0 when values have both
signs, else 2–98 %), Around zero, Data 2–98 %, Fixed. Grey = no number; faded = out of date.

## Design decisions

- **No second calculation.** The numbers are `StepTableModule`'s (the ROI / Groups table's step numbers = the Sensorgram's lines as drawn). The map asks it
  to `recalculate` all ROIs (or all groups) and reads them with `roi_step_value` / `group_step_value` (added for this). Fitted traces are shared through
  `TraceStore`, so a ROI fitted for the Sensorgram or the table costs nothing; switching Show / scheme / range costs milliseconds.
- **Calculates only on request**: switching the map on, choosing "By group", Refresh. Other input changes (fit settings, Subtract, new scan, moved step) only
  fade the map and light Refresh (amber). The on/off switch is therefore *not* remembered across restarts (it would calculate at launch); all other
  controls are, except the Step (a step belongs to one dataset).
- Progress of a map calculation shows under the ROI / Groups task in the status bar (it is that module's calculation). A map calculation supersedes a
  table calculation that is running (the table's own rows stay as they were and its Refresh lights).
- Colours are a display override (`RoiOverlay.draw(color_override=...)`); stored ROI colours are never changed. The ROI overlay's redraw key includes the
  map's `revision`, so an unchanged map costs nothing per frame.

## Where

- Pure: `analysis/signal_map.py` (schemes, range, colours, `SignalMapView`, export table); Qt-free, in the `lspri-science-no-qt` import contract.
- Module: `selection/signal_map_module.py`.
- UI: `panels/image/signal_map_group.py` (ribbon group), `panels/image/signal_map_display.py` (legend item, hover tooltip, colour override for the panel).
- Wiring: `app_rewrite._wire_signal_map`. Export: the shared `PlotExportButton` with the Image plot (figure includes the legend) and a per-ROI CSV.
- Tests: `tests/unit/test_lspri_signal_map.py`, `tests/integration/test_lspri_signal_map.py`.

## Open

- Unseen by eye (legend placement and size, icon choices: colour-picker = map, ruler = legend, pointer = hover).
- More schemes are one entry each in `SCHEMES`.
- An |S − B| (unsigned) option was not added; ask if wanted.

## Legend as a shortcut (2026-11-10, later the same day)

- Colours drop-down shows each scheme as its colour bar (name in the tooltip).
- The legend on the image is clickable (`SignalMapLegendItem.mouseClickEvent`, `part_at`): left click on title/bar = colour schemes, left click on the low/high number = type a value (range becomes Fixed, the other end keeps the shown value; `SignalMapGroup.set_fixed_bound`), right click = menu (Auto scale, Range, Colours, Show, Pair, Step, Per, Refresh, Legend, Hover values, Turn off).
- The menus are built by `SignalMapGroup` from its own controls, so a choice made on the legend is remembered and shown on the ribbon. The legend only reports where it was clicked; `_wire_signal_map` connects the two.
- The legend's bounding box is the chip only, and `SignalMapDisplay.owns_click` stops a legend click from also selecting/clearing ROIs (`ImagePanel._on_scene_clicked`).
