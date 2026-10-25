# Sensorgram: y-axis switch, baseline selector, y-range fix (2026-10-25)

**Asked for.** (1) Switch the y axis between the absolute position and the shift from the baseline by a double-click on the y axis label. (2) A View-group
icon that shows the baseline range selector as a short band (about 10 % of the plot height) with a colour and a fill transparency, like the Image
ribbon's ROI display menu.

**What was already there.** The absolute y axis was already the default (Baseline > Subtract off); Subtract on gives the shift. So (1) is a second way
to flip that same switch (`SensorgramPanel._on_y_label_double_clicked` -> `_on_baseline_toggled`), nothing more. The tooltip of the axis says so.

**Selector.**
* `baseline_selector_menu.py`: the View button (a plot frame with a band along its bottom) and its pick-up panel. The toggle / colour swatch / transparency
  wedge is the Image ribbon's `RoiOverlayControls`, reused through its new `OverlaySpec` argument (no copy).
* `plot.py`: the `LinearRegionItem` has `span=(0, 0.1)`: the bottom tenth of the view; `set_baseline_style(colour, alpha)`.
* Shown or hidden independently of Subtract; default shown (as before). Dragging it now only **stores the window** (it used to switch Subtract on as
  well). With no stored window it is drawn where Subtract would put it (first tenth of the axis) without storing anything.
* A window stored in the old unit is re-placed when the Cube / Time choice changes, subtracting or not (before: only while subtracting).
* Remembered: `sensorgram/baseline_band_visible|color|alpha` (`storage/ui_state_keys.py`).

**Y-range bug found on the way (older than this change).** In "Average all / by group" the shaded SD / SEM band stretched the y auto-range to about
-32 .. 641 nm for peaks near 595 nm. Measured: the real scan (`2026-10-22_172035`, polynomial 9, centroid) holds only 588 - 605 nm, and the plot with
a single line ranges 582 - 609 nm, so the data were right; `pg.FillBetweenItem`'s bounding rectangle was the cause. Fix (`plot.show_series`): the fill is
added with `ignoreBounds`, and the band's two edge curves (a fully transparent pen: a curve with *no* pen is also skipped by the auto-range) give the
range. Pinned by `test_the_y_range_of_an_average_follows_the_line_and_its_band_not_the_fill`. The Spectra panel's error bars were not checked.

**Fill rendering (same day, from the maintainer's look at it).** Three causes, all in `plot.py`:
* `LinearRegionItem.setBrush` sets the new brush but never repaints, and it forgets the hover state: a new transparency showed only when the pointer
  moved over the band. `set_baseline_style` now restores the hover state and calls `update()` (measured: with the plot shown, the bare call produces no
  scene repaint, ours produces one over the band).
* The band's rectangle is computed from the visible y range, so it moves at every auto-range; its repaint is now requested from `sigRangeChanged` and
  after `setRegion`.
* The resting fill (alpha 0.16) was only slightly lighter than the plot background (rest (41,49,68) against (25,28,32); under the pointer (55,70,101))
  and pyqtgraph's hover brush is a big jump, so the band read as "off" until touched. Default alpha is now 0.28 and the hover lift 0.08.
Pinned by `test_a_new_transparency_is_repainted_at_once_and_keeps_the_hover_brush` (the plot must be shown: a scene reports repaints only then).

**Not looked at on screen:** the band's height, the icon, the menu.

**Ribbon rearranged (2026-10-26).** Smoothing group = method pick-up menu (`spectra/pick_menu.PickMenu`, answers like the combo it replaced: jagged line / SG / MA)
+ window number (no "window" text, hidden while the method is "none") + the spike-removal icon toggle (`stat_icons.spikes_icon`; the "Spikes" group is gone).
Baseline group = the Subtract icon toggle (`stat_icons.subtract_icon`, same switch as the y-label double-click) + the selector menu button. View group = the mask marks only.
Start / end fields float on the plot at the base of the band (`baseline_selector_menu.BaselineRangeReadout`, placed by `plot._reposition_range_readout`); the menu also holds the band's height in % of the plot (remembered).
Not seen on screen: the SG / MA letters (the offscreen test machine has no fonts), icon sizes, ribbon width.
