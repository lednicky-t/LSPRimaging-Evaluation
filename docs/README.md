# LSPRi Evaluation docs: index

Most files are dated records: a design, an audit, or a measurement, written when the work was done. They explain *why* something is the way it is; they are not a current status. The rules are in [`../CLAUDE.md`](../CLAUDE.md). For the current state of the code, trust the code and CLAUDE.md first, then the newest dated doc on the topic.

Titles below are read from the files. Date in a file name = when it was written (a file without a date is a standing reference).

## Start here

Rules, structure, and the records every other doc builds on.

- [`whole_app_audit_2026-10-29.md`](whole_app_audit_2026-10-29.md): LSPRi Evaluation: whole-app audit (clean-up, maintenance, documentation), 2026-10-29 *(latest audit: start here for clean-up and cut-over questions)*
- [`old_generation_cutover_plan_2026-10-29.md`](old_generation_cutover_plan_2026-10-29.md): Cut-over plan: replacing the old LSPRi Evaluation app with the rewrite (2026-10-29) *(done 2026-10-29 except phase 3, the module renames: entry points, old code, old engine, dead functions)*
- [`whole_app_audit_2026-10-07.md`](whole_app_audit_2026-10-07.md): LSPRi Evaluation rewrite: whole-app audit and fix plan (2026-10-07)
- [`rewrite_status_by_feature_2026-10-29.md`](rewrite_status_by_feature_2026-10-29.md): Where the rewrite stands: feature-by-feature record (moved out of CLAUDE.md, 2026-10-29) *(per-feature record moved out of CLAUDE.md: search it by feature name, never read it whole)*
- [`rewrite_rules_and_decisions_2026-09.md`](rewrite_rules_and_decisions_2026-09.md): Rewrite rules and decisions (design record) - LSPRi Evaluation
- [`rewrite_architecture_sketch_2026-09.md`](rewrite_architecture_sketch_2026-09.md): LSPRi Evaluation rewrite: architecture sketch (2026-09-20)
- [`rewrite_feature_inventory_2026-09.md`](rewrite_feature_inventory_2026-09.md): LSPRi Evaluation rewrite: feature inventory (2026-09-19)
- [`spectra_analysis_methods.md`](spectra_analysis_methods.md): Spectra analysis methods: what is computed where (LSPRi Evaluation rewrite)
- [`image_tools_coordinate_spaces.md`](image_tools_coordinate_spaces.md): Image Tools: Coordinate Spaces and Where They Apply
- [`format_versioning.md`](format_versioning.md): Format Versioning
- [`versioning_and_repo_practices.md`](versioning_and_repo_practices.md): Versioning and Repo Practices
- [`logging_design_2026-10-14.md`](logging_design_2026-10-14.md): Logging and diagnostics design (LSPRi Evaluation rewrite) - 2026-10-14

## History of the rewrite

How the rewrite was planned and built; read for background, not for current status.

- [`rewrite_build_log_2026-09.md`](rewrite_build_log_2026-09.md): LSPRi Evaluation rewrite — Build Log *(482 KB: search it by date or keyword, never read it in full)*
- [`rewrite_status_and_plan_2026-09-30.md`](rewrite_status_and_plan_2026-09-30.md): LSPRi Evaluation rewrite: status and plan (2026-09-30) *(history: its "what is built" claims are stale)*
- [`rewrite_gui_shell_design_2026-09.md`](rewrite_gui_shell_design_2026-09.md): LSPRi Evaluation rewrite — GUI shell design (window chrome, panel presets, theming)
- [`whole_app_bug_audit_2026-09.md`](whole_app_bug_audit_2026-09.md): LSPRi Evaluation — whole-app bug/data-flow audit (2026-09-08)
- [`panels_cleanup_plan_2026-10-06.md`](panels_cleanup_plan_2026-10-06.md): Workflow / Image panel cleanup plan (2026-10-06)

## Analysis, scan, Spectra and Sensorgram

From pixels to numbers, and how the numbers are shown.

- [`analysis_redesign_plan_2026-10-19.md`](analysis_redesign_plan_2026-10-19.md): Analysis redesign: plan and design proposal (2026-10-19)
- [`analysis_step1_audit_2026-10-23.md`](analysis_step1_audit_2026-10-23.md): Analysis step 1 audit (2026-10-23)
- [`analysis_pipeline_layers.md`](analysis_pipeline_layers.md): Analysis pipeline layers: what's per-ROI, what's Statistics
- [`analysis_pipeline_redesign.md`](analysis_pipeline_redesign.md): Analysis Pipeline & Caching Redesign
- [`analysis_caching_architecture.md`](analysis_caching_architecture.md): Analysis caching architecture: what exists and why
- [`analysis_provenance_store_design_2026-09.md`](analysis_provenance_store_design_2026-09.md): Analysis provenance store design
- [`formula_spectrum_rename_and_compute_render_split.md`](formula_spectrum_rename_and_compute_render_split.md): "absorbance" -> "formula_spectrum" rename, and the spectrum compute/render split
- [`dark_frame_wavelength_zero_policy.md`](dark_frame_wavelength_zero_policy.md): Wavelength 0.0 nm ("dark frame") policy
- [`fit_window_modes_test_2026-10-25.md`](fit_window_modes_test_2026-10-25.md): Fit window modes: max ± width, peak ± k × FWHM (test, 2026-10-25)
- [`poly_order_quality_study_2026-10-25.md`](poly_order_quality_study_2026-10-25.md): Polynomial order: what tells a good order from an over-fitted one (study, 2026-10-25)
- [`shift_estimators_comparison_2026-10-25.md`](shift_estimators_comparison_2026-10-25.md): Ways to read a peak shift other than fit-then-compare (test, 2026-10-25)
- [`spectral_outlier_detection_investigation.md`](spectral_outlier_detection_investigation.md): Spectral outlier detection: debris, splices, and real transitions
- [`sensorgram_panel_split_2026-10-26.md`](sensorgram_panel_split_2026-10-26.md): Sensorgram panel split (2026-10-26)
- [`sensorgram_fit_freeze_2026-10-24.md`](sensorgram_fit_freeze_2026-10-24.md): Sensorgram fit froze the window (2026-10-24)
- [`sensorgram_mask_marks_2026-10-24.md`](sensorgram_mask_marks_2026-10-24.md): Sensorgram mask marks (2026-10-24)
- [`sensorgram_baseline_selector_2026-10-25.md`](sensorgram_baseline_selector_2026-10-25.md): Sensorgram: y-axis switch, baseline selector, y-range fix (2026-10-25)
- [`sensorgram_reentrancy_and_cube_slider_cache_indicator.md`](sensorgram_reentrancy_and_cube_slider_cache_indicator.md): Sensorgram Start/Stop reentrancy fix, and the Cube/Time slider cache indicator
- [`step_statistics_backbone_2026-10-26.md`](step_statistics_backbone_2026-10-26.md): Step statistics: the backbone (2026-10-26)
- [`results_export_design_2026-10-29.md`](results_export_design_2026-10-29.md): Results export ("Outputs"): design proposal (2026-10-29) *(proposal, nothing built: exporting results (the blocker of the cut-over))*
- [`step_stats_size_cap_2026-10-26.md`](step_stats_size_cap_2026-10-26.md): Step stats table: the size box seems dead (2026-10-26)
- [`experimental_plan_steps_inventory_2026-10-25.md`](experimental_plan_steps_inventory_2026-10-25.md): Experimental plan steps - what exists, what is missing, what can be reused (2026-10-25)
- [`experimental_plan_bar_design_2026-10-26.md`](experimental_plan_bar_design_2026-10-26.md): Sensorgram "Experimental plan" bar - design proposal (2026-10-26)
- [`plan_steps_as_ranges_2026-10-29.md`](plan_steps_as_ranges_2026-10-29.md): The plan's steps are the app's ranges (2026-10-29): analysis and proposal

## ROIs and groups

The ROI model, the ROI table, arrays, and tuning ROI positions.

- [`roi_system_roadmap.md`](roi_system_roadmap.md): ROI System Roadmap
- [`roi_timeline_design_2026-10-08.md`](roi_timeline_design_2026-10-08.md): ROI geometry timeline (Persistent / Individual scope): design (2026-10-08)
- [`roi_table_audit_2026-10-06.md`](roi_table_audit_2026-10-06.md): ROI / Group table audit: stable app vs rewrite (2026-10-06)
- [`group_table_ordering.md`](group_table_ordering.md): Group table ordering ("#" column, Move Up/Down)
- [`roi_array_section_audit_2026-10-08.md`](roi_array_section_audit_2026-10-08.md): ROI tab "Array" section: audit and proposed design (2026-10-08)
- [`array_spot_size_scan_2026-10-20.md`](array_spot_size_scan_2026-10-20.md): Array detection: spot-size scan (2026-10-20)
- [`auto_crop_lattice_2026-10-21.md`](auto_crop_lattice_2026-10-21.md): Auto crop to the ROI lattice (2026-10-21)
- [`roi_layout_row_2026-10-16.md`](roi_layout_row_2026-10-16.md): ROI layout row under the Cube slider (2026-10-16)
- [`roi_feature_detection_2026-10-29.md`](roi_feature_detection_2026-10-29.md): Finding small features on ROIs, from the image alone (2026-10-29)
- [`roi_position_tuning_plan_2026-10-28.md`](roi_position_tuning_plan_2026-10-28.md): ROI position tuning: build plan (2026-10-28)
- [`roi_position_tuning_2026-10-28.md`](roi_position_tuning_2026-10-28.md): ROI position tuning: data-driven centres from the Sensorgram (design + first real-data results, 2026-10-28)
- [`single_roi_tuning_design_2026-10-28.md`](single_roi_tuning_design_2026-10-28.md): Tuning one ROI: scan modes, a pixel cache around the ROI, and a live preview (design, 2026-10-28)
- [`roi_nudge_partial_scan_proposal_2026-10-26.md`](roi_nudge_partial_scan_proposal_2026-10-26.md): Nudging one ROI: re-scan only what changed (proposal, 2026-10-26)
- [`roi_subset_scan_bottlenecks_2026-10-28.md`](roi_subset_scan_bottlenecks_2026-10-28.md): Scanning one or a few ROIs of an OME-Zarr export: where the time goes now (2026-10-28)
- [`roi_drag_slowdown_2026-10-21.md`](roi_drag_slowdown_2026-10-21.md): ROI drag went slow: Auto crop's readiness check (2026-10-21)
- [`roi_scoped_resample_cv2_fast_path.md`](roi_scoped_resample_cv2_fast_path.md): ROI-scoped resample: cv2 fast path, and exactly what it changes about the data
- [`roi_editor_branch_plan.md`](roi_editor_branch_plan.md): ROI Editor Branch Plan *(historical: an earlier ROI redesign proposal, not the ROI model that shipped)*
- [`roi_implementation_direction.md`](roi_implementation_direction.md): ROI Implementation Direction *(historical: an earlier ROI redesign proposal, not the ROI model that shipped)*
- [`roi_table_direction.md`](roi_table_direction.md): ROI Table Direction *(historical: an earlier ROI redesign proposal, not the ROI model that shipped)*

## Masks

Mask tab tools, mask rows, and the transient scan.

- [`mask_rows_2026-10-16.md`](mask_rows_2026-10-16.md): Mask rows under the Cube and wavelength sliders (2026-10-16)
- [`mask_edit_preview_2026-10-29.md`](mask_edit_preview_2026-10-29.md): Mask-tab edit preview, and Threshold / Local contrast Add / Subtract (2026-10-29)
- [`mask_eraser_2026-10-29.md`](mask_eraser_2026-10-29.md): Mask tab: Eraser (2026-10-29)
- [`area_selection_tool_2026-10-03.md`](area_selection_tool_2026-10-03.md): Area selection tool (rectangle / lasso) - 2026-10-03
- [`auto_transient_mask_exploration_2026-10-16.md`](auto_transient_mask_exploration_2026-10-16.md): Auto mode for the Mask tab: finding flowing particles ("transients")
- [`auto_transient_scan_and_mask_plan_2026-10-16.md`](auto_transient_scan_and_mask_plan_2026-10-16.md): Plan: "Scan for events" and "Auto mask" in the Mask tab (2026-10-16)
- [`transient_scan_automask_slider_audit_2026-10-17.md`](transient_scan_automask_slider_audit_2026-10-17.md): Audit: transient scan, Auto mask, mask export, sliders (2026-10-17)

## Image, rotation, chromatic correction and background

What happens to a picture before it is measured.

- [`rotation_fill_regions_audit_2026-10-03.md`](rotation_fill_regions_audit_2026-10-03.md): Rotation-fill regions: how they are made and how every tool treats them (audit, 2026-10-03)
- [`rotation_nan_phase1_inventory_2026-10-03.md`](rotation_nan_phase1_inventory_2026-10-03.md): Rotation fill → NaN: Phase 1 report (removal inventory + NaN audit), 2026-10-03
- [`rotated_zarr_export_interpolation_2026-10-20.md`](rotated_zarr_export_interpolation_2026-10-20.md): Rotated OME-Zarr export: interpolation measurement (2026-10-20)
- [`chromatic_landmark_lab_findings_2026-10-04.md`](chromatic_landmark_lab_findings_2026-10-04.md): Chromatic landmark lab: findings (2026-10-04)
- [`background_method_study_2026-10-06.md`](background_method_study_2026-10-06.md): Background estimation: speed vs quality study — 2026-10-06
- [`background_drift_measurement_2026-09.md`](background_drift_measurement_2026-09.md): Does a background estimated once stay valid? — measurement, 2026-09-23
- [`row_banding_artifact_analysis_2026-09.md`](row_banding_artifact_analysis_2026-09.md): Row-banding artifact (rolling shutter × PWM illumination): analysis and proposed fix
- [`image_area_slider_redesign.md`](image_area_slider_redesign.md): Image Area: Cube/Wavelength Slider Redesign (2026-08)

## Window, panels and startup

Layout, ribbons, preferences, and what runs on the GUI thread.

- [`panel_layout_workflow_resizable_2026-10-18.md`](panel_layout_workflow_resizable_2026-10-18.md): Panel layout: Workflow resizable, four columns, Image minimum width (2026-10-18)
- [`ribbon_alignment_and_field_style_2026-10-19.md`](ribbon_alignment_and_field_style_2026-10-19.md): Ribbon alignment and field style (2026-10-19)
- [`scrolling_ribbons_plan_2026-10-24.md`](scrolling_ribbons_plan_2026-10-24.md): Panels narrower than their control rows: scrolling ribbons (analysis, 2026-10-24)
- [`preferences_dialog_2026-10-19.md`](preferences_dialog_2026-10-19.md): Edit -> Preferences (2026-10-19)
- [`keyboard_shortcut_registry_design_2026-10-30.md`](keyboard_shortcut_registry_design_2026-10-30.md): Keyboard shortcut registry: one table that binds the keys and builds the list (2026-10-30) *(proposal, no code: waits for the maintainer's go)*
- [`startup_gui_thread_audit_2026-10-24.md`](startup_gui_thread_audit_2026-10-24.md): Startup: what runs on the GUI thread (2026-10-24)
- [`startup_time_2026-10-24.md`](startup_time_2026-10-24.md): Startup time, 2026-10-24: 44.7 s -> 7.1 s, and what is left

## Sessions, export and OME-Zarr

Saving, loading and exporting data; the zarr crash investigation.

- [`session_autosave_blocking_analysis_2026-10-17.md`](session_autosave_blocking_analysis_2026-10-17.md): Session autosave freezes the GUI with hundreds of masks (analysis, 2026-10-17)
- [`session_write_cost_2026-10-21.md`](session_write_cost_2026-10-21.md): The 11-second session write (2026-10-21)
- [`session_load_off_gui_thread_2026-10-22.md`](session_load_off_gui_thread_2026-10-22.md): Session load off the GUI thread, and a faster session carry (2026-10-22)
- [`session_carry_export_2026-10-22.md`](session_carry_export_2026-10-22.md): Session carried along with the OME-Zarr export (2026-10-22)
- [`export_speed_2026-10-22.md`](export_speed_2026-10-22.md): OME-Zarr export speed (2026-10-22)
- [`zarr_chunk_planner_2026-10-19.md`](zarr_chunk_planner_2026-10-19.md): OME-Zarr chunk planner (2026-10-19)
- [`ome_zarr_reexport_and_experimental_data_2026-09.md`](ome_zarr_reexport_and_experimental_data_2026-09.md): OME-Zarr re-export bug + `Experimental_data/` metadata copy (2026-09-27)
- [`imaging_measurement_export_format.md`](imaging_measurement_export_format.md): Imaging Measurement Export/Backup Format
- [`measurement_backup_performance_and_crash_recovery.md`](measurement_backup_performance_and_crash_recovery.md): Measurement backup: crash recovery, the "gets slower over time" investigation, and batched writes
- [`tiff_vs_ome_zarr_read_benchmark.md`](tiff_vs_ome_zarr_read_benchmark.md): TIFF stack vs. OME-Zarr read benchmark: is chunking really the dominant cost?
- [`qthreadpool_zarr_crash_investigation.md`](qthreadpool_zarr_crash_investigation.md): QThreadPool + zarr: root-causing the STATUS_HEAP_CORRUPTION crash, and the fix

## Performance investigations

Measurements of where time or memory went.

- [`image_refresh_performance_analysis_2026-10-11.md`](image_refresh_performance_analysis_2026-10-11.md): Image panel refresh performance analysis (2026-10-11)
- [`play_fast_picture_2026-10-21.md`](play_fast_picture_2026-10-21.md): Play: fast grey picture (2026-10-21)
- [`play_prep_cost_2026-10-21.md`](play_prep_cost_2026-10-21.md): Play at 12-15 frames/s instead of 20-25 (2026-10-21)
- [`testing7_playback_analysis_2026-10-14.md`](testing7_playback_analysis_2026-10-14.md): Slideshow preview test `testing7` - analysis (2026-10-14)
- [`bulk_analysis_performance_investigation.md`](bulk_analysis_performance_investigation.md): Bulk analysis performance investigation: "Start analysis" slowness
- [`live_preview_analysis_slowdown_investigation.md`](live_preview_analysis_slowdown_investigation.md): "Live" preview slows down "Start analysis" - root cause (2026-09, not yet fixed)
- [`roi_mask_cache_memory_investigation.md`](roi_mask_cache_memory_investigation.md): "Start analysis" RAM ballooning to several GB - root cause and fix (2026-09)
- [`image_panel_test_crash_status_2026-10-11.md`](image_panel_test_crash_status_2026-10-11.md): Image-panel test crash: status as of 2026-10-11

## Other

Not sorted into a topic yet.

- [`plim_comparison_notes.md`](plim_comparison_notes.md): plim comparison notes for LSPR imaging evaluation
