from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from lspr_ui import get_active_theme

from lspr_imaging_app.gui.main_window import CollapsibleSection
from lspr_imaging_app.gui.panel_help_registry import panel_help_text


def _visible_tab_stylesheet() -> str:
    return (
        "QTabWidget::pane {"
        "  border: 1px solid #334155;"
        "  border-top: none;"
        "  background: transparent;"
        "}"
        "QTabBar::tab {"
        "  padding: 5px 14px;"
        "  min-width: 64px;"
        "  border: 1px solid #334155;"
        "  border-bottom: none;"
        "  border-radius: 4px 4px 0 0;"
        "  background: #1e293b;"
        "  color: #94a3b8;"
        "  margin-right: 2px;"
        "}"
        "QTabBar::tab:selected {"
        "  background: #0f172a;"
        "  color: #f1f5f9;"
        "  border-color: #475569;"
        "  font-weight: 600;"
        "}"
        "QTabBar::tab:hover:!selected {"
        "  background: #263245;"
        "  color: #cbd5e1;"
        "}"
    )


def _placeholder_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #64748b; font-style: italic;")
    return label


def _nested_section_group(parent: QWidget, *sections: QWidget) -> QWidget:
    """Indent child CollapsibleSections so they read as nested under their
    parent section instead of sitting flush with it."""
    outer = QWidget(parent)
    outer_layout = QVBoxLayout(outer)
    outer_layout.setContentsMargins(16, 2, 0, 2)
    outer_layout.setSpacing(4)
    for section in sections:
        outer_layout.addWidget(section)
    return outer


def _nested_title_color() -> str:
    return get_active_theme().text_dim


def _make_apply_status_label(window: QWidget, target_section: CollapsibleSection, text: str, tooltip: str) -> QToolButton:
    """A small clickable text indicator mirroring target_section's apply state -
    green when applied, red when not, same colors as its link/link-off icon.
    Clicking it toggles the same state the icon controls."""
    theme = get_active_theme()
    button = QToolButton(window)
    button.setText(f"[{text}]")
    button.setAutoRaise(True)
    button.setCheckable(False)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(tooltip)

    def refresh() -> None:
        color = theme.accent_green if target_section.is_applied() else theme.accent_red
        button.setStyleSheet(
            "QToolButton {"
            f"  color: {color};"
            "  background: transparent;"
            "  border: none;"
            "  font-weight: 600;"
            # Same vertical padding as the section title's own toggle button
            # (collapsible_toggle_stylesheet: "padding: 4px 0"), and no font-size
            # override, so this baseline lines up with "Image tools" instead of
            # sitting off-center against it.
            "  padding: 4px 2px;"
            "}"
            "QToolButton:hover { text-decoration: underline; }"
        )

    button.clicked.connect(lambda *_: target_section.set_applied(not target_section.is_applied()))
    # add_applied_listener(), not apply_changed - apply_changed is deliberately
    # suppressed (via blockSignals) while session state is restored at startup,
    # so a purely visual mirror like this one needs the always-fires path or it
    # falls out of sync with the section's own icon after a restore.
    target_section.add_applied_listener(lambda *_: refresh())
    refresh()
    return button


def build_layout(window) -> None:
    top_row_widget = QWidget(window)
    top_row = QHBoxLayout(top_row_widget)
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(4)
    top_row.addWidget(window.open_explorer_button)
    top_row.addWidget(window.folder_edit, 1)
    top_row.addWidget(window.browse_button)

    # "Summary" nested section: structured, paired-row fields. The compact
    # Size/Images/Cubes/WL stats sit on the section's own title row instead
    # (header_extra), same pattern as "Dataset:" and "Image tools:".
    summary_content = QWidget(window)
    summary_content_layout = QVBoxLayout(summary_content)
    summary_content_layout.setContentsMargins(8, 8, 8, 8)
    summary_content_layout.setSpacing(6)
    summary_content_layout.addWidget(window.summary_base_rows_widget)
    summary_content_layout.addWidget(window.summary_ome_zarr_rows_widget)
    window.summary_section = CollapsibleSection(
        "Summary",
        summary_content,
        expanded=True,
        help_text=panel_help_text("summary"),
        title_color=_nested_title_color(),
        header_extra=window.summary_header_stats_label,
        parent=window,
    )

    # "Reference" nested section: title row carries the wavelength/cube
    # indicators (same pattern as "Summary"'s title stats), content below
    # holds the actual mode controls.
    reference_header_row = QWidget(window)
    reference_header_layout = QHBoxLayout(reference_header_row)
    reference_header_layout.setContentsMargins(0, 0, 0, 0)
    reference_header_layout.setSpacing(0)
    reference_header_layout.addWidget(window.reference_wavelength_status_label)
    reference_header_layout.addWidget(QLabel(","))
    reference_header_layout.addSpacing(6)
    reference_header_layout.addWidget(window.reference_spectral_cube_status_label)

    reference_content = QWidget(window)
    reference_layout = QFormLayout(reference_content)
    reference_layout.setContentsMargins(8, 8, 8, 8)
    reference_layout.setHorizontalSpacing(6)
    reference_layout.setVerticalSpacing(4)
    reference_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    reference_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    reference_controls = QHBoxLayout()
    reference_controls.addWidget(window.reference_auto_button)
    reference_controls.addWidget(window.reference_manual_button)
    reference_controls.addSpacing(10)
    reference_controls.addWidget(window.reference_method_status_label)
    reference_controls.addStretch(1)
    reference_layout.addRow("Reference mode", reference_controls)
    window.reference_section = CollapsibleSection(
        "Reference",
        reference_content,
        expanded=True,
        help_text=panel_help_text("reference"),
        title_color=_nested_title_color(),
        header_extra=reference_header_row,
        parent=window,
    )

    # "Export" nested section: the controls that used to live in the
    # "Stack to Zarr" pop-out dialog, now inline instead of a separate window.
    export_content = QWidget(window)
    export_content_layout = QVBoxLayout(export_content)
    export_content_layout.setContentsMargins(8, 8, 8, 8)
    export_content_layout.setSpacing(8)
    for row in (
        window.dataset_ome_zarr_controls_row,
        window.dataset_ome_zarr_options_row,
        window.dataset_ome_zarr_compression_row,
        window.dataset_ome_zarr_skip_excluded_row,
        window.dataset_ome_zarr_export_progress_row,
    ):
        export_content_layout.addWidget(row)
    window.export_section = CollapsibleSection(
        "Export",
        export_content,
        expanded=False,
        help_text=panel_help_text("export"),
        title_color=_nested_title_color(),
        parent=window,
    )

    dataset_inner = QWidget(window)
    dataset_inner_layout = QVBoxLayout(dataset_inner)
    dataset_inner_layout.setContentsMargins(0, 0, 0, 0)
    dataset_inner_layout.setSpacing(4)
    dataset_inner_layout.addWidget(top_row_widget)
    dataset_inner_layout.addWidget(
        _nested_section_group(window, window.summary_section, window.reference_section, window.export_section)
    )

    mask_group = QWidget(window)
    mask_layout = QVBoxLayout(mask_group)
    mask_layout.setContentsMargins(8, 8, 8, 8)
    mask_layout.setSpacing(8)
    mask_create_row = QHBoxLayout()
    mask_create_row.addWidget(window.mask_create_new_button)
    mask_create_row.addWidget(window.mask_load_from_file_button)
    mask_create_row.addWidget(window.mask_save_button)
    mask_create_row.addStretch(1)
    mask_layout.addLayout(mask_create_row)
    histogram_row = QWidget(window)
    histogram_row_layout = QHBoxLayout(histogram_row)
    histogram_row_layout.setContentsMargins(0, 0, 0, 0)
    histogram_row_layout.setSpacing(6)
    histogram_row_layout.addWidget(QLabel("Histogram masking", histogram_row))
    histogram_row_layout.addWidget(window.histogram_mask_apply_button)
    histogram_row_layout.addWidget(window.histogram_mask_reset_button)
    histogram_row_layout.addStretch(1)
    mask_layout.addWidget(histogram_row)
    figure_tools = QWidget(window)
    figure_layout = QFormLayout(figure_tools)
    figure_layout.setContentsMargins(0, 0, 0, 0)
    figure_layout.setHorizontalSpacing(6)
    figure_layout.setVerticalSpacing(4)
    figure_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    figure_layout.addRow("Relative threshold", window._build_relative_mask_row())
    relative_buttons = QHBoxLayout()
    relative_buttons.addWidget(window.relative_mask_apply_button)
    relative_buttons.addWidget(window.relative_mask_reset_button)
    relative_buttons.addWidget(window.relative_mask_show_button)
    relative_buttons.addStretch(1)
    figure_layout.addRow("", relative_buttons)
    figure_layout.addRow("Local contrast", window._build_local_contrast_mask_row())
    local_buttons = QHBoxLayout()
    local_buttons.addWidget(window.local_contrast_mask_apply_button)
    local_buttons.addWidget(window.local_contrast_mask_reset_button)
    local_buttons.addWidget(window.local_contrast_mask_show_button)
    local_buttons.addStretch(1)
    figure_layout.addRow("", local_buttons)
    figure_layout.addRow("Morphology", window._build_morphology_mask_row())
    morph_buttons = QHBoxLayout()
    morph_buttons.addWidget(window.morphology_mask_apply_button)
    morph_buttons.addWidget(window.morphology_mask_reset_button)
    morph_buttons.addWidget(window.morphology_mask_show_button)
    morph_buttons.addStretch(1)
    figure_layout.addRow("", morph_buttons)
    figure_layout.addRow("Drawing", window._build_drawing_mask_row())
    mask_layout.addWidget(figure_tools)

    chromatic_group = QWidget(window)
    chromatic_layout = QFormLayout(chromatic_group)
    chromatic_layout.setContentsMargins(8, 8, 8, 8)
    chromatic_layout.setHorizontalSpacing(6)
    chromatic_layout.setVerticalSpacing(4)
    chromatic_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    refpoints_row = QHBoxLayout()
    refpoints_row.addWidget(QLabel("Spectral"))
    refpoints_row.addWidget(window.chromatic_sample_count_spin)
    refpoints_row.addSpacing(12)
    refpoints_row.addWidget(QLabel("Spacial"))
    refpoints_row.addWidget(window.chromatic_feature_count_spin)
    refpoints_row.addStretch(1)
    chromatic_layout.addRow("Ref.points", refpoints_row)
    refpoints_row2 = QHBoxLayout()
    refpoints_row2.addWidget(QLabel("Sub.px"))
    refpoints_row2.addWidget(window.chromatic_subpixel_precision_combo)
    refpoints_row2.addSpacing(12)
    refpoints_row2.addWidget(QLabel("Track"))
    refpoints_row2.addWidget(window.chromatic_landmark_kind_combo)
    refpoints_row2.addSpacing(12)
    refpoints_row2.addWidget(QLabel("Model"))
    refpoints_row2.addWidget(window.chromatic_landmark_model_combo)
    refpoints_row2.addStretch(1)
    chromatic_layout.addRow("", refpoints_row2)
    action_row = QHBoxLayout()
    action_row.addWidget(window.chromatic_start_button)
    action_row.addWidget(window.chromatic_grid_button)
    action_row.addWidget(window.chromatic_grid_reset_button)
    action_row.addWidget(window.chromatic_prev_button)
    action_row.addWidget(window.chromatic_next_button)
    action_row.addWidget(QLabel("Ref.point"))
    action_row.addWidget(window.chromatic_landmark_id_spin)
    action_row.addWidget(window.chromatic_landmark_clear_button)
    action_row.addStretch(1)
    chromatic_layout.addRow("Editing", action_row)
    transform_row = QHBoxLayout()
    transform_row.addWidget(window.chromatic_auto_button)
    transform_row.addWidget(window.chromatic_reference_points_all_button)
    transform_row.addWidget(window.chromatic_transform_button)
    transform_row.addWidget(window.chromatic_landmark_export_button)
    transform_row.addStretch(1)
    chromatic_layout.addRow("", transform_row)
    chromatic_layout.addRow("", window.chromatic_apply_check)
    chromatic_layout.addRow("Status", window.chromatic_summary)
    chromatic_layout.addRow("Progress", window.chromatic_progress_label)

    circle_editor_group = QWidget(window)
    circle_editor_layout = QFormLayout(circle_editor_group)
    circle_editor_layout.setContentsMargins(8, 8, 8, 8)
    circle_editor_layout.setHorizontalSpacing(6)
    circle_editor_layout.setVerticalSpacing(4)
    circle_editor_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    circle_editor_layout.addRow("", window._make_section_separator())
    circle_editor_layout.addRow("Sample diameter", window._build_roi_geometry_row())
    circle_editor_layout.addRow("Reference ring", window._build_reference_row())
    circle_editor_layout.addRow("Areas", window.roi_geometry_area_label)
    circle_editor_layout.addRow("", window._make_section_separator())
    detection_buttons = QHBoxLayout()
    detection_buttons.setContentsMargins(0, 0, 0, 0)
    detection_buttons.setSpacing(4)
    detection_buttons.addWidget(window.detect_rois_button)
    detection_buttons.addWidget(window.roi_corner_select_button)
    detection_buttons.addWidget(window.reorder_rois_button)
    detection_buttons.addWidget(window.clear_rois_button)
    detection_buttons.addStretch(1)
    circle_editor_layout.addRow("", detection_buttons)
    circle_editor_layout.addRow("Result", window.roi_summary)

    rectangle_editor_group = QWidget(window)
    rectangle_editor_layout = QVBoxLayout(rectangle_editor_group)
    rectangle_editor_layout.setContentsMargins(12, 12, 12, 12)
    rectangle_editor_layout.addWidget(_placeholder_label("Rectangle ROI editing — coming soon."))
    rectangle_editor_layout.addStretch(1)

    freehand_editor_group = QWidget(window)
    freehand_editor_layout = QVBoxLayout(freehand_editor_group)
    freehand_editor_layout.setContentsMargins(12, 12, 12, 12)
    freehand_editor_layout.addWidget(_placeholder_label("Polygon / freehand ROI editing — coming soon."))
    freehand_editor_layout.addStretch(1)

    window.roi_editor_tabs = QTabWidget(window)
    window.roi_editor_tabs.setTabPosition(QTabWidget.TabPosition.North)
    window.roi_editor_tabs.setMovable(False)
    window.roi_editor_tabs.setStyleSheet(_visible_tab_stylesheet())
    window.roi_editor_tabs.addTab(circle_editor_group, "Circles")
    window.roi_editor_tabs.addTab(rectangle_editor_group, "Rectangles")
    window.roi_editor_tabs.addTab(freehand_editor_group, "Freehand")
    window.roi_editor_tabs.currentChanged.connect(window._on_roi_editor_tab_changed)
    window.roi_editor_tabs.setCurrentIndex(0)

    roi_editor_content = QWidget(window)
    roi_editor_content_layout = QVBoxLayout(roi_editor_content)
    roi_editor_content_layout.setContentsMargins(4, 2, 4, 2)
    roi_editor_content_layout.setSpacing(4)
    window.left_roi_editor_row = QWidget(roi_editor_content)
    window.left_roi_editor_layout = QHBoxLayout(window.left_roi_editor_row)
    window.left_roi_editor_layout.setContentsMargins(0, 0, 0, 0)
    window.left_roi_editor_layout.setSpacing(8)
    roi_editor_content_layout.addWidget(window.left_roi_editor_row)
    roi_editor_content_layout.addWidget(window._build_array_row())
    roi_editor_content_layout.addWidget(window.roi_editor_tabs)

    background_group = QWidget(window)
    background_layout = QFormLayout(background_group)
    background_layout.setContentsMargins(8, 8, 8, 8)
    background_layout.setHorizontalSpacing(6)
    background_layout.setVerticalSpacing(4)
    background_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    background_file_row = QHBoxLayout()
    background_file_row.setContentsMargins(0, 0, 0, 0)
    background_file_row.setSpacing(4)
    background_file_row.addWidget(window.background_create_new_button)
    background_file_row.addWidget(window.background_load_from_file_button)
    background_file_row.addWidget(window.background_save_button)
    background_file_row.addStretch(1)
    background_layout.addRow("", background_file_row)
    background_row = QHBoxLayout()
    background_row.setContentsMargins(0, 0, 0, 0)
    background_row.setSpacing(6)
    background_row.addWidget(QLabel("Sigma"))
    background_row.addWidget(window.background_smoothing_sigma_spin)
    background_row.addSpacing(10)
    background_row.addWidget(QLabel("Bin"))
    background_row.addWidget(window.background_smoothing_binning_combo)
    background_row.addSpacing(10)
    background_row.addWidget(window.background_ignore_roi_button)
    background_row.addWidget(window.background_ignore_mask_button)
    background_row.addSpacing(10)
    background_row.addWidget(QLabel("Grow excl."))
    background_row.addWidget(window.background_exclusion_dilation_spin)
    background_row.addWidget(window.background_local_reference_check)
    background_row.addWidget(window.background_profile_button)
    background_row.addStretch(1)
    background_layout.addRow("Background", background_row)

    # Top row: controls that scope *which* cubes/ROIs get computed. Stays above
    # the nested Spectra fitting / Statistics sections below it.
    analysis_top_widget = QWidget(window)
    analysis_top_layout = QFormLayout(analysis_top_widget)
    analysis_top_layout.setContentsMargins(8, 8, 8, 4)
    analysis_top_layout.setHorizontalSpacing(6)
    analysis_top_layout.setVerticalSpacing(4)
    analysis_top_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    analysis_scope_row = QHBoxLayout()
    analysis_scope_row.addWidget(window.analysis_roi_table_button)
    analysis_scope_row.addWidget(window.analysis_refresh_button)
    analysis_scope_row.addWidget(window.analysis_calculate_all_button)
    analysis_scope_row.addWidget(window.analysis_stop_button)
    analysis_scope_row.addWidget(window.analysis_preview_button)
    analysis_scope_row.addStretch(1)
    analysis_top_layout.addRow("Selection", analysis_scope_row)
    analysis_range_row = QHBoxLayout()
    analysis_range_row.setContentsMargins(0, 0, 0, 0)
    analysis_range_row.setSpacing(6)
    analysis_range_row.addWidget(QLabel("Start"))
    analysis_range_row.addWidget(window.analysis_start_spectral_cube_spin)
    analysis_range_row.addSpacing(10)
    analysis_range_row.addWidget(QLabel("End"))
    analysis_range_row.addWidget(window.analysis_end_spectral_cube_spin)
    analysis_range_row.addStretch(1)
    analysis_top_layout.addRow("Spectral cubes", analysis_range_row)

    # "Spectra fitting" nested section: the metric/formula definition itself.
    analysis_fitting_content = QWidget(window)
    analysis_fitting_layout = QFormLayout(analysis_fitting_content)
    analysis_fitting_layout.setContentsMargins(8, 8, 8, 8)
    analysis_fitting_layout.setHorizontalSpacing(6)
    analysis_fitting_layout.setVerticalSpacing(4)
    analysis_fitting_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    analysis_fit_row = QHBoxLayout()
    analysis_fit_row.setContentsMargins(0, 0, 0, 0)
    analysis_fit_row.setSpacing(6)
    analysis_fit_row.addWidget(QLabel("Order"))
    analysis_fit_row.addWidget(window.analysis_poly_order_spin)
    analysis_fit_row.addSpacing(10)
    analysis_fit_row.addWidget(QLabel("Metric"))
    analysis_fit_row.addWidget(window.analysis_metric_combo)
    analysis_fit_row.addStretch(1)
    analysis_fitting_layout.addRow("Fit", analysis_fit_row)
    analysis_fitting_layout.addRow("Formula", window.analysis_formula_label)
    analysis_fitting_layout.addRow("Result", window.analysis_summary_label)

    # "Statistics" nested section: averaging/smoothing controls - not designed yet.
    analysis_statistics_content = QWidget(window)
    analysis_statistics_layout = QVBoxLayout(analysis_statistics_content)
    analysis_statistics_layout.setContentsMargins(12, 12, 12, 12)
    analysis_statistics_layout.addWidget(
        _placeholder_label("Statistics tools (averaging, smoothing, axis selection) — coming soon.")
    )
    analysis_statistics_layout.addStretch(1)

    window.image_toolbar = QWidget(window)
    window.image_toolbar.setObjectName("imageToolbar")
    window.dataset_section = CollapsibleSection(
        "Dataset:",
        dataset_inner,
        expanded=True,
        help_text=panel_help_text("dataset"),
        header_extra=window.dataset_type_indicator_row,
        parent=window,
    )
    window.chromatic_section = CollapsibleSection(
        "Chromatic correction",
        chromatic_group,
        expanded=False,
        applied=bool(window._state.preprocessing.chromatic_correction_enabled),
        apply_tooltip="Apply or skip the stored chromatic transform models for display and processing.",
        help_text=panel_help_text("chromatic"),
        title_color=_nested_title_color(),
        parent=window,
    )
    window.mask_section = CollapsibleSection(
        "Mask",
        mask_group,
        expanded=True,
        applied=bool(window._state.area_roi_settings.ignore_marked_pixels),
        apply_tooltip="Apply or skip mask-based exclusions during image display and processing.",
        help_text=panel_help_text("mask"),
        title_color=_nested_title_color(),
        parent=window,
    )
    window.background_section = CollapsibleSection(
        "Background removal",
        background_group,
        expanded=True,
        applied=bool(window._state.preprocessing.flatten_background_enabled),
        apply_tooltip="Apply or skip background removal from the processing pipeline.",
        help_text=panel_help_text("background"),
        title_color=_nested_title_color(),
        parent=window,
    )

    # "Transforms" nests the icon row (rotate/crop/measure) as a child section,
    # a sibling of Mask / Chromatic correction / Background removal, all under
    # "Image tools" - which is now just their parent, not a control itself.
    window.transforms_section = CollapsibleSection(
        "Transforms",
        window.image_toolbar,
        expanded=True,
        applied=bool(window._state.preprocessing.image_tools_enabled),
        apply_tooltip="Link image tools to the processed image. Off means preview only; on means recalculate downstream views.",
        help_text=panel_help_text("transforms"),
        title_color=_nested_title_color(),
        parent=window,
    )
    image_tools_inner = QWidget(window)
    image_tools_inner_layout = QVBoxLayout(image_tools_inner)
    image_tools_inner_layout.setContentsMargins(0, 0, 0, 0)
    image_tools_inner_layout.setSpacing(4)
    image_tools_inner_layout.addWidget(
        _nested_section_group(
            window, window.transforms_section, window.mask_section, window.chromatic_section, window.background_section
        )
    )

    # Header status row: click any bracketed label to toggle that stage's apply
    # state - same green/red as its own link/link-off icon, just visible at a
    # glance from the "Image tools" header without opening each nested section.
    image_tools_status_row = QWidget(window)
    image_tools_status_layout = QHBoxLayout(image_tools_status_row)
    image_tools_status_layout.setContentsMargins(0, 0, 0, 0)
    image_tools_status_layout.setSpacing(2)
    image_tools_status_layout.addWidget(
        _make_apply_status_label(
            window, window.transforms_section, "Transforms", "Toggle whether image-tool transforms are linked to the processed image."
        )
    )
    image_tools_status_layout.addWidget(
        _make_apply_status_label(window, window.mask_section, "Mask", "Toggle whether mask-based exclusions are applied.")
    )
    image_tools_status_layout.addWidget(
        _make_apply_status_label(
            window, window.chromatic_section, "Chrom. correct.", "Toggle whether the stored chromatic transform is applied."
        )
    )
    image_tools_status_layout.addWidget(
        _make_apply_status_label(
            window, window.background_section, "BG removal", "Toggle whether background removal is applied."
        )
    )

    # "Image tools" is now a pure parent/grouping header: no apply toggle, help,
    # or pin of its own - those moved to "Transforms" above, which is what they
    # actually described (linking the icon-row tools to the processed image).
    window.image_tools_section = CollapsibleSection(
        "Image tools:",
        image_tools_inner,
        expanded=True,
        show_help=False,
        show_pin=False,
        header_extra=image_tools_status_row,
        parent=window,
    )

    window.roi_editor_section = CollapsibleSection(
        "ROI editor",
        roi_editor_content,
        expanded=True,
        applied=bool(window._read_bool_setting("controls/live_geometry", False)),
        apply_tooltip="Apply live ROI geometry recalculation while editing.",
        help_text=panel_help_text("roi_editor"),
        parent=window,
    )

    # "Analysis" nests Spectra fitting / Statistics as child sections
    # underneath the scope/range controls that stay pinned above them.
    window.spectra_fitting_section = CollapsibleSection(
        "Spectra fitting",
        analysis_fitting_content,
        expanded=True,
        help_text=panel_help_text("spectra_fitting"),
        title_color=_nested_title_color(),
        parent=window,
    )
    window.statistics_section = CollapsibleSection(
        "Statistics",
        analysis_statistics_content,
        expanded=False,
        help_text=panel_help_text("statistics"),
        title_color=_nested_title_color(),
        parent=window,
    )
    analysis_inner = QWidget(window)
    analysis_inner_layout = QVBoxLayout(analysis_inner)
    analysis_inner_layout.setContentsMargins(0, 0, 0, 0)
    analysis_inner_layout.setSpacing(4)
    analysis_inner_layout.addWidget(analysis_top_widget)
    analysis_inner_layout.addWidget(
        _nested_section_group(window, window.spectra_fitting_section, window.statistics_section)
    )
    window.analysis_section = CollapsibleSection(
        "Analysis",
        analysis_inner,
        expanded=True,
        applied=bool(window._analysis_enabled),
        apply_tooltip="Enable or disable analysis calculations.",
        help_text=panel_help_text("analysis"),
        parent=window,
    )

    # "Results / Export": sensorgram region/measurement tools and export of
    # ROI values, spectra, and sensorgram metrics - not designed yet.
    results_export_content = QWidget(window)
    results_export_layout = QVBoxLayout(results_export_content)
    results_export_layout.setContentsMargins(12, 12, 12, 12)
    results_export_layout.addWidget(
        _placeholder_label(
            "Sensorgram measurement (regions, averages, kinetics/slopes, step comparison) "
            "and export of ROI values, spectra, and sensorgram metrics — coming soon."
        )
    )
    results_export_layout.addStretch(1)
    window.results_export_section = CollapsibleSection(
        "Results / Export",
        results_export_content,
        expanded=False,
        help_text=panel_help_text("results_export"),
        parent=window,
    )

    window._bind_collapsible_group(
        [
            window.dataset_section,
            window.summary_section,
            window.reference_section,
            window.export_section,
            window.image_tools_section,
            window.transforms_section,
            window.mask_section,
            window.chromatic_section,
            window.background_section,
            window.roi_editor_section,
            window.analysis_section,
            window.spectra_fitting_section,
            window.statistics_section,
            window.results_export_section,
        ]
    )

    window.chromatic_apply_check.hide()
    window.ignore_marked_check.hide()
    window.left_tabs = QTabWidget(window)
    window.left_tabs.setTabPosition(QTabWidget.TabPosition.North)
    window.left_tabs.setMovable(False)
    window.left_tabs.setDocumentMode(True)
    window.left_tabs.setElideMode(Qt.TextElideMode.ElideRight)
    window.left_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    window.left_tabs.setMinimumWidth(340)
    window.left_tabs.setStyleSheet("QTabWidget::pane { border: 0; } QTabBar::tab { min-width: 0; padding: 0; margin: 0; border: 0; background: transparent; }")
    window.left_tabs.addTab(
        window._make_left_tab_page(
            window.dataset_section,
            window.image_tools_section,
            window.roi_editor_section,
            window.analysis_section,
            window.results_export_section,
        ),
        "ROI controls",
    )
    window.left_tabs.tabBar().hide()
    window.left_tabs.currentChanged.connect(window._save_layout_preferences)

    histogram_content = QWidget(window)
    histogram_layout = QVBoxLayout(histogram_content)
    histogram_layout.setContentsMargins(8, 8, 8, 8)
    histogram_layout.addWidget(window.histogram_plot)
    histogram_controls = QHBoxLayout()
    histogram_controls.setContentsMargins(0, 1, 0, 0)
    histogram_controls.setSpacing(8)
    histogram_controls.addStretch(1)
    histogram_mini_label = QLabel("Bin", histogram_content)
    histogram_mini_label.setObjectName("toolbarMiniLabel")
    histogram_controls.addWidget(histogram_mini_label)
    window.histogram_bins_spin.setFixedWidth(76)
    histogram_controls.addWidget(window.histogram_bins_spin)
    histogram_controls.addWidget(window.histogram_y_scale_button)
    histogram_layout.addLayout(histogram_controls)

    spectra_content = QWidget(window)
    spectrum_layout = QVBoxLayout(spectra_content)
    spectrum_layout.setContentsMargins(8, 8, 8, 8)
    spectrum_layout.setSpacing(6)
    spectrum_layout.addWidget(window.spectrum_summary_label)
    spectrum_layout.addWidget(window.spectrum_plot)

    sensorgram_content = QWidget(window)
    sensorgram_layout = QVBoxLayout(sensorgram_content)
    sensorgram_layout.setContentsMargins(8, 8, 8, 8)
    sensorgram_layout.setSpacing(6)
    sensorgram_layout.addWidget(window.sensorgram_summary_label)
    sensorgram_layout.addWidget(window.sensorgram_plot)

    window.bottom_view_toolbar = QWidget(window)
    window.bottom_view_toolbar.setObjectName("bottomViewToolbar")

    image_slicer_row = QWidget(window)
    image_slicer_layout = QHBoxLayout(image_slicer_row)
    image_slicer_layout.setContentsMargins(0, 0, 0, 0)
    image_slicer_layout.setSpacing(6)
    slicer_mini_label_width = 36
    spectral_cube_mini_label = QLabel("Cube", image_slicer_row)
    spectral_cube_mini_label.setObjectName("toolbarMiniLabel")
    spectral_cube_mini_label.setFixedWidth(slicer_mini_label_width)
    spectral_cube_mini_label.setToolTip("Spectral cube index")
    image_slicer_layout.addWidget(spectral_cube_mini_label)
    image_slicer_layout.addWidget(window.spectral_cube_slider, 1)
    image_slicer_layout.addWidget(window.spectral_cube_spin)
    wavelength_mini_label = QLabel("λ", image_slicer_row)
    wavelength_mini_label.setObjectName("toolbarMiniLabel")
    wavelength_mini_label.setFixedWidth(slicer_mini_label_width)
    wavelength_mini_label.setToolTip("Wavelength")
    image_slicer_layout.addWidget(wavelength_mini_label)
    image_slicer_layout.addWidget(window.wavelength_slider, 1)
    image_slicer_layout.addWidget(window.wavelength_spin)
    image_slicer_layout.addWidget(window.reference_jump_button)
    image_slicer_layout.addWidget(window.image_exclusion_button)

    image_tools_panel = QWidget(window)
    image_tools_layout = QVBoxLayout(image_tools_panel)
    image_tools_layout.setContentsMargins(0, 0, 0, 0)
    image_tools_layout.setSpacing(4)
    image_tools_layout.addWidget(window.image_name_label)
    image_tools_layout.addWidget(window.image_view, 1)
    image_tools_layout.addWidget(window.bottom_view_toolbar)
    image_tools_layout.addWidget(image_slicer_row)

    window.roi_table = QTableWidget(window)
    window.roi_table.setColumnCount(9)
    window.roi_table.setHorizontalHeaderLabels(["ID", "Group", "C_c", "C_r", "D_s", "d_r", "D_r", "x", "y"])
    window.roi_table.setAlternatingRowColors(False)
    window.roi_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    window.roi_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
    window.roi_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
    window.roi_table.setWordWrap(False)
    window.roi_table.verticalHeader().setVisible(False)
    window.roi_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
    window.roi_table.horizontalHeader().setStretchLastSection(True)
    window.roi_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    window.roi_table.horizontalHeader().setSortIndicatorShown(True)
    window.roi_table.setSortingEnabled(True)
    window.roi_table.setShowGrid(False)
    table_font = window.roi_table.font()
    table_font.setPointSize(8)
    window.roi_table.setFont(table_font)
    window.roi_table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    window.roi_table.setMaximumWidth(16777215)
    window.roi_table.setMinimumWidth(220)
    window.roi_table.installEventFilter(window)
    window.roi_table.viewport().installEventFilter(window)
    window._set_help(
        window.roi_table,
        "ROI table keyboard shortcuts:\n"
        "PageUp / PageDown: Move selected ROIs in the table\n"
        "Delete / Backspace: Remove selected ROIs\n"
        "Ctrl+C: Copy selected ROI properties\n"
        "Ctrl+V: Paste copied ROI properties\n"
        "Double-click: Edit the clicked ROI field\n"
        "Use the table selection to choose one or more ROIs before copying or moving.",
    )
    roi_list_panel = QWidget(window)
    window.roi_list_io_layout = QVBoxLayout(roi_list_panel)
    window.roi_list_io_layout.setContentsMargins(0, 0, 0, 0)
    window.roi_list_io_layout.setSpacing(4)
    window.roi_list_io_layout.addWidget(window.roi_table)
    roi_list_io_row = QHBoxLayout()
    roi_list_io_row.setContentsMargins(0, 0, 0, 0)
    roi_list_io_row.setSpacing(4)
    window.roi_list_cached_button = window._make_icon_tool_button(
        "database",
        "#22c55e",
        "Show only cached ROIs in the image overlay.",
        checkable=True,
        icon=window._make_cached_rois_icon(window._cached_rois_only_visible),
    )
    window.roi_list_cached_button.setChecked(window._cached_rois_only_visible)
    window.roi_export_button = window._make_icon_tool_button(
        "file-export", "#22c55e", "Save a named backup of the ROI table (JSON). The live table is already saved automatically as you edit."
    )
    window.roi_import_button = window._make_icon_tool_button(
        "file-import", "#38bdf8", "Load a saved ROI table (JSON), replacing the current one."
    )
    roi_list_io_row.addWidget(window.roi_list_cached_button)
    roi_list_io_row.addStretch(1)
    roi_list_io_row.addWidget(window.roi_export_button)
    roi_list_io_row.addWidget(window.roi_import_button)
    window.roi_list_io_layout.addLayout(roi_list_io_row)
    window._apply_roi_table_style()

    workflow_content = QWidget(window)
    workflow_content_layout = QVBoxLayout(workflow_content)
    workflow_content_layout.setContentsMargins(0, 0, 0, 0)
    workflow_content_layout.setSpacing(4)
    workflow_content_layout.addWidget(window.left_tabs, 1)

    workflow_log_content = QWidget(workflow_content)
    workflow_log_content_layout = QVBoxLayout(workflow_log_content)
    workflow_log_content_layout.setContentsMargins(0, 0, 0, 0)
    workflow_log_content_layout.setSpacing(4)
    workflow_log_row = QHBoxLayout()
    workflow_log_row.setContentsMargins(0, 0, 0, 0)
    workflow_log_row.setSpacing(4)
    workflow_log_label = QLabel("Console", workflow_log_content)
    workflow_log_label.setObjectName("toolbarMiniLabel")
    window._workflow_log_autoscroll_enabled = True
    window.workflow_log_autoscroll_button = window._make_icon_tool_button("arrow-down", "#38bdf8", "Auto-scroll the workflow log to the newest entry.", checkable=True)
    window.workflow_log_autoscroll_button.setChecked(True)
    window.workflow_log_autoscroll_button.toggled.connect(window._set_workflow_log_autoscroll_enabled)
    window.workflow_log_copy_button = window._make_icon_tool_button("copy", "#38bdf8", "Copy the workflow log to the clipboard.")
    window.workflow_log_copy_button.clicked.connect(window._copy_workflow_log)
    window.workflow_log_clear_button = window._make_icon_tool_button("trash-2", "#ef4444", "Clear the workflow log.")
    window.workflow_log_clear_button.clicked.connect(lambda *_: window.workflow_log_view.clear())
    workflow_log_row.addWidget(workflow_log_label, 0)
    workflow_log_row.addStretch(1)
    workflow_log_row.addWidget(window.workflow_log_autoscroll_button, 0)
    workflow_log_row.addWidget(window.workflow_log_copy_button, 0)
    workflow_log_row.addWidget(window.workflow_log_clear_button, 0)
    workflow_log_content_layout.addLayout(workflow_log_row)
    window.workflow_log_view = QTextEdit(workflow_log_content)
    window.workflow_log_view.setReadOnly(True)
    window.workflow_log_view.setAcceptRichText(True)
    window.workflow_log_view.document().setMaximumBlockCount(1000)
    window.workflow_log_view.setPlaceholderText("Debug output, errors, and analysis timing appear here.")
    window.workflow_log_view.setMinimumHeight(120)
    window.workflow_log_view.setTabChangesFocus(True)
    window.workflow_log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    window.workflow_log_view.setFont(QFont("Consolas", 9))
    window.workflow_log_view.setStyleSheet("QTextEdit { background: #0b1220; color: #cbd5e1; border: 1px solid #243041; border-radius: 6px; }")
    window.workflow_log_view.installEventFilter(window)
    workflow_log_content_layout.addWidget(window.workflow_log_view, 0)
    window.workflow_log_section = CollapsibleSection(
        "Logs",
        workflow_log_content,
        expanded=False,
        help_text=panel_help_text("logs"),
        parent=workflow_content,
    )
    workflow_content_layout.addWidget(window.workflow_log_section, 0)
    window.workflow_panel = window._create_panel_container("Workflow", workflow_content, panel_name="workflowPanel")
    window.workflow_panel.setMinimumWidth(340)
    window.roi_list_panel = window._create_panel_container("ROI table", roi_list_panel, panel_name="roiListPanel")
    window.roi_list_panel.setMinimumWidth(240)
    window.image_panel = window._create_panel_container("Image area", image_tools_panel, panel_name="imageAreaPanel")
    window.image_panel.setMinimumWidth(360)
    window.histogram_panel = window._create_panel_container("Histogram", histogram_content, panel_name="histogramPanel")
    window.histogram_panel.setMinimumWidth(240)
    window.spectra_panel = window._create_panel_container("Spectra", spectra_content, panel_name="spectraPanel")
    window.spectra_panel.setMinimumWidth(320)
    window.sensorgram_panel = window._create_panel_container("Sensorgram", sensorgram_content, panel_name="sensorgramPanel")
    window.sensorgram_panel.setMinimumWidth(320)
    for panel in (window.workflow_panel, window.roi_list_panel, window.image_panel, window.histogram_panel, window.spectra_panel, window.sensorgram_panel):
        panel.visibilityChanged.connect(lambda _visible, target=panel: window._on_panel_visibility_changed(target))
    window._restore_default_panel_layout()
