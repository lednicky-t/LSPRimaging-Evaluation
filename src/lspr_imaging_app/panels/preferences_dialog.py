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
settings writer, `LayoutPresetManager.auto_apply`). Nothing is applied before OK; Cancel changes nothing.
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

from .image.overlay_cache import BUDGET_CHOICES_MB, DEFAULT_BUDGET_MB, ENV_BUDGET_MB


@dataclass(frozen=True)
class Preferences:
    """The values the dialog edits. The defaults are the app's own (pinned against `AppSettings` in the tests)."""

    reopen_last_dataset: bool = True
    auto_apply_layout_preset: bool = False
    overlay_cache_mb: int = DEFAULT_BUDGET_MB
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
        self.auto_apply_check = QCheckBox("Arrange the panels when the Workflow stage changes", general)
        self.auto_apply_check.setToolTip(
            "Switching to another stage in the Workflow panel applies that stage's panel preset "
            "(View -> Panel Presets). Off: the panels stay where you put them."
        )
        general_layout = QVBoxLayout(general)
        general_layout.addWidget(self.reopen_check)
        general_layout.addWidget(self.auto_apply_check)

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
        memory_layout = QVBoxLayout(memory)
        memory_layout.addLayout(form)
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
            auto_apply_layout_preset=self.auto_apply_check.isChecked(),
            overlay_cache_mb=int(self.cache_combo.currentData()),
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
        self.auto_apply_check.setChecked(preferences.auto_apply_layout_preset)
        index = self.cache_combo.findData(int(preferences.overlay_cache_mb))
        if index >= 0:
            self.cache_combo.setCurrentIndex(index)
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
