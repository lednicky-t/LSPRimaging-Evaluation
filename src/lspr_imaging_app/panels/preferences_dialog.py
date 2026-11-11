"""Edit -> Preferences... (2026-10-19): the app-wide settings that are not part of any panel.

Replaces the Options menu, which had grown into a mix of startup behaviour, a memory budget and two settings
that belonged elsewhere (ROI fill opacity duplicated the Image ribbon's Transparency slider; ROI position
rounding was removed, the rounding stayed on). Scope rule (maintainer, 2026-10-19): things a panel already
controls (ROIs, visualisation, overlays) stay in that panel; this dialog is for the rest of the app: startup,
layout behaviour, memory and speed. Further settings (image cache size, read-ahead, logging) are planned as
separate tasks, see `docs/preferences_dialog_2026-10-19.md`.

One page, no categories: three settings do not need a sidebar. The dialog is display only: it is given the
current values, and `values()` returns what the user chose; `app_rewrite._wire_preferences` compares the two and
applies the differences through the same setters the menu used (`image_panel.set_overlay_cache_mb`, the
settings writer). Nothing is applied before OK; Cancel changes nothing. (The "arrange the panels when the Workflow
stage changes" option was removed with the Workflow panel, 2026-11-11.)
"""

from __future__ import annotations

from dataclasses import dataclass

from lspr_core.perf import DEFAULT_PERF_PREFERENCES, MODE_AUTO, MODE_MANUAL, MODE_STANDARD, PerfPreferences
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..display_options import RESOLUTION_FULL, RESOLUTION_STANDARD, ResolutionSizes, format_size
from .image.overlay_cache import BUDGET_CHOICES_MB, DEFAULT_BUDGET_MB, ENV_BUDGET_MB


@dataclass(frozen=True)
class Preferences:
    """The values the dialog edits. The defaults are the app's own (pinned against `AppSettings` in the tests)."""

    reopen_last_dataset: bool = True
    overlay_cache_mb: int = DEFAULT_BUDGET_MB
    gpu_drawing: bool = False
    """Draw the Image panel's canvas with the graphics card (OpenGL viewport; `image/gpu_view.py`). Off by default since 2026-11-11:
    with one repaint per frame, a processor-drawn window plays faster at 4K (30 vs 24.6 fps; `docs/play_diagnosis_2026-11-11.md` 6d)."""
    screen_resolution: str = RESOLUTION_FULL
    """"full" (sharp) or "standard" (Windows draws the window at 100 % and stretches it: fewer pixels to hand to the screen, so faster on a
    4K screen, but softer; `display_options.py`). Saved here, applied at the next start."""
    perf: PerfPreferences = DEFAULT_PERF_PREFERENCES
    """Parallel work (export, scans): standard, tune automatically or a fixed number of workers; cores kept free; low priority."""


DEFAULT_PREFERENCES = Preferences()


def _cache_label(megabytes: int) -> str:
    label = "Off" if megabytes == 0 else f"{megabytes} MB"
    return f"{label} (default)" if megabytes == DEFAULT_BUDGET_MB else label


class PreferencesDialog(QDialog):
    def __init__(
        self,
        current: Preferences,
        *,
        overlay_cache_status: str = "",
        cache_set_by_environment: bool = False,
        perf_status: str = "",
        core_count: int = 8,
        perf_saved_count: int = 0,
        running_resolution: str = RESOLUTION_FULL,
        resolution_sizes: ResolutionSizes | None = None,
        resolution_available: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._perf_saved = current.perf  # what the dialog was given: the manual count is returned unchanged unless Fixed is chosen
        self.forget_saved_requested = False  # set by the "Forget saved counts" button; the app acts on it when OK is pressed
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(440)

        general = QGroupBox("General", self)
        self.reopen_check = QCheckBox("Reopen the last dataset on launch", general)
        self.reopen_check.setToolTip("When the app starts, load the dataset that was open when it was last closed.")
        general_layout = QVBoxLayout(general)
        general_layout.addWidget(self.reopen_check)

        memory = QGroupBox("Performance and memory", self)
        self.cache_combo = QComboBox(memory)
        for megabytes in sorted({*BUDGET_CHOICES_MB, int(current.overlay_cache_mb)}):  # a size set by the environment is listed too
            self.cache_combo.addItem(_cache_label(megabytes), megabytes)
        self.cache_combo.setToolTip(
            "Keeps a picture of the ROI overlay for every wavelength, so Play does not repaint it on each frame. "
            "Faster with many ROIs; uses memory (about 8 MB per wavelength at a 1500 x 850 view). "
            "Off paints the overlay directly. Takes effect at once."
        )
        form = QFormLayout()
        form.addRow("ROI overlay cache:", self.cache_combo)
        self.cache_status_label = QLabel(overlay_cache_status, memory)
        self.cache_status_label.setWordWrap(True)
        self.cache_status_label.setEnabled(False)  # greyed: information, not a control
        self.cache_status_label.setVisible(bool(overlay_cache_status))
        self.cache_environment_label = QLabel(
            f"The environment variable {ENV_BUDGET_MB} is set. It decides the size at start-up, "
            "so a choice here lasts for this run only.",
            memory,
        )
        self.cache_environment_label.setWordWrap(True)
        self.cache_environment_label.setVisible(cache_set_by_environment)
        self.gpu_check = QCheckBox("Draw the image with the graphics card (OpenGL)", memory)
        self.gpu_check.setToolTip(
            "The Image panel's picture, ROIs and overlays are painted by the graphics card instead of the processor. "
            "Off by default: the image is cheaper to paint this way, but the graphics card then hands the WHOLE window to the screen "
            "on every change, which costs more on a large or 4K window (Play at 4K: about 25 pictures per second on, 30 off, "
            "measured on one computer). It may help on a computer with a slow processor and a strong graphics card. "
            "Turning it on takes effect at once; turning it off is complete only after the app is restarted (until then the "
            "window still goes through the graphics card). An old or virtual graphics driver can draw wrongly or slowly: "
            "turn it off if the image looks wrong. Without OpenGL on this computer it stays off."
        )
        # The entries show the real pixel sizes of the screen this window is on (the words "full" / "standard" are only the saved values).
        if resolution_sizes is not None:
            full_text = f"{format_size(resolution_sizes.full)} (the monitor's real pixels, sharp)"
            reduced_text = f"{format_size(resolution_sizes.reduced)} (stretched to {format_size(resolution_sizes.full)}, faster, softer)"
            this_run = format_size(resolution_sizes.full if running_resolution == RESOLUTION_FULL else resolution_sizes.reduced)
        else:  # the sizes could not be found (not Windows, or no screen): say it in words
            full_text = "Full resolution (the monitor's real pixels, sharp)"
            reduced_text = "Reduced resolution (stretched by Windows, faster, softer)"
            this_run = "reduced resolution" if running_resolution == RESOLUTION_STANDARD else "full resolution"
        self.resolution_combo = QComboBox(memory)
        self.resolution_combo.addItem(full_text, RESOLUTION_FULL)
        self.resolution_combo.addItem(reduced_text, RESOLUTION_STANDARD)
        pixels_equal = resolution_sizes is not None and not resolution_sizes.differ
        self.resolution_combo.setToolTip(
            "The window is drawn at the monitor's real resolution (sharp), or at the smaller size Windows would use at 100 % scale; "
            "Windows then stretches it to the monitor on the graphics card. The smaller window has fewer pixels to hand over on every "
            "screen update, so Play is faster on a 4K monitor. The price: the whole window, text included, is softer, like an old "
            "program on a high-resolution monitor. Little or no gain on a window that is already small.\n"
            "The sizes are those of the screen this window is on now. Takes effect the next time the app starts."
            + ("\nWindows' scale of this screen is 100 %, so both choices give the same window." if pixels_equal else "")
        )
        self.resolution_combo.setEnabled(bool(resolution_available) and not pixels_equal)
        form.addRow("Screen resolution:", self.resolution_combo)
        self.display_note_label = QLabel(
            f"Screen resolution takes effect the next time the app starts. This run: {this_run}.",
            memory,
        )
        self.display_note_label.setWordWrap(True)
        self.display_note_label.setEnabled(False)  # greyed: information, not a control
        memory_layout = QVBoxLayout(memory)
        memory_layout.addLayout(form)
        memory_layout.addWidget(self.gpu_check)
        memory_layout.addWidget(self.display_note_label)
        memory_layout.addWidget(self.cache_status_label)
        memory_layout.addWidget(self.cache_environment_label)

        parallel = QGroupBox("Parallel work (export and scans)", self)
        self.perf_mode_combo = QComboBox(parallel)
        self.perf_mode_combo.addItem("Standard (every core but the free ones)", MODE_STANDARD)
        self.perf_mode_combo.addItem("Tune automatically (then remember)", MODE_AUTO)
        self.perf_mode_combo.addItem("Fixed number of workers", MODE_MANUAL)
        self.perf_mode_combo.setToolTip(
            "Standard: every core except the ones kept free below. This is what the app was measured with and is right for most "
            "computers. If a tuning run has saved a better number for this computer, Standard uses that number.\n"
            "Tune automatically: while an export or scan runs, the app tries other numbers of workers, keeps the fastest and saves it "
            "for this computer. Try it on a new or much faster computer; it costs a few percent while it searches, and once it has "
            "found a number you can switch back to Standard, which keeps using it.\n"
            "Fixed: always exactly the number below, for example on a shared workstation or to repeat a timing.\n"
            "The result of an export or scan is the same in every mode; only the speed differs."
        )
        self.perf_workers_spin = QSpinBox(parallel)
        self.perf_workers_spin.setRange(1, max(2 * int(core_count), 2))
        self.perf_workers_spin.setValue(max(1, int(core_count) - 1))
        self.perf_workers_spin.setToolTip("Used only with a fixed number. More workers than the computer has cores slows everything down.")
        self.perf_reserved_spin = QSpinBox(parallel)
        self.perf_reserved_spin.setRange(0, max(int(core_count) - 1, 0))
        self.perf_reserved_spin.setToolTip(
            "Cores that Standard and Tune automatically never use, so the window and the rest of the computer stay responsive. "
            "Ignored on a computer with two cores or fewer."
        )
        self.perf_low_priority_check = QCheckBox("Run background work at low priority", parallel)
        self.perf_low_priority_check.setToolTip(
            "Export and scan workers yield to other programs and to this window when the computer is busy. "
            "It costs next to nothing when nothing else is running."
        )
        parallel_form = QFormLayout()
        parallel_form.addRow("Workers:", self.perf_mode_combo)
        parallel_form.addRow("Number (fixed):", self.perf_workers_spin)
        parallel_form.addRow("Cores kept free:", self.perf_reserved_spin)
        self.perf_status_label = QLabel(perf_status, parallel)
        self.perf_status_label.setWordWrap(True)
        self.perf_status_label.setEnabled(False)  # greyed: information, not a control
        self.perf_status_label.setVisible(bool(perf_status))
        parallel_layout = QVBoxLayout(parallel)
        parallel_layout.addLayout(parallel_form)
        parallel_layout.addWidget(self.perf_low_priority_check)
        parallel_layout.addWidget(self.perf_status_label)
        self.perf_forget_button = QPushButton("Forget saved counts", parallel)
        self.perf_forget_button.setToolTip(
            "Deletes the numbers of workers that tuning saved for this computer, so Standard goes back to every core but the free ones. "
            "Applied when you press OK."
        )
        self.perf_forget_button.setEnabled(int(perf_saved_count) > 0)
        self.perf_forget_button.clicked.connect(self._forget_saved_clicked)
        parallel_layout.addWidget(self.perf_forget_button)
        self.perf_mode_combo.currentIndexChanged.connect(self._sync_perf_enabled)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        restore = self.button_box.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        restore.clicked.connect(self._restore_defaults)

        layout = QVBoxLayout(self)
        layout.addWidget(general)
        layout.addWidget(memory)
        layout.addWidget(parallel)
        layout.addWidget(self.button_box)

        self._show(current)

    def values(self) -> Preferences:
        """What the dialog shows now (what OK would apply)."""
        return Preferences(
            reopen_last_dataset=self.reopen_check.isChecked(),
            overlay_cache_mb=int(self.cache_combo.currentData()),
            gpu_drawing=self.gpu_check.isChecked(),
            screen_resolution=str(self.resolution_combo.currentData()),
            perf=self._perf_values(),
        )

    def _perf_values(self) -> PerfPreferences:
        manual = str(self.perf_mode_combo.currentData()) == MODE_MANUAL
        return PerfPreferences(
            mode=str(self.perf_mode_combo.currentData()),
            # The number is only the user's choice when Fixed is selected; otherwise what was saved stays as it was.
            manual_workers=self.perf_workers_spin.value() if manual else self._perf_saved.manual_workers,
            reserved_cores=self.perf_reserved_spin.value(),
            low_priority=self.perf_low_priority_check.isChecked(),
        ).normalized()

    def _sync_perf_enabled(self) -> None:
        manual = str(self.perf_mode_combo.currentData()) == MODE_MANUAL
        self.perf_workers_spin.setEnabled(manual)
        self.perf_reserved_spin.setEnabled(not manual)

    def _forget_saved_clicked(self) -> None:
        self.forget_saved_requested = True
        self.perf_forget_button.setEnabled(False)
        self.perf_forget_button.setText("Saved counts will be forgotten on OK")

    def _show(self, preferences: Preferences) -> None:
        self.reopen_check.setChecked(preferences.reopen_last_dataset)
        index = self.cache_combo.findData(int(preferences.overlay_cache_mb))
        if index >= 0:
            self.cache_combo.setCurrentIndex(index)
        self.gpu_check.setChecked(preferences.gpu_drawing)
        self.resolution_combo.setCurrentIndex(max(self.resolution_combo.findData(preferences.screen_resolution), 0))
        perf = preferences.perf
        self.perf_mode_combo.setCurrentIndex(max(self.perf_mode_combo.findData(perf.mode), 0))
        if perf.manual_workers > 0:
            self.perf_workers_spin.setValue(perf.manual_workers)
        self.perf_reserved_spin.setValue(perf.reserved_cores)
        self.perf_low_priority_check.setChecked(perf.low_priority)
        self._sync_perf_enabled()

    def _restore_defaults(self) -> None:
        """Puts the defaults on screen; nothing is applied until OK."""
        self._show(DEFAULT_PREFERENCES)
