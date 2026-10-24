"""The Image panel's "Analysis" ribbon tab (2026-10-24, maintainer request: move the Workflow panel's Analysis section here; the Workflow
panel is on its way out).

Screen purpose: start and stop the scan (step 1 of the analysis: mean / STD / pixel counts per ROI, cube, wavelength and side, saved to
``analysis/scan.h5`` of the session), choose how much of it to redo, and reach the file.

Layout (one ribbon row, captioned groups):

    [play / stop]  |  [skip]  |  [folder]
        Scan         Fast scan   Scan file

- **Scan:** one button. Play starts the scan; while it runs the same button is a red Stop (as the Export tab's), which stops it like the
  Cancel in the status bar's task indicator. Cubes that were finished stay saved.
- **Fast scan** (on by default): read again only the cubes whose inputs changed since the last scan (``analysis/scan_fingerprint.py``). Off =
  read every cube again. This is the old checkbox "Scan every cube again", turned round: its saved value (``analysis/scan_every_cube_again``)
  is not carried over.
- **Scan file:** opens the folder that holds the scan file the Spectra and Sensorgram panels are showing. Visible only while the file exists.
- **Text:** what the last scan did ("scanned in ...", "up to date", why it could not start) is the "i" icon's text while this tab is open and the
  Scan button's tooltip; a message that must be seen (a refusal, an outcome) also goes to the status bar. Progress and Cancel are the
  task indicator's.

A display and three buttons, nothing more: the buttons only emit requests, and the owner (``app_rewrite._wire_analysis_scan``) starts
``analysis/scan_task.ScanTask`` and tells this tab what is running (``set_running``), what to say (``set_status``) and whether the file
exists (``set_scan_file_available``). It touches no module and no file.
"""

from __future__ import annotations

import html

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.live_state import AnalysisLiveModule
from ..live_toggle import LiveToggle
from ..ribbon_group import labeled_icon_group, ribbon_separator
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_STOP_COLOR = "#ef4444"  # as the Export tab's cancel
_FAST_ON_COLOR = "#38bdf8"  # the ribbon's tool blue
_RUN_TIP = (
    "Scan: read every frame once and save the mean, spread and pixel counts of each ROI (all ROIs, both sides, all wavelengths) "
    "to analysis/scan.h5 of this session. Progress and Cancel are in the status bar."
)
_FAST_TIP = (
    "Fast scan: read again only the cubes whose inputs changed since the last scan (ROIs, masks, chromatic correction, background, "
    "frame roles). Switch it off to read every cube again."
)
_FILE_TIP = "Open the folder of the scan file (analysis/scan.h5 of this session)."
FAST_SCAN_KEY = "analysis/fast_scan"


class AnalysisTab(QWidget):
    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    status_message = pyqtSignal(str)  # a message to show on the status bar (see `set_status`)
    info_changed = pyqtSignal()  # the "i" text changed (the panel re-reads it while this tab is open)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._status = "No scan yet."
        self._icon_cache: dict[tuple[str, str], QIcon] = {}

        self.live_toggle = LiveToggle(self)  # bound to the shared live / paused flag by `bind_live`
        self.run_button = self._icon_button()
        self.fast_scan_button = self._icon_button(_FAST_TIP, checkable=True)
        self.fast_scan_button.setChecked(True)
        self.folder_button = self._icon_button(_FILE_TIP)

        live_group, _ = labeled_icon_group(self, self.live_toggle, "Live")
        run_group, self._run_caption = labeled_icon_group(self, self.run_button, "Scan")
        fast_group, _ = labeled_icon_group(self, self.fast_scan_button, "Fast scan")
        self._folder_group, _ = labeled_icon_group(self, self.folder_button, "Scan file")
        self._folder_group.setVisible(False)  # until the owner says the file exists

        self._separators: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, group in enumerate((live_group, run_group, fast_group, self._folder_group)):
            if index:
                separator = ribbon_separator(self)
                self._separators.append(separator)
                layout.addWidget(separator)
            layout.addWidget(group)
        layout.addStretch(1)

        self.run_button.clicked.connect(lambda _checked=False: (self.stop_requested if self._running else self.run_requested).emit())
        self.fast_scan_button.toggled.connect(self._refresh_icons)
        self.folder_button.clicked.connect(lambda _checked=False: self.open_folder_requested.emit())
        self._refresh_icons()

    # -- driven by the owner -------------------------------------------------------------------------------------------------

    def bind_live(self, module: AnalysisLiveModule) -> None:
        """Share the live / paused flag with the Spectra and Sensorgram panels (the owner makes the module)."""
        self.live_toggle.bind(module)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.fast_scan_button.setEnabled(not self._running)
        self._refresh_icons()

    def set_status(self, text: str, *, announce: bool = False) -> None:
        """What the last scan did. `announce`: also show it on the status bar (a refusal or an outcome; not the quiet "No scan yet")."""
        self._status = text
        self._refresh_icons()
        self.info_changed.emit()
        if announce and text:
            self.status_message.emit(text)

    def status_text(self) -> str:
        return self._status

    def set_scan_file_available(self, available: bool) -> None:
        self._folder_group.setVisible(bool(available))
        self._sync_separators()

    def scan_file_button_visible(self) -> bool:
        return self._folder_group.isVisibleTo(self)

    def fast_scan(self) -> bool:
        return self.fast_scan_button.isChecked()

    def is_running(self) -> bool:
        return self._running

    def info_html(self) -> str:
        """The "i" icon's text while this tab is open: what the tab does, then what the last scan did."""
        return (
            "<b>Scan</b><br>Play: read every frame once and save the mean, spread and pixel counts of every ROI to this session's "
            "<i>analysis/scan.h5</i>; the Spectra and Sensorgram panels draw from it. While it runs the button is Stop (finished cubes stay "
            "saved). Fast scan: read again only the cubes whose inputs changed. Folder icon (when a scan file exists): open its folder."
            f"<br><br>{html.escape(self._status).replace(chr(10), '<br>')}"
        )

    def refresh_theme(self) -> None:
        self.live_toggle.refresh_theme()
        for button in (self.run_button, self.fast_scan_button, self.folder_button):
            style_general_icon_button(button)
        self._refresh_icons()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {get_active_theme().control_border};")

    # -- internals ------------------------------------------------------------------------------------------------------------

    def _icon_button(self, tooltip: str = "", *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        style_general_icon_button(button)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        return button

    def _icon(self, name: str, color: str) -> QIcon:
        key = (name, color)
        icon = self._icon_cache.get(key)
        if icon is None:
            icon = self._icon_cache[key] = load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        return icon

    def _sync_separators(self) -> None:
        # The separator before the folder group goes with it.
        if self._separators:
            self._separators[-1].setVisible(self._folder_group.isVisibleTo(self))

    def _refresh_icons(self, *_args: object) -> None:
        theme = get_active_theme()
        if self._running:
            self.run_button.setIcon(self._icon("player-stop", _STOP_COLOR))
            self.run_button.setToolTip("Stop the scan. Cubes that are already finished stay saved; scan again to continue.")
            self._run_caption.setText("Stop")
        else:
            self.run_button.setIcon(self._icon("player-play", theme.accent_green))
            self.run_button.setToolTip(f"{_RUN_TIP}\n\n{self._status}" if self._status else _RUN_TIP)
            self._run_caption.setText("Scan")
        self.fast_scan_button.setIcon(
            self._icon("player-skip-forward", _FAST_ON_COLOR if self.fast_scan_button.isChecked() else theme.text_dim))
        self.folder_button.setIcon(self._icon("folder-open", theme.text_dim))
        self._sync_separators()
