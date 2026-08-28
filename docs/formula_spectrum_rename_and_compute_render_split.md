# "absorbance" -> "formula_spectrum" rename, and the spectrum compute/render split

2026-08-28. Two related changes: renaming ~700 identifiers that said "absorbance"
when the actual computed value can be any of 4 formulas
(`absorbance`/`ratio`/`relative_change`/`mod_absorbance`, see `formula_key`), and
splitting `_apply_absorbance_spectrum_result`'s (now `_apply_formula_spectrum_result`)
compute/render into two methods - a follow-up to an earlier same-day pass that
deliberately deferred the split as "a design decision, not a refactor."

## The rename

### Scope and the one hard rule

`AbsorbanceSpectrumResult`, `_refresh_absorbance_spectrum`, `_absorbance_spectrum_cache`,
etc. were misleading: the actual value is whatever `formula_key` selected, absorbance
being only the default. ~1,100 occurrences suite-wide, ~665 within
`apps/LSPRi/eva/src` alone.

**The one thing that must never be renamed**: the `formula_key` *enum value*
`"absorbance"` itself (`FORMULA_KEYS = ("absorbance", "ratio", "relative_change",
"mod_absorbance")`, `processing/analysis.py:9`) - that string means "the user
selected the absorbance formula specifically," a completely different thing from
the generic "absorbance" used throughout the misleadingly-named identifiers. A
naive blind string substitution would have corrupted this and silently broken
formula selection.

**Also excluded, deliberately**: the shared HDF5 on-disk group/dataset name
(`LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME = "absorbance_spectra"`,
`packages/lspr_io/src/lspr_io/schema.py:182`, and the `"absorbance"` dataset name
inside it, `storage/measurement_export.py`). This is shared with LSPRi
**Acquisition** (`apps/LSPRi/acq`), which has no formula concept and genuinely
always computes literal absorbance - the naming is *correct* there. Renaming it
would break existing `.h5` files in both apps. `apps/LSPRi/acq/**` and
`apps/sLSPR/**` were never touched by this pass.

**Session-JSON cache keys** (`processing_profile.json`): two keys,
`"absorbance_spectrum_cache"`/`"absorbance_spectral_cube_cache"`, were renamed
*with a read-fallback* (`payload.get("formula_spectrum_cache",
payload.get("absorbance_spectrum_cache", []))`), copying the exact precedent
already in the same method for a prior rename (`roi_absorbance_cache` was
previously `spot_absorbance_cache`). A third key, `roi_absorbance_cache`, was
intentionally left alone - it's not part of this pass, already migrated once.

### Mechanism: `tokenize`, not regex

A small script (kept in the session scratchpad, not the repo) walked every
in-scope file with Python's `tokenize` module and collected `NAME` tokens
containing "absorbance" - structurally excluding `STRING`/`COMMENT` tokens by
construction, so the `formula_key` enum value and the HDF5 literals never even
entered the candidate list. The resulting ~98 distinct identifiers were
hand-reviewed one by one (not blindly "absorbance"->"formula") before building
an old->new mapping table, since the target names aren't a uniform swap - e.g.
`_roi_absorbance_cache` -> `_roi_formula_spectrum_cache` inserts "spectrum"
where `_absorbance_spectrum_cache` -> `_formula_spectrum_cache` doesn't need to.
A second script applied the approved mapping via exact `NAME`-token replacement,
patched into each line **last-column-first** (not `tokenize.untokenize`, which
can reformat whitespace) so the diff is a pure identifier swap.

**A few names were deliberately kept unchanged** after reading their actual
bodies/tests, not just their names:
- `absorbance_from_means()` (`processing/analysis.py`) - a genuinely
  absorbance-specific legacy helper (`formula_value(s, r, "absorbance")`
  hardcoded), plus its tests.
- `_absorbance_groups`/`_absorbance_group`/`absorbance_root`
  (`storage/measurement_export.py`) - private plumbing that resolves the handle
  to the *fixed* on-disk `absorbance_spectra` HDF5 group; renaming the Python
  name while the disk structure keeps saying "absorbance_spectra" would create
  a code/disk mismatch, the opposite of clearer naming.
- `fit_absorbance_curve` -> renamed to **`fit_polynomial_curve`**, not
  `fit_formula_curve` - it's a generic polynomial-fit function used regardless
  of formula (confirmed by reading `fit_curve_for_method`'s dispatch), so it
  was renamed to match its sibling `fit_gaussian_curve`'s "named by method"
  convention instead of the formula_spectrum pattern.
- The bare word `absorbance` (as a local variable, not a compound identifier)
  meant different things in different files - generic in `processing/analysis.py`
  (renamed to `formula_values`, matching `FormulaSpectrumResult.formula_values`)
  and in `test_lspri_measurement_export.py` (renamed to `formula_spectrum`), but
  genuinely absorbance-specific in `test_lspri_analysis.py`/`test_lspri_roi_rasterize.py`
  (kept, since those tests specifically exercise the legacy absorbance-only
  helper). These were excluded from the automatic pass and hand-edited per file.

### The regression grep caught two real, silent runtime bugs

After the bulk rename, a full re-grep for "absorbance" across the renamed scope
turned up two `STRING`-literal identifier references the tokenizer correctly
left alone (by design - it only touches `NAME` tokens) but that were actually
functional, not prose:

1. `plot_style_settings_dialog.py`: `if hasattr(window, "_refresh_absorbance_spectrum"):`
   - the method itself was renamed to `_refresh_formula_spectrum`, so this
   `hasattr` check would have always been `False`, silently breaking "apply
   plot style changes -> refresh the spectrum display."
2. `analysis_worker_mixin.py` (x2): `getattr(point, "roi_absorbance_results", None)`
   - the dataclass field (`SensorgramPointResult.roi_absorbance_results`, in
   `worker.py`) was renamed to `roi_formula_spectrum_results`, so this
   `getattr` would have always returned the `None` default, silently breaking
   both the per-cube HDF5 backup during a sensorgram run and the live-preview
   redraw.

Both were caught only because the post-rename regression grep was read
line-by-line rather than trusted as "probably fine since tests still pass" -
**the existing test suite passed both before and after fixing these**, meaning
neither path has coverage. Also fixed: two quoted forward-reference type
annotations (`dict[int, "AbsorbanceSpectrumResult"]` in `domain/models.py`,
`-> "AbsorbanceSpectrumTraceIndex | None"` in `measurement_export.py`) that are
`STRING` tokens syntactically but function as type references - same
"string-that-acts-like-an-identifier" trap as the `hasattr`/`getattr` cases
above, just for static typing instead of runtime dispatch. **Lesson for next
time a rename like this happens**: after any bulk identifier rename, grep
specifically for `hasattr(`/`getattr(`/`setattr(` with a string argument
matching an old name, and for quoted forward-reference type annotations -
these are the two places a tokenize-based (string-safe-by-design) rename
script will correctly leave a stale reference behind, because the string
*looks* like data to the tokenizer but *acts* like code at runtime/type-check
time.

## The compute/render split

`_apply_formula_spectrum_result` (`analysis_worker_mixin.py`) used to do
per-ROI series drawing, axis-range computation, metric/current-point
calculation, and status-text building all in one ~155-line method, with a
`plot_manager.py add_spectrum_series` doing its own fused compute (NaN-filter/
sort/fit)+render (draw two `PlotDataItem`s + legend) as a black box in the
middle - and a real inefficiency: the curve fit was computed *twice* for the
single-ROI case (once inside `add_spectrum_series` to draw the curve, once
again explicitly for the metric).

Split in 5 independently-verified stages:
- **A**: `plot_manager.py`'s `add_spectrum_series` split into
  `compute_spectrum_series_data()` (NaN-filter/sort/fit, zero Qt) and
  `render_spectrum_series()` (styling + plot calls + legend), with
  `add_spectrum_series` kept as a temporary call-through wrapper - pure
  extraction, zero behavior change.
- **B**: `_apply_formula_spectrum_result` split into
  `_compute_formula_spectrum_result()` (returns a `FormulaSpectrumRenderBundle`
  dataclass, or `None` for the "no valid data" case - the only early-return
  branch that actually lives inside this method; other deferred/error cases
  are handled earlier by the *caller*, `_refresh_formula_spectrum`, so the
  bundle needs no status enum) and `_render_formula_spectrum_result()` (applies
  the bundle to the plot/labels) - but still going through the Stage-A
  wrapper, to isolate "did the extraction preserve behavior" from "did
  switching call sites preserve behavior" as separate diffs.
- **C**: switched to calling `compute_spectrum_series_data`/
  `render_spectrum_series` directly and reused the primary series' computed
  fit for the metric calculation, eliminating the duplicate fit evaluation.
  Before this stage, added `test_repeated_calls_are_bit_identical`
  (`tests/unit/test_lspri_analysis.py`) asserting `fit_curve_for_method`
  called twice with identical inputs gives bit-identical results, for both
  "poly" and "gaussian" - the actual claim the dedup relies on
  (`np.polynomial.Polynomial.fit`/`scipy.optimize.curve_fit` are deterministic
  here, confirmed rather than assumed).
- **D**: `_apply_pending_sensorgram_live_preview` (same fused-call pattern,
  same file) switched to the split pair too.
- **E**: removed the now-zero-caller `add_spectrum_series`/`_add_spectrum_series`
  wrapper and `MainWindow` forwarder.

`FormulaSpectrumRenderBundle` lives in `analysis_worker_mixin.py` itself (not
`analysis_types.py`) since it's produced and consumed entirely within that one
file - no circular-import reason to hoist it out, unlike
`SpectrumSettingsSnapshot`/`SharedWavelengthGeometry` (which cross the
worker-mixin/chromatic-mixin boundary) or the newly-added
`SpectrumSeriesComputedData` (which *does* cross plot_manager.py <->
analysis_worker_mixin.py, so it lives in `analysis_types.py`).

Verified via `py_compile` + a runtime import/attribute smoke test + the full
`tests/*lspri*` suite (438 passing, up from 437 - the one new determinism
test) after every one of the 11 total stages across both parts.
