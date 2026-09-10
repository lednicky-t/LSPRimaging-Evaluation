from __future__ import annotations

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_ZARR_ADAPTIVE_BATCH_OPTIONS = [
    (256, "256 MB"),
    (512, "512 MB"),
    (1024, "1 GB (default)"),
    (2048, "2 GB"),
    (4096, "4 GB"),
]

_STARTUP_RESTORE_TIMEOUT_OPTIONS = [
    (5, "Prompt restore (5s)"),
    (0, "Auto restore (0s)"),
]

_MEASUREMENT_BACKUP_BATCH_SIZE_OPTIONS = [
    (1, "1 cube - write immediately (most crash-safe)"),
    (5, "5 cubes (default)"),
    (10, "10 cubes"),
    (20, "20 cubes"),
    (50, "50 cubes (fastest, least crash-safe)"),
]


class PreferencesDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(420)
        # run_dark_frame_impact_test's result arrives on a background thread's
        # timing (a real dataset read, not instant) - if the dialog is closed
        # before it comes back, its callbacks must not touch widgets that no
        # longer exist. done() (called for both accept/reject/close) is the
        # single choke point for that, unlike overriding closeEvent alone.
        self._closed = False

        # Appearance
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Bright", "bright")

        # Startup
        self.startup_restore_combo = QComboBox()
        for seconds, label in _STARTUP_RESTORE_TIMEOUT_OPTIONS:
            self.startup_restore_combo.addItem(label, seconds)
        self.startup_restore_combo.setToolTip(
            "How long to show the \"restore previous session?\" prompt on launch before "
            "giving up (Prompt restore) or skip the prompt and restore immediately (Auto restore)."
        )
        self.log_panel_open_check = QCheckBox("Open log panel on startup")
        self.log_panel_open_check.setToolTip(
            "Start the app with the Workflow log console (Debug output, errors, and timing) "
            "expanded. Takes effect on the next launch; the log panel can always be expanded "
            "or collapsed manually from the Workflow tab."
        )
        self.remember_dataset_format_check = QCheckBox("Remember dataset format choice")
        self.remember_dataset_format_check.setToolTip(
            "When a dataset folder contains both a TIFF image stack and an OME-Zarr export, "
            "reuse whichever one you picked last time instead of asking again - most useful so "
            "restoring the previous session on launch doesn't have to prompt. Turn off to always "
            "be asked."
        )

        # Wavelength handling
        self.exclude_zero_wavelength_check = QCheckBox("Treat 0 nm as a dark reference frame")
        self.exclude_zero_wavelength_check.setToolTip(
            "0 nm (a dark/broadband frame some acquisitions capture alongside the narrowband "
            "wavelengths) is never used in chromatic correction, masking, or ROI placement "
            "regardless of this setting. When enabled, it's also dropped from ordinary "
            "wavelength navigation (the wavelength slider/spinbox, sample-wavelength lists) AND "
            "from the Analysis section's spectrum (Metric trace fitting, \"Start analysis\" "
            "sensorgram sweeps) - both read the same filtered wavelength list, so leaving this "
            "off lets a 0 nm frame's near-zero counts pull a polynomial fit's peak search far "
            "outside your real spectral range. Use \"Test dark-frame impact\" below to check how "
            "much it actually matters for your dataset before deciding. Cube navigation always "
            "starts from the next lowest real wavelength instead of 0 nm when this is on. The "
            "image itself is unaffected either way. Takes effect the next time a dataset is "
            "loaded, not for one already open."
        )
        self.dark_frame_test_button = QPushButton("Test dark-frame impact...")
        self.dark_frame_test_button.setToolTip(
            "Measures your 0 nm frame's own pixel counts (dark current - never zero on a real "
            "sensor) for the first spectral cube, and simulates subtracting them from every real "
            "wavelength's sample/reference values as a dark-offset correction, reporting how much "
            "that would actually change your formula value (e.g. Absorbance) - a small dark count "
            "against a bright signal is usually negligible, but the same count can matter against "
            "a dim one. Also flags likely hot/noisy pixels in the dark frame itself. Requires a "
            "dataset with a 0 nm frame to be loaded and at least one ROI selected. Works for both "
            "TIFF and OME-Zarr datasets."
        )
        self.dark_frame_test_button.clicked.connect(self._run_dark_frame_impact_test)
        self.dark_frame_test_result_label = QLabel("")
        self.dark_frame_test_result_label.setWordWrap(True)

        # OME-Zarr export
        self.zarr_adaptive_enabled_check = QCheckBox("Adaptive worker tuning enabled")
        self.zarr_adaptive_enabled_check.setToolTip(
            "Periodically adjust the number of Stack-to-Zarr export worker processes based on "
            "whether time is being spent waiting on disk or on compression. Turn off for a "
            "fixed, predictable worker count equal to your CPU's core count."
        )
        self.zarr_adaptive_batch_combo = QComboBox()
        for mb, label in _ZARR_ADAPTIVE_BATCH_OPTIONS:
            self.zarr_adaptive_batch_combo.addItem(label, mb)
        self.zarr_adaptive_batch_combo.setToolTip(
            "How much data is processed between adaptive tuning checks. A larger sample avoids "
            "mistaking a fast drive's temporary write-cache burst for its true sustained speed."
        )
        self.zarr_adaptive_info_button = QPushButton("What is this?")
        self.zarr_adaptive_info_button.clicked.connect(self._show_zarr_adaptive_info)

        # Analysis
        self.measurement_backup_batch_combo = QComboBox()
        for cubes, label in _MEASUREMENT_BACKUP_BATCH_SIZE_OPTIONS:
            self.measurement_backup_batch_combo.addItem(label, cubes)
        self.measurement_backup_batch_combo.setToolTip(
            "During \"Start analysis\", results are backed up to measurement_backup.h5 as they're "
            "computed. Buffering several cubes' worth before each write cuts backup overhead "
            "roughly by that factor - but if the app crashes mid-batch, only the results already "
            "written to disk survive; anything still buffered is lost (a normal Stop or app close "
            "always flushes what's buffered first, so this only matters for an actual crash). "
            "\"1 cube\" writes every result immediately, matching the original behavior."
        )

        self.analysis_cache_budget_spin = QSpinBox()
        self.analysis_cache_budget_spin.setRange(16, 8192)
        self.analysis_cache_budget_spin.setSingleStep(50)
        self.analysis_cache_budget_spin.setSuffix(" MB")
        self.analysis_cache_budget_spin.setToolTip(
            "How much RAM is used to hold already-computed absorbance spectra (one entry per ROI "
            "per spectral cube) and computed sensorgram traces (one entry per ROI-selection/"
            "settings combination). A larger budget means switching between already-analyzed "
            "ROIs/cubes/selections stays instant for longer before anything needs to be re-read "
            "from the measurement backup file or recomputed; it costs more memory, not accuracy - "
            "a value that's too small only means more frequent (still correct, just slower) disk "
            "reads or recomputation, never wrong results. Shared between both caches, each sized "
            "independently against this same budget."
        )
        self.analysis_cache_budget_spin.valueChanged.connect(self._update_analysis_cache_estimate_label)
        self.analysis_cache_estimate_label = QLabel("")

        # Small calculator: "how big would fully caching N ROIs x N
        # wavelengths x N cubes' worth of spectra be" - answers the sizing
        # question in the other direction from the estimate label above
        # (that one goes budget -> entry count; this one goes ROI/wavelength/
        # cube counts -> MB), so a maintainer can size the budget against
        # their actual dataset instead of guessing. Deliberately spectra-only
        # (matches the formula requested: #ROI x #wavelengths x #cubes) - the
        # sensorgram cache doesn't scale with wavelength count the same way,
        # so it isn't part of this specific calculation.
        self.cache_calc_roi_spin = QSpinBox()
        self.cache_calc_roi_spin.setRange(1, 100000)
        self.cache_calc_roi_spin.setValue(1)
        self.cache_calc_roi_spin.setToolTip("Number of ROIs to estimate for.")
        self.cache_calc_wavelength_spin = QSpinBox()
        self.cache_calc_wavelength_spin.setRange(1, 2000)
        self.cache_calc_wavelength_spin.setValue(40)
        self.cache_calc_wavelength_spin.setToolTip("Number of wavelengths per spectrum to estimate for.")
        self.cache_calc_cube_spin = QSpinBox()
        self.cache_calc_cube_spin.setRange(1, 500000)
        self.cache_calc_cube_spin.setValue(100)
        self.cache_calc_cube_spin.setToolTip("Number of spectral cubes (time points) to estimate for.")
        for spin in (self.cache_calc_roi_spin, self.cache_calc_wavelength_spin, self.cache_calc_cube_spin):
            spin.valueChanged.connect(self._update_cache_calculator_result_label)
        self.cache_calc_result_label = QLabel("")
        self.cache_calc_use_selection_button = QPushButton("Use current selection")
        self.cache_calc_use_selection_button.setToolTip("Fill # ROIs from how many ROIs are currently selected.")
        self.cache_calc_use_selection_button.clicked.connect(self._fill_cache_calculator_from_selection)
        self.cache_calc_use_dataset_button = QPushButton("Use current dataset")
        self.cache_calc_use_dataset_button.setToolTip(
            "Fill # wavelengths and # cubes from the currently loaded dataset."
        )
        self.cache_calc_use_dataset_button.clicked.connect(self._fill_cache_calculator_from_dataset)

        self._build_ui()
        self._load_from_window()
        self._fit_to_available_screen()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # A tall dialog gets a scroll area instead of growing past the
        # screen's available height (see _fit_to_available_screen), matching
        # the pattern used by singleLSPR Acquisition's preferences dialog.
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll_area)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        appearance_box = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_box)
        appearance_layout.setHorizontalSpacing(16)
        appearance_layout.setVerticalSpacing(8)
        appearance_layout.addRow("Theme", self.theme_combo)

        startup_box = QGroupBox("Startup")
        startup_layout = QFormLayout(startup_box)
        startup_layout.setHorizontalSpacing(16)
        startup_layout.setVerticalSpacing(8)
        startup_layout.addRow("Restore previous session", self.startup_restore_combo)
        startup_layout.addRow(self.log_panel_open_check)
        startup_layout.addRow(self.remember_dataset_format_check)

        wavelength_box = QGroupBox("Wavelength handling")
        wavelength_layout = QFormLayout(wavelength_box)
        wavelength_layout.setHorizontalSpacing(16)
        wavelength_layout.setVerticalSpacing(8)
        wavelength_layout.addRow(self.exclude_zero_wavelength_check)
        dark_frame_test_row = QHBoxLayout()
        dark_frame_test_row.addWidget(self.dark_frame_test_button)
        dark_frame_test_row.addStretch(1)
        wavelength_layout.addRow(dark_frame_test_row)
        wavelength_layout.addRow(self.dark_frame_test_result_label)

        zarr_box = QGroupBox("OME-Zarr export: adaptive worker tuning")
        zarr_layout = QFormLayout(zarr_box)
        zarr_layout.setHorizontalSpacing(16)
        zarr_layout.setVerticalSpacing(8)
        zarr_layout.addRow(self.zarr_adaptive_enabled_check)
        zarr_layout.addRow("Sample size for tuning decisions", self.zarr_adaptive_batch_combo)
        info_row = QHBoxLayout()
        info_row.addStretch(1)
        info_row.addWidget(self.zarr_adaptive_info_button)
        zarr_layout.addRow(info_row)

        analysis_box = QGroupBox("Analysis: measurement backup")
        analysis_layout = QFormLayout(analysis_box)
        analysis_layout.setHorizontalSpacing(16)
        analysis_layout.setVerticalSpacing(8)
        analysis_layout.addRow("Write batch size", self.measurement_backup_batch_combo)

        cache_box = QGroupBox("Analysis: memory cache")
        cache_layout = QFormLayout(cache_box)
        cache_layout.setHorizontalSpacing(16)
        cache_layout.setVerticalSpacing(8)
        cache_size_row = QHBoxLayout()
        cache_size_row.addWidget(self.analysis_cache_budget_spin)
        cache_size_row.addWidget(self.analysis_cache_estimate_label)
        cache_size_row.addStretch(1)
        cache_layout.addRow("Memory cache size", cache_size_row)

        cache_calc_inputs_row = QHBoxLayout()
        cache_calc_inputs_row.addWidget(self.cache_calc_roi_spin)
        cache_calc_inputs_row.addWidget(QLabel("ROIs ×"))
        cache_calc_inputs_row.addWidget(self.cache_calc_wavelength_spin)
        cache_calc_inputs_row.addWidget(QLabel("wavelengths ×"))
        cache_calc_inputs_row.addWidget(self.cache_calc_cube_spin)
        cache_calc_inputs_row.addWidget(QLabel("cubes ="))
        cache_calc_inputs_row.addWidget(self.cache_calc_result_label)
        cache_calc_inputs_row.addStretch(1)
        cache_calc_buttons_row = QHBoxLayout()
        cache_calc_buttons_row.addWidget(self.cache_calc_use_selection_button)
        cache_calc_buttons_row.addWidget(self.cache_calc_use_dataset_button)
        cache_calc_buttons_row.addStretch(1)
        cache_calc_column = QVBoxLayout()
        cache_calc_column.addLayout(cache_calc_inputs_row)
        cache_calc_column.addLayout(cache_calc_buttons_row)
        cache_layout.addRow("Spectra cache size calculator", cache_calc_column)

        layout.addWidget(appearance_box)
        layout.addWidget(startup_box)
        layout.addWidget(wavelength_box)
        layout.addWidget(zarr_box)
        layout.addWidget(analysis_box)
        layout.addWidget(cache_box)

        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        apply_btn = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self.apply_changes)
        button_row = QWidget(self)
        button_row_layout = QVBoxLayout(button_row)
        button_row_layout.setContentsMargins(16, 10, 16, 16)
        button_row_layout.addWidget(button_box)
        outer_layout.addWidget(button_row, 0)

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_height = max(int(available.height() * 0.85), 320)
        target_height = min(self.sizeHint().height(), max_height)
        self.resize(self.width(), target_height)

    def _on_ok(self) -> None:
        self.apply_changes()
        self.accept()

    def done(self, result: int) -> None:  # type: ignore[override]
        self._closed = True
        super().done(result)

    def _show_zarr_adaptive_info(self) -> None:
        if hasattr(self._window, "_dataset_controller"):
            self._window._dataset_controller._show_ome_zarr_adaptive_tuning_info()

    def _run_dark_frame_impact_test(self) -> None:
        analysis_controller = getattr(self._window, "_analysis_controller", None)
        if analysis_controller is None or not hasattr(analysis_controller, "run_dark_frame_impact_test"):
            return
        self.dark_frame_test_button.setEnabled(False)
        self.dark_frame_test_result_label.setText("Testing cube 0...")

        def _on_result(text: str) -> None:
            if self._closed:
                return
            self.dark_frame_test_button.setEnabled(True)
            self.dark_frame_test_result_label.setText(text)

        def _on_error(message: str) -> None:
            if self._closed:
                return
            self.dark_frame_test_button.setEnabled(True)
            self.dark_frame_test_result_label.setText(str(message))

        analysis_controller.run_dark_frame_impact_test(_on_result, _on_error)

    def _update_analysis_cache_estimate_label(self) -> None:
        window = self._window
        if not hasattr(window, "_estimated_roi_formula_spectrum_entry_bytes"):
            self.analysis_cache_estimate_label.setText("")
            return
        budget_bytes = self.analysis_cache_budget_spin.value() * 1024 * 1024
        spectra_per_entry_bytes = max(1, window._estimated_roi_formula_spectrum_entry_bytes())
        sensorgrams_per_entry_bytes = max(1, window._estimated_sensorgram_result_entry_bytes())
        estimated_spectra = int(budget_bytes / spectra_per_entry_bytes)
        estimated_sensorgrams = int(budget_bytes / sensorgrams_per_entry_bytes)
        self.analysis_cache_estimate_label.setText(
            f"→ ~{estimated_spectra:,} spectra, or ~{estimated_sensorgrams:,} sensorgrams (each up to that budget)"
        )

    def _update_cache_calculator_result_label(self) -> None:
        window = self._window
        if not hasattr(window, "_estimated_roi_formula_spectrum_bytes_per_wavelength"):
            self.cache_calc_result_label.setText("")
            return
        n_rois = self.cache_calc_roi_spin.value()
        n_wavelengths = self.cache_calc_wavelength_spin.value()
        n_cubes = self.cache_calc_cube_spin.value()
        bytes_per_wavelength = window._estimated_roi_formula_spectrum_bytes_per_wavelength()
        total_bytes = n_rois * n_wavelengths * n_cubes * bytes_per_wavelength
        total_mb = total_bytes / (1024.0 * 1024.0)
        if total_mb >= 1024.0:
            self.cache_calc_result_label.setText(f"~{total_mb / 1024.0:,.2f} GB")
        else:
            self.cache_calc_result_label.setText(f"~{total_mb:,.1f} MB")

    def _fill_cache_calculator_from_selection(self) -> None:
        window = self._window
        selected_roi_ids = getattr(window, "_selected_roi_ids", None)
        if selected_roi_ids:
            self.cache_calc_roi_spin.setValue(max(1, len(selected_roi_ids)))

    def _fill_cache_calculator_from_dataset(self) -> None:
        window = self._window
        wavelength_values = getattr(window, "_wavelength_values", None)
        if wavelength_values:
            self.cache_calc_wavelength_spin.setValue(max(1, len(wavelength_values)))
        spectral_cube_values = getattr(window, "_spectral_cube_values", None)
        if spectral_cube_values:
            self.cache_calc_cube_spin.setValue(max(1, len(spectral_cube_values)))

    def _load_from_window(self) -> None:
        window = self._window

        theme = str(window._settings.value("ui/theme", "dark")) if hasattr(window, "_settings") else "dark"
        theme_index = self.theme_combo.findData("bright" if theme == "bright" else "dark")
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)

        if hasattr(window, "_startup_restore_timeout_seconds"):
            timeout = window._startup_restore_timeout_seconds()
            index = self.startup_restore_combo.findData(timeout)
            self.startup_restore_combo.setCurrentIndex(index if index >= 0 else 0)

        if hasattr(window, "_startup_log_panel_open"):
            self.log_panel_open_check.setChecked(bool(window._startup_log_panel_open()))

        if hasattr(window, "_remember_dataset_format_choice"):
            self.remember_dataset_format_check.setChecked(bool(window._remember_dataset_format_choice()))

        if hasattr(window, "_exclude_zero_wavelength_enabled"):
            self.exclude_zero_wavelength_check.setChecked(bool(window._exclude_zero_wavelength_enabled()))

        if hasattr(window, "_ome_zarr_adaptive_enabled"):
            self.zarr_adaptive_enabled_check.setChecked(bool(window._ome_zarr_adaptive_enabled()))

        if hasattr(window, "_ome_zarr_adaptive_batch_mb"):
            batch_mb = window._ome_zarr_adaptive_batch_mb()
            index = self.zarr_adaptive_batch_combo.findData(batch_mb)
            self.zarr_adaptive_batch_combo.setCurrentIndex(index if index >= 0 else 2)

        if hasattr(window, "_measurement_backup_batch_size"):
            batch_size = window._measurement_backup_batch_size()
            index = self.measurement_backup_batch_combo.findData(batch_size)
            self.measurement_backup_batch_combo.setCurrentIndex(index if index >= 0 else 1)

        if hasattr(window, "_analysis_cache_budget_mb"):
            self.analysis_cache_budget_spin.setValue(int(window._analysis_cache_budget_mb()))
        self._update_analysis_cache_estimate_label()
        self._update_cache_calculator_result_label()

    def apply_changes(self) -> None:
        window = self._window

        theme = str(self.theme_combo.currentData() or "dark")
        if hasattr(window, "_set_ui_theme"):
            window._set_ui_theme(theme)

        if hasattr(window, "_set_startup_restore_timeout_seconds"):
            timeout = self.startup_restore_combo.currentData()
            if timeout is not None:
                window._set_startup_restore_timeout_seconds(int(timeout))

        if hasattr(window, "_set_startup_log_panel_open"):
            window._set_startup_log_panel_open(self.log_panel_open_check.isChecked())

        if hasattr(window, "_set_remember_dataset_format_choice"):
            window._set_remember_dataset_format_choice(self.remember_dataset_format_check.isChecked())

        if hasattr(window, "_set_exclude_zero_wavelength_enabled"):
            window._set_exclude_zero_wavelength_enabled(self.exclude_zero_wavelength_check.isChecked())

        if hasattr(window, "_set_ome_zarr_adaptive_enabled"):
            window._set_ome_zarr_adaptive_enabled(self.zarr_adaptive_enabled_check.isChecked())

        if hasattr(window, "_set_ome_zarr_adaptive_batch_mb"):
            batch_mb = self.zarr_adaptive_batch_combo.currentData()
            if batch_mb is not None:
                window._set_ome_zarr_adaptive_batch_mb(int(batch_mb))

        if hasattr(window, "_set_measurement_backup_batch_size"):
            batch_size = self.measurement_backup_batch_combo.currentData()
            if batch_size is not None:
                window._set_measurement_backup_batch_size(int(batch_size))

        if hasattr(window, "_set_analysis_cache_budget_mb"):
            window._set_analysis_cache_budget_mb(self.analysis_cache_budget_spin.value())


def show_preferences_dialog_for(window) -> None:
    dialog = PreferencesDialog(window)
    dialog.exec()
