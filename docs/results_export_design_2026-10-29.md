# Results export ("Outputs"): design proposal (2026-10-29)

**Maintainer's answers (2026-10-29), which override the proposal where they differ:**

- The old LSPRi Evaluation app **is not used**, so there is no old format to stay compatible with. The export is ours to shape.
- **The file format is not decided and will be decided later**, once all functions are in. It is too early to settle tables and layout. Do not build before that discussion.
- Already decided: the most useful content is the **Sensorgram and the statistics from it**; the export is available **from any plot area**, both as a **figure** and as **data**; out-of-date numbers are **warned about**, not refused; the folder is **picked each time**, with a default in a dedicated export folder inside the session.
- Consequence for the screen: section 3 assumed one *Outputs* section. With export available from every plot, the main control is one shared export control on each plot (section 10). The *Outputs* section can stay for bulk export or be dropped.

**Status: proposal. Nothing is built.** It is the design for item P1 / decision C1 of `old_generation_cutover_plan_2026-10-29.md`: the rewrite cannot yet get numbers out, and the Workflow panel's *Outputs* section is an empty "Outputs - not built yet." label. Section 9 lists what I need you to decide before any code is written.

## 1. What an export has to carry

Scientist's view: "the sensor response, as numbers I can plot and report, and enough about how I got them to repeat it." Three kinds of result already exist in the app:

| Result | Where it is computed | What one row is |
|---|---|---|
| **Sensorgram** | `analysis/scan_trace.py` `compute_sensorgram` -> `Series` | per line (one ROI, or a group / the average): value per cube (nm), optional band (low / high), the x value (cube number or time), the baseline that was subtracted, the member ROIs |
| **Spectra** | `analysis/scan_view.py` `compute_spectra` -> `RoiSpectrum` | per ROI at one cube: wavelength (nm), formula value, its error, the fitted curve, the metric wavelength (peak position) |
| **Step statistics** | `analysis/step_stats.py` `StepStats`, `GroupStepStats`, `LineStepValues` | per ROI / group and plan step: n, mean, SD, SEM, drift slope, detrended noise |

Not computed anywhere in the main app yet: a **response** per baseline / signal step pair (Delta, its uncertainty, SNR). The ROI-tuning code already defines it (`analysis/roi_tuning.py`: `delta` = mean of the signal step minus mean of the baseline step, `delta_sem` = the two means' SEMs added in quadrature, `noise` = the detrended noise of the baseline step, which `step_stats` also calls `noise`, `snr` = |delta| / noise). An export could offer that table with the same formulas so the numbers agree with the tuning; it would be numbers shown for the first time, so it is a question for you (section 9, question 2).

And the record of **how** each number was made (formula, fit and metric settings are not part of the scan file; they are applied when a result is drawn, so an export has to write them down): formula key, `MetricSettings` (fit method, polynomial order, fit window, metric, range), `StatisticsSettings` (smoothing, spike rejection, baseline, display mode, aggregation), the ROI table (id, group, centre and diameters in processed pixels, colour), the plan steps, the dataset and session, the scan file's stamp (`SCAN_ALGORITHM_VERSION`, format version), app name and version, the time of export, and which cubes were **out of date** at that moment.

## 2. Principles (from the project rules)

- **Export what is drawn.** The export builds its numbers with the same functions and the same request the panel used, so the file equals the plot (a test pins this). It never re-implements the maths.
- **Never silent about stale data.** If cubes are out of date (hollow rings on the Sensorgram), the file marks them and the message after the export says so. It does not refuse (you may want it anyway) and it does not hide it.
- **Raw and derived stay separate; nothing is overwritten.** Every export goes into a **new** folder named with the time; an existing file is never replaced. Outputs live inside the session, `sessions/<id>/outputs/<timestamp>_<label>/`, so they travel with the session that produced them.
- **Open formats first.** CSV (UTF-8, comma, one header row, columns read by name, units in the column name, empty cell = no value, numbers written with full precision), exactly as the ROI-tuning table already does (`analysis/roi_tuning_export.py`). One JSON file records the settings.
- **Export code is separate from analysis code, and pure.** Table builders have no Qt and no files; a thin writer writes. Heavy work runs on a plain thread with progress and Cancel, shown in the status-bar task indicator (the `ThreadedTask` pattern, like Scan and Auto mask).
- **Remembered like every control** (`ui_state.bind(...)`).

## 3. Screen description (as the GUI rules ask)

```text
Screen purpose:
  Get the evaluated results out of the app as files, with the record of how they were made.
  It lives in the Workflow panel, stage "Outputs" (the section that is a placeholder today).

Main user actions:
  1. Tick what to include: Sensorgram | Step statistics | Response | Spectra | ROIs and groups | Settings record (always on)
  2. Choose which ROIs: all / selected / as shown on the Sensorgram
  3. Press "Export"; watch progress in the status bar; Cancel if needed
  4. Press "Open folder" to see the files

Layout:
- Left panel:    n/a (the section sits in the Workflow panel, one column, ~340 px wide like its siblings)
- Center panel:  n/a
- Right panel:   n/a
- Section body, top to bottom:
    Include:        [x] Sensorgram   [x] Step statistics   [ ] Response (Delta, SNR)   [ ] Spectra   [x] ROIs and groups
                    (Settings record is always written; shown as a fixed, greyed line)
    ROIs:           ( ) All   (o) As on the Sensorgram   ( ) Selected
    Sensorgram:     (o) As drawn (smoothing, baseline, groups)   ( ) Also the unprocessed trace per ROI
    Spectra:        (o) Current cube   ( ) All cubes     [note: size estimate shown for "All cubes"]
    Folder:         <session>/outputs/    [Open folder]
    [ Export ]   [ Open last export ]
- Bottom/status area: the app's task indicator (spinner, progress, Cancel, result message with the
  number of files and any "n cubes were out of date" warning).

Important states:
  - no dataset / no session / no scan file yet: section disabled, one line says why and what to do ("Run Scan first")
  - scan file present but cubes out of date: Export enabled; the result message warns; the files mark the cubes
  - Sensorgram not yet computed for the current settings: the export computes it (helper process, same as the panel)
  - export running: Export becomes Stop; the dataset, session or scan changing cancels it and drops the result
  - finished: "Open last export" enabled; message "Exported 4 files to ..."
  - nothing ticked: Export disabled
  - "All cubes" spectra ticked: shows the expected size before you press Export

Validation rules:
  - at least one item ticked and at least one ROI in scope
  - the output folder must be creatable and writable
  - "All cubes" spectra over a threshold size (proposal: 200 MB) asks for confirmation once

Error messages (plain language, with the next step):
  - "No results to export yet. Run Scan on the Analysis tab first."
  - "Could not create the folder <path>: <system reason>. Choose another location or free some space."
  - "Export stopped. Nothing was kept." (Cancel: the partly written folder is removed)
  - "The data changed while exporting (dataset, session or scan). Nothing was kept; press Export again."
  - "n cubes are out of date (scan again for current numbers); they are marked in the file."
```

## 4. Files written (version 1)

One folder per export, e.g. `sessions/<id>/outputs/2026-10-29_153045/`:

| File | Content |
|---|---|
| `sensorgram.csv` | **Wide**: `cube, x_value, x_unit, out_of_date, <line key>_nm, <line key>_low_nm, <line key>_high_nm, ...`, one row per cube; line keys are ROI ids and group names (also in `lines.csv`) |
| `lines.csv` | one row per line: key, label, colour, number of member ROIs, member ROI ids, baseline subtracted (nm) |
| `step_statistics.csv` | **Long**: `line, step, step_note, n, mean_nm, sd_nm, sem_nm, slope_nm_per_s, noise_nm` |
| `response.csv` *(optional, new, question 2)* | **Long**: `line, baseline_step, signal_step, delta_nm, delta_sem_nm, noise_nm, snr, n_baseline, n_signal`, with the formulas of the ROI-tuning code |
| `spectra_cube<N>.csv` (or `spectra.csv` for all cubes) | **Long**: `roi_id, cube, wavelength_nm, value, error, fit_value, metric_wavelength_nm` |
| `rois.csv` | ROI id, group, centre x / y (processed px), diameters (px), colour |
| `steps.csv` | the experimental plan steps as shown (number, note, start, end, baseline / signal role) |
| `export_info.json` | the record of section 1: `format_name: "lspri_results_export"`, `format_version: "1.0"`, app name and version, UTC time, dataset, session, scan stamp, all settings, list of out-of-date cubes, file list with row counts |

Why CSV and JSON, not one HDF5 file: the rules prefer open formats for simple tables, every spreadsheet and plotting program reads them, and a search of this repo found no program that reads the old schema-7 file (decision C2). HDF5 can be added later for the one large item (all-cubes spectra) without changing the rest.

## 5. How it is built (small steps, each testable)

1. **Pure builders** `analysis/results_export.py`: take `Series`, `RoiSpectrum`, step statistics, ROI table and settings; return lists of rows and a dict for the JSON. No Qt, no files. Tests with small deterministic fixtures: the CSV equals the `Series` values exactly (full-precision round trip), empty and NaN cells, units, out-of-date marking, a ROI in no group.
2. **Writer** (`write_export(folder, tables, info)`): creates the new folder, writes files, removes a half-written folder on error or cancel. Plain thread only.
3. **Task and module** (`ResultsExportTask(ThreadedTask)` + a small module): `snapshot` on the GUI thread (the same request object the panel holds, copied into plain data), compute on a plain thread (`compute_sensorgram` with the panel's known traces so a cache hit costs nothing; the helper process for fits), `_on_result` reports. Joins the task indicator (report, finish, cancel connects in `app_rewrite.py`).
4. **Section body** `panels/workflow/outputs_section.py`: only widgets and signals; every control `ui_state.bind(...)`. Real buttons so tests can `.click()` them (GUI rule).
5. **Later, optional:** an Export icon on the Sensorgram and Spectra ribbons that exports exactly the plot in front of you.

Reproducibility check for the maintainer: export the same session twice with unchanged settings and compare the files (byte-identical except the time in `export_info.json`); change the polynomial order and see the settings record change.

## 6. Cost and size

- Sensorgram, 160 ROIs x 314 cubes: about 50,000 numbers; the polynomial fit takes about 1 s, the Gaussian one about 2 s (measured 2026-10-24; recorded in `rewrite_status_by_feature_2026-10-29.md`, "Analysis redesign"). A CSV of a few MB.
- Spectra of one cube: 160 ROIs x 27 wavelengths = 4,300 rows.
- Spectra of **all** cubes: 314 x 160 x 27 = about 1.36 million rows, roughly 80 MB of CSV. That is why it is a separate, confirmed option.

## 7. Not in version 1 (and why)

- Figures (PNG / SVG of the plots): pyqtgraph can export them; a separate small step if you want it.
- The old schema-7 measurement file: see C2 in the cut-over plan; nothing in this repo reads it.
- Writing results back into the dataset folder: outputs stay in the session (rule: raw data is never touched).

## 8. Rules this adds to the project (to be copied into CLAUDE.md when built)

An export builds its numbers with the panel's own functions; it marks out-of-date cubes and never hides them; it never overwrites a file; the settings record is always written; the table builders are pure.

## 9. Questions and the maintainer's answers (2026-10-29)

1. **What do you export from the old app today, and into what?** Origin, Excel, Python, a report? This decides wide versus long tables, units in the headers, and one more thing: **if you open the files in Excel on a Czech (or other non-English) setup, a comma-separated file with decimal points opens wrongly.** Do you want an "Excel" variant (semicolon, decimal comma) as a choice?
   **Answer:** the old app is not used. Format later.
2. **Which tables do you really need?** Sensorgram per ROI and per group; step statistics (mean, SD, SEM, slope, noise); spectra of the current cube; spectra of all cubes. And do you want the new **response table** (Delta, SNR per baseline / signal step pair, same formulas as ROI tuning)? Anything missing?
   **Answer:** not worth discussing now. Most useful: the Sensorgram and the statistics from it.
3. **Do you want figures** (PNG / SVG) in version 1?
   **Answer:** yes, figures and data, both from any plot area.
4. **Stale numbers:** mark and warn (my proposal), or refuse until the scan is current?
   **Answer:** warn.
5. **One folder per export under the session** (proposal) or a place you choose each time?
   **Answer:** choose each time; the default can be a dedicated export folder inside the session.
6. **Is there a downstream script or colleague that needs the old schema-7 file?** (C2.) If yes, I need a sample of how it is read.
   **Answer:** the maintainer asked what that file is (explained 2026-10-29: the old app's results and backup file); no known use, see C2 in the cut-over plan.

## 10. What the answers change in the design (apply when the format is discussed and the export is built)

- **One shared export control on every plot area** (today: Sensorgram, Spectra, Histogram), built once as a small shared component and wired to each plot, with two actions: **Save figure** (the plot as drawn; pyqtgraph can write PNG and SVG; to be confirmed when built) and **Save data** (the numbers behind that plot). "Export what is drawn" stays the rule, so the data of a plot is the data of that plot, and a test pins it.
- **First content:** Sensorgram data and the statistics shown with it (the step-statistics table of the Sensorgram). Spectra data through the same control later. The response table (Delta, SNR) and all-cubes spectra wait for the format discussion.
- **Folder:** a dialog each time, opening in `sessions/<id>/exports/` (created on first use), where a file is never overwritten silently.
- **Warning:** the message after an export, and the file itself, say how many cubes were out of date.
- **Dropped:** compatibility with the old schema-7 file and any old export.
- **Still open for the format discussion:** CSV versus other formats, wide versus long tables, units in headers, the Excel locale question, what the settings record contains.
