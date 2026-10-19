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

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
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
        layout.addWidget(self.button_box)

        self._show(current)

    def values(self) -> Preferences:
        """What the dialog shows now (what OK would apply)."""
        return Preferences(
            reopen_last_dataset=self.reopen_check.isChecked(),
            auto_apply_layout_preset=self.auto_apply_check.isChecked(),
            overlay_cache_mb=int(self.cache_combo.currentData()),
        )

    def _show(self, preferences: Preferences) -> None:
        self.reopen_check.setChecked(preferences.reopen_last_dataset)
        self.auto_apply_check.setChecked(preferences.auto_apply_layout_preset)
        index = self.cache_combo.findData(int(preferences.overlay_cache_mb))
        if index >= 0:
            self.cache_combo.setCurrentIndex(index)

    def _restore_defaults(self) -> None:
        """Puts the defaults on screen; nothing is applied until OK."""
        self._show(DEFAULT_PREFERENCES)
