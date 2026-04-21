from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QStyle,
)

from lspr_imaging_app.gui.main_window import CollapsibleSection
from lspr_imaging_app.gui.panel_help_registry import panel_help_text


def build_layout(window) -> None:
    top_row_widget = QWidget(window)
    top_row = QHBoxLayout(top_row_widget)
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(4)
    folder_label = QLabel(window)
    folder_label.setPixmap(window.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(16, 16))
    folder_label.setToolTip("Dataset folder")
    top_row.addWidget(folder_label)
    top_row.addWidget(window.folder_edit, 1)
    top_row.addWidget(window.browse_button)
    top_row.addWidget(window.load_button)

    dataset_group = QWidget(window)
    dataset_layout = QFormLayout(dataset_group)
    dataset_layout.setContentsMargins(8, 8, 8, 8)
    dataset_layout.setHorizontalSpacing(6)
    dataset_layout.setVerticalSpacing(4)
    dataset_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    dataset_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    dataset_layout.insertRow(0, top_row_widget)
    summary_block = QWidget(window)
    summary_block_layout = QVBoxLayout(summary_block)
    summary_block_layout.setContentsMargins(0, 0, 0, 0)
    summary_block_layout.setSpacing(2)
    summary_block_layout.addWidget(window.dataset_summary)
    summary_block_layout.addWidget(window.dataset_stack_widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    dataset_layout.addRow("Summary", summary_block)
    dataset_layout.addRow("Stack to Zarr", window.dataset_ome_zarr_controls_row)
    dataset_layout.addRow("", window.dataset_ome_zarr_options_row)
    dataset_layout.addRow("", window.dataset_ome_zarr_compression_row)
    dataset_layout.addRow("", window.dataset_ome_zarr_info_row)
    dataset_layout.addRow("", window.dataset_ome_zarr_export_progress_row)
    reference_row = QHBoxLayout()
    reference_row.addWidget(window.reference_summary, 1)
    dataset_layout.addRow("Reference", reference_row)
    reference_controls = QHBoxLayout()
    reference_controls.addWidget(window.reference_auto_button)
    reference_controls.addWidget(window.reference_manual_button)
    reference_controls.addStretch(1)
    dataset_layout.addRow("Reference mode", reference_controls)
    reference_status_row = QHBoxLayout()
    reference_status_row.setContentsMargins(0, 0, 0, 0)
    reference_status_row.setSpacing(10)
    reference_status_row.addWidget(window.reference_wavelength_status_label)
    reference_status_row.addWidget(window.reference_frame_status_label)
    reference_status_row.addWidget(window.reference_method_status_label)
    reference_status_row.addStretch(1)
    dataset_layout.addRow("Reference info", reference_status_row)
    slider_group = QGroupBox("Dataset slicer", dataset_group)
    slider_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    slider_layout = QVBoxLayout(slider_group)
    slicer_label_width = 78
    frame_row = QHBoxLayout()
    frame_label = QLabel("Frame", slider_group)
    frame_label.setFixedWidth(slicer_label_width)
    frame_row.addWidget(frame_label)
    frame_row.addWidget(window.frame_slider, 1)
    frame_row.addWidget(window.frame_spin)
    wavelength_row = QHBoxLayout()
    wavelength_label = QLabel("Wavelength", slider_group)
    wavelength_label.setFixedWidth(slicer_label_width)
    wavelength_row.addWidget(wavelength_label)
    wavelength_row.addWidget(window.wavelength_slider, 1)
    wavelength_row.addWidget(window.wavelength_spin)
    slider_layout.addLayout(frame_row)
    slider_layout.addLayout(wavelength_row)
    dataset_layout.addRow(slider_group)

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
    refpoints_row.addSpacing(12)
    refpoints_row.addWidget(QLabel("Sub.px"))
    refpoints_row.addWidget(window.chromatic_subpixel_precision_combo)
    refpoints_row.addStretch(1)
    refpoints_row.addWidget(window.chromatic_auto_button)
    refpoints_row.addWidget(window.chromatic_reference_points_all_button)
    chromatic_layout.addRow("Ref.points", refpoints_row)
    action_row = QHBoxLayout()
    action_row.addWidget(window.chromatic_start_button)
    action_row.addWidget(window.chromatic_prev_button)
    action_row.addWidget(window.chromatic_next_button)
    action_row.addWidget(QLabel("Ref.point"))
    action_row.addWidget(window.chromatic_landmark_id_spin)
    action_row.addWidget(window.chromatic_landmark_clear_button)
    action_row.addStretch(1)
    chromatic_layout.addRow("Editing", action_row)
    chromatic_layout.addRow("", window.chromatic_transform_button)
    chromatic_layout.addRow("", window.chromatic_apply_check)
    chromatic_layout.addRow("Status", window.chromatic_summary)
    chromatic_layout.addRow("Progress", window.chromatic_progress_label)

    spot_editor_group = QWidget(window)
    spot_editor_layout = QFormLayout(spot_editor_group)
    spot_editor_layout.setContentsMargins(8, 8, 8, 8)
    spot_editor_layout.setHorizontalSpacing(6)
    spot_editor_layout.setVerticalSpacing(4)
    spot_editor_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    window.left_spot_editor_row = QWidget(spot_editor_group)
    window.left_spot_editor_layout = QHBoxLayout(window.left_spot_editor_row)
    window.left_spot_editor_layout.setContentsMargins(0, 0, 0, 0)
    window.left_spot_editor_layout.setSpacing(8)
    spot_editor_layout.addRow("", window._make_section_separator())
    spot_editor_layout.addRow("Tools", window.left_spot_editor_row)
    spot_editor_layout.addRow("Spot diameter", window._build_spot_geometry_row())
    spot_editor_layout.addRow("Reference ring", window._build_ring_row())
    spot_editor_layout.addRow("Areas", window.spot_geometry_area_label)
    spot_editor_layout.addRow("", window._make_section_separator())
    spot_editor_layout.addRow("Array", window._build_array_row())
    detection_buttons = QHBoxLayout()
    detection_buttons.setContentsMargins(0, 0, 0, 0)
    detection_buttons.setSpacing(4)
    detection_buttons.addWidget(window.detect_spots_button)
    detection_buttons.addWidget(window.spot_corner_select_button)
    detection_buttons.addWidget(window.reorder_spots_button)
    detection_buttons.addWidget(window.clear_spots_button)
    detection_buttons.addStretch(1)
    spot_editor_layout.addRow("", detection_buttons)
    spot_editor_layout.addRow("Result", window.spot_summary)

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
    background_row.addWidget(window.background_ignore_spot_button)
    background_row.addWidget(window.background_ignore_mask_button)
    background_row.addWidget(window.background_profile_button)
    background_row.addStretch(1)
    background_layout.addRow("Background", background_row)

    analysis_group = QWidget(window)
    analysis_layout = QFormLayout(analysis_group)
    analysis_layout.setContentsMargins(8, 8, 8, 8)
    analysis_layout.setHorizontalSpacing(6)
    analysis_layout.setVerticalSpacing(4)
    analysis_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    analysis_scope_row = QHBoxLayout()
    analysis_scope_row.addWidget(window.analysis_spot_table_button)
    analysis_scope_row.addWidget(window.analysis_refresh_button)
    analysis_scope_row.addWidget(window.analysis_calculate_all_button)
    analysis_scope_row.addWidget(window.analysis_stop_button)
    analysis_scope_row.addWidget(window.analysis_preview_button)
    analysis_scope_row.addStretch(1)
    analysis_layout.addRow("Selection", analysis_scope_row)
    analysis_fit_row = QHBoxLayout()
    analysis_fit_row.setContentsMargins(0, 0, 0, 0)
    analysis_fit_row.setSpacing(6)
    analysis_fit_row.addWidget(QLabel("Order"))
    analysis_fit_row.addWidget(window.analysis_poly_order_spin)
    analysis_fit_row.addSpacing(10)
    analysis_fit_row.addWidget(QLabel("Metric"))
    analysis_fit_row.addWidget(window.analysis_metric_combo)
    analysis_fit_row.addStretch(1)
    analysis_layout.addRow("Fit", analysis_fit_row)
    analysis_range_row = QHBoxLayout()
    analysis_range_row.setContentsMargins(0, 0, 0, 0)
    analysis_range_row.setSpacing(6)
    analysis_range_row.addWidget(QLabel("Start"))
    analysis_range_row.addWidget(window.analysis_start_frame_spin)
    analysis_range_row.addSpacing(10)
    analysis_range_row.addWidget(QLabel("End"))
    analysis_range_row.addWidget(window.analysis_end_frame_spin)
    analysis_range_row.addStretch(1)
    analysis_layout.addRow("Frames", analysis_range_row)
    analysis_layout.addRow("Formula", window.analysis_formula_label)
    analysis_layout.addRow("Result", window.analysis_summary_label)

    window.image_toolbar = QWidget(window)
    window.image_toolbar.setObjectName("imageToolbar")
    window.dataset_section = CollapsibleSection("Dataset", dataset_group, expanded=True, help_text=panel_help_text("dataset"), parent=window)
    window.chromatic_section = CollapsibleSection(
        "Chromatic correction",
        chromatic_group,
        expanded=False,
        applied=bool(window._state.preprocessing.chromatic_correction_enabled),
        apply_tooltip="Apply or skip the stored chromatic transform models for display and processing.",
        help_text=panel_help_text("chromatic"),
        parent=window,
    )
    window.mask_section = CollapsibleSection(
        "Mask",
        mask_group,
        expanded=True,
        applied=bool(window._state.spot_detection.ignore_marked_pixels),
        apply_tooltip="Apply or skip mask-based exclusions during image display and processing.",
        help_text=panel_help_text("mask"),
        parent=window,
    )
    window.image_tools_section = CollapsibleSection(
        "Image tools",
        window.image_toolbar,
        expanded=True,
        applied=bool(window._state.preprocessing.image_tools_enabled),
        apply_tooltip="Link image tools to the processed image. Off means preview only; on means recalculate downstream views.",
        help_text=panel_help_text("image_tools"),
        parent=window,
    )
    window.spot_editor_section = CollapsibleSection(
        "Spot editor",
        spot_editor_group,
        expanded=True,
        applied=bool(window._read_bool_setting("controls/live_geometry", False)),
        apply_tooltip="Apply live spot-geometry recalculation while editing.",
        help_text=panel_help_text("spot_editor"),
        parent=window,
    )
    window.background_section = CollapsibleSection(
        "Background removal",
        background_group,
        expanded=True,
        applied=bool(window._state.preprocessing.flatten_background_enabled),
        apply_tooltip="Apply or skip background removal from the processing pipeline.",
        help_text=panel_help_text("background"),
        parent=window,
    )
    window.analysis_section = CollapsibleSection(
        "Analysis",
        analysis_group,
        expanded=True,
        applied=bool(window._analysis_enabled),
        apply_tooltip="Enable or disable analysis calculations.",
        help_text=panel_help_text("analysis"),
        parent=window,
    )
    window._bind_collapsible_group(
        [
            window.dataset_section,
            window.mask_section,
            window.chromatic_section,
            window.image_tools_section,
            window.spot_editor_section,
            window.background_section,
            window.analysis_section,
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
            window.mask_section,
            window.chromatic_section,
            window.image_tools_section,
            window.spot_editor_section,
            window.background_section,
            window.analysis_section,
        ),
        "Controls",
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
    image_tools_panel = QWidget(window)
    image_tools_layout = QVBoxLayout(image_tools_panel)
    image_tools_layout.setContentsMargins(0, 0, 0, 0)
    image_tools_layout.setSpacing(4)
    image_tools_layout.addWidget(window.image_name_label)
    image_tools_layout.addWidget(window.image_view, 1)
    image_tools_layout.addWidget(window.bottom_view_toolbar)

    window.spot_list_table = QTableWidget(window)
    window.spot_list_table.setColumnCount(9)
    window.spot_list_table.setHorizontalHeaderLabels(["ID", "Group", "C_c", "C_r", "D_s", "d_r", "D_r", "x", "y"])
    window.spot_list_table.setAlternatingRowColors(False)
    window.spot_list_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    window.spot_list_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
    window.spot_list_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
    window.spot_list_table.setWordWrap(False)
    window.spot_list_table.verticalHeader().setVisible(False)
    window.spot_list_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
    window.spot_list_table.horizontalHeader().setStretchLastSection(True)
    window.spot_list_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    window.spot_list_table.horizontalHeader().setSortIndicatorShown(True)
    window.spot_list_table.setSortingEnabled(True)
    window.spot_list_table.setShowGrid(False)
    table_font = window.spot_list_table.font()
    table_font.setPointSize(8)
    window.spot_list_table.setFont(table_font)
    window.spot_list_table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    window.spot_list_table.setMaximumWidth(16777215)
    window.spot_list_table.setMinimumWidth(220)
    window.spot_list_table.installEventFilter(window)
    window.spot_list_table.viewport().installEventFilter(window)
    window._set_help(
        window.spot_list_table,
        "Spot table keyboard shortcuts:\n"
        "PageUp / PageDown: Move selected spots in the table\n"
        "Delete / Backspace: Remove selected spots\n"
        "Ctrl+C: Copy selected spot properties\n"
        "Ctrl+V: Paste copied spot properties\n"
        "Double-click: Edit the clicked spot field\n"
        "Use the table selection to choose one or more spots before copying or moving.",
    )
    spot_list_panel = QWidget(window)
    window.spot_list_io_layout = QVBoxLayout(spot_list_panel)
    window.spot_list_io_layout.setContentsMargins(0, 0, 0, 0)
    window.spot_list_io_layout.setSpacing(4)
    window.spot_list_io_layout.addWidget(window.spot_list_table)
    spot_list_io_row = QHBoxLayout()
    spot_list_io_row.setContentsMargins(0, 0, 0, 0)
    spot_list_io_row.setSpacing(4)
    window.spot_list_cached_button = window._make_icon_tool_button(
        "database",
        "#22c55e",
        "Show only cached spots in the image overlay.",
        checkable=True,
        icon=window._make_cached_spots_icon(window._cached_spots_only_visible),
    )
    window.spot_list_cached_button.setChecked(window._cached_spots_only_visible)
    window.spot_list_export_button = window._make_icon_tool_button("file-import", "#22c55e", "Save the spot table to a CSV file.")
    window.spot_list_import_button = window._make_icon_tool_button("file-export", "#38bdf8", "Load spot table data from a CSV file.")
    spot_list_io_row.addWidget(window.spot_list_cached_button)
    spot_list_io_row.addStretch(1)
    spot_list_io_row.addWidget(window.spot_list_export_button)
    spot_list_io_row.addWidget(window.spot_list_import_button)
    window.spot_list_io_layout.addLayout(spot_list_io_row)
    window._apply_spot_list_table_style()

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
    window.spot_list_panel = window._create_panel_container("Spot table", spot_list_panel, panel_name="spotListPanel")
    window.spot_list_panel.setMinimumWidth(240)
    window.image_panel = window._create_panel_container("Image area", image_tools_panel, panel_name="imageAreaPanel")
    window.image_panel.setMinimumWidth(360)
    window.histogram_panel = window._create_panel_container("Histogram", histogram_content, panel_name="histogramPanel")
    window.histogram_panel.setMinimumWidth(240)
    window.spectra_panel = window._create_panel_container("Spectra", spectra_content, panel_name="spectraPanel")
    window.spectra_panel.setMinimumWidth(320)
    window.sensorgram_panel = window._create_panel_container("Sensorgram", sensorgram_content, panel_name="sensorgramPanel")
    window.sensorgram_panel.setMinimumWidth(320)
    for panel in (window.workflow_panel, window.spot_list_panel, window.image_panel, window.histogram_panel, window.spectra_panel, window.sensorgram_panel):
        panel.visibilityChanged.connect(lambda _visible, target=panel: window._on_panel_visibility_changed(target))
    window._restore_default_panel_layout()
