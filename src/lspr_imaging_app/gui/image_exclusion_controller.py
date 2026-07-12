from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainterPath
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lspr_imaging_app.domain.exclusions import ImageExclusionRule, is_excluded, remove_rule, upsert_rule

EXCLUSION_COLOR = "#ef4444"
NEUTRAL_COLOR = "#94a3b8"


class ImageExclusionController:
    """Owns the "exclude from processing" feature: the slicer-row menu, the
    manage-all-exclusions dialog, and the red-frame/icon indicator for the
    currently displayed image. Rule storage itself is just
    `window._state.image_exclusions` (a plain list of `ImageExclusionRule`,
    round-tripped through processing_profile.json like the ROI/chromatic
    lists it sits next to) -- this class only owns the UI and the glue that
    keeps caches and the dialog in sync with it.
    """

    def __init__(self, window) -> None:
        self.window = window
        self._manage_dialog: QDialog | None = None
        self._manage_table: QTableWidget | None = None

    def _current_key(self) -> tuple[int, float] | None:
        return self.window._current_image_key

    def is_current_excluded(self) -> bool:
        key = self._current_key()
        if key is None:
            return False
        return is_excluded(self.window._state.image_exclusions, key[0], key[1])

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def show_menu(self) -> None:
        window = self.window
        button = window.image_exclusion_button
        key = self._current_key()
        menu = QMenu(window)
        if key is None:
            action = menu.addAction("Load a dataset to manage exclusions")
            action.setEnabled(False)
        else:
            spectral_cube_index, wavelength_nm = key
            menu.addAction(
                f"Exclude this image (cube {spectral_cube_index}, {wavelength_nm:g} nm)",
                lambda: self._add_rule(spectral_cube_index, wavelength_nm),
            )
            menu.addAction(
                f"Exclude {wavelength_nm:g} nm (all spectral cubes)",
                lambda: self._add_rule(None, wavelength_nm),
            )
            menu.addAction(
                f"Exclude spectral cube {spectral_cube_index} (all wavelengths)",
                lambda: self._add_rule(spectral_cube_index, None),
            )
            covering = [rule for rule in window._state.image_exclusions if is_excluded([rule], spectral_cube_index, wavelength_nm)]
            if covering:
                menu.addSeparator()
                for rule in covering:
                    menu.addAction(
                        f"Remove exclusion: {self._describe_rule(rule)}",
                        lambda checked=False, r=rule: self._remove_rule(r.spectral_cube_index, r.wavelength_nm),
                    )
        menu.addSeparator()
        menu.addAction("Manage all exclusions...", self.open_manage_dialog)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    @staticmethod
    def _describe_rule(rule: ImageExclusionRule) -> str:
        if rule.spectral_cube_index is None:
            return f"{rule.wavelength_nm:g} nm (all spectral cubes)"
        if rule.wavelength_nm is None:
            return f"spectral cube {rule.spectral_cube_index} (all wavelengths)"
        return f"cube {rule.spectral_cube_index}, {rule.wavelength_nm:g} nm"

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def _add_rule(self, spectral_cube_index: int | None, wavelength_nm: float | None) -> None:
        window = self.window
        window._push_undo_point("Exclude image")
        upsert_rule(
            window._state.image_exclusions,
            spectral_cube_index,
            wavelength_nm,
            created_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._after_rules_changed()

    def _remove_rule(self, spectral_cube_index: int | None, wavelength_nm: float | None) -> None:
        window = self.window
        window._push_undo_point("Remove exclusion")
        remove_rule(window._state.image_exclusions, spectral_cube_index, wavelength_nm)
        self._after_rules_changed()

    def _after_rules_changed(self) -> None:
        window = self.window
        window._invalidate_caches_for_exclusion_change()
        window._schedule_processing_state_save()
        self.update_indicator()
        self.refresh_manage_dialog_if_open()

    # ------------------------------------------------------------------
    # Indicator (icon recolor + red frame around the image view)
    # ------------------------------------------------------------------

    def update_indicator(self, spectral_cube_index: int | None = None, wavelength_nm: float | None = None) -> None:
        window = self.window
        if spectral_cube_index is None or wavelength_nm is None:
            key = self._current_key()
            excluded = False if key is None else is_excluded(window._state.image_exclusions, key[0], key[1])
        else:
            excluded = is_excluded(window._state.image_exclusions, spectral_cube_index, wavelength_nm)
        color = EXCLUSION_COLOR if excluded else NEUTRAL_COLOR
        window.image_exclusion_button.setIcon(window._mask_panel_icon("ban", color=color))
        overlay = window._ensure_exclusion_overlay()
        if excluded and window._current_processed_image is not None:
            height, width = window._current_processed_image.shape[:2]
            path = QPainterPath()
            path.addRect(QRectF(1.0, 1.0, max(float(width) - 2.0, 1.0), max(float(height) - 2.0, 1.0)))
            overlay.setPath(path)
            overlay.setVisible(True)
        else:
            overlay.setVisible(False)

    # ------------------------------------------------------------------
    # Manage dialog
    # ------------------------------------------------------------------

    def open_manage_dialog(self) -> None:
        window = self.window
        if self._manage_dialog is None:
            dialog = QDialog(window)
            dialog.setWindowTitle("Manage excluded images")
            dialog.resize(520, 360)
            layout = QVBoxLayout(dialog)
            table = QTableWidget(dialog)
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["Spectral cube", "Wavelength (nm)", "Note", ""])
            layout.addWidget(table)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
            buttons.rejected.connect(dialog.reject)
            buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)
            self._manage_dialog = dialog
            self._manage_table = table
        self._populate_manage_table()
        self._manage_dialog.show()
        self._manage_dialog.raise_()
        self._manage_dialog.activateWindow()

    def refresh_manage_dialog_if_open(self) -> None:
        if self._manage_dialog is not None and self._manage_dialog.isVisible():
            self._populate_manage_table()

    def _populate_manage_table(self) -> None:
        table = self._manage_table
        rules = self.window._state.image_exclusions
        table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            cube_text = "all" if rule.spectral_cube_index is None else str(rule.spectral_cube_index)
            wavelength_text = "all" if rule.wavelength_nm is None else f"{rule.wavelength_nm:g}"
            table.setItem(row, 0, QTableWidgetItem(cube_text))
            table.setItem(row, 1, QTableWidgetItem(wavelength_text))
            table.setItem(row, 2, QTableWidgetItem(rule.note))
            remove_button = QPushButton("Remove", table)
            remove_button.clicked.connect(lambda checked=False, r=rule: self._remove_rule(r.spectral_cube_index, r.wavelength_nm))
            table.setCellWidget(row, 3, remove_button)
        table.resizeColumnsToContents()
