"""The Image panel's "Analysis" ribbon tab (2026-10-24, maintainer request: move the Workflow panel's Analysis section here; the Workflow
panel is on its way out).

Screen purpose: start and stop the scan (step 1 of the analysis: mean / STD / pixel counts per ROI, cube, wavelength and side, saved to
``analysis/scan.h5`` of the session), choose how much of it to redo, and reach the file.

Layout (one ribbon row, captioned groups):

    [play / stop]  |  [skip / stop]  |  [bolt]  |  [folder]
        Scan          Fast scan         Mode      Scan file

- **Scan:** play reads every cube again. While it runs the same button is a red Stop (as the Export tab's), which stops it like the
  Cancel in the status bar's task indicator. Cubes that were finished stay saved.
- **Fast scan:** skip-forward reads again only the cubes whose inputs changed since the last scan (``analysis/scan_fingerprint.py``). A
  button since 2026-10-27 (it was a toggle, ``analysis/fast_scan``; that saved value is not used any more). While a scan runs, the button
  that started it is the Stop and the other one is disabled.
- **Mode** (2026-10-27): Normal or Performance, a pick-up menu (``analysis_mode_picker.py``). Performance keeps the worker processes ready
  between scans so that a scan starts at once (``analysis/scan_pool.py``); Normal starts them for each scan. Remembered; disabled while a scan runs.
- **Scan file:** opens the folder that holds the scan file the Spectra and Sensorgram panels are showing. Visible only while the file exists.
- **Text:** what the last scan did ("scanned in ...", "up to date", why it could not start) is the "i" icon's text while this tab is open and the
  Scan button's tooltip; a message that must be seen (a refusal, an outcome) also goes to the status bar. Progress and Cancel are the
  task indicator's.

A display and a few buttons, nothing more: the buttons only emit requests, and the owner (``app_rewrite._wire_analysis_scan``) starts
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
from .analysis_mode_picker import DESCRIPTIONS, LABELS, AnalysisModePicker
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_STOP_COLOR = "#ef4444"  # as the Export tab's cancel
_FAST_COLOR = "#38bdf8"  # the ribbon's tool blue
_RUN_TIP = (
    "Scan: read every frame of every cube again and save the mean, spread and pixel counts of each ROI (all ROIs, both sides, all "
    "wavelengths) to analysis/scan.h5 of this session. Progress and Cancel are in the status bar."
)
_FAST_TIP = (
    "Fast scan: read again only the cubes whose inputs changed since the last scan (ROIs, masks, chromatic correction, background, "
    "frame roles). The same numbers as a full Scan, less waiting."
)
_STOP_TIP = "Stop the scan. Cubes that are already finished stay saved; scan again to continue."
_FILE_TIP = "Open the folder of the scan file (analysis/scan.h5 of this session)."


class AnalysisTab(QWidget):
    run_requested = pyqtSignal(bool)  # True = Fast scan (only what is out of date), False = Scan (every cube)
    stop_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)  # "normal" | "performance": the user picked a mode (the owner tells the scan task)
    status_message = pyqtSignal(str)  # a message to show on the status bar (see `set_status`)
    info_changed = pyqtSignal()  # the "i" text changed (the panel re-reads it while this tab is open)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._running_fast = False  # which of the two run buttons started the scan in progress (it is the Stop button meanwhile)
        self._status = "No scan yet."
        self._icon_cache: dict[tuple[str, str], QIcon] = {}

        self.live_toggle = LiveToggle(self)  # bound to the shared live / paused flag by `bind_live`
        self.run_button = self._icon_button()
        self.fast_run_button = self._icon_button()
        self.mode_picker = AnalysisModePicker(self)
        self.folder_button = self._icon_button(_FILE_TIP)

        live_group, _ = labeled_icon_group(self, self.live_toggle, "Live")
        run_group, self._run_caption = labeled_icon_group(self, self.run_button, "Scan")
        fast_group, self._fast_caption = labeled_icon_group(self, self.fast_run_button, "Fast scan")
        mode_group, _ = labeled_icon_group(self, self.mode_picker, "Mode")
        self._folder_group, _ = labeled_icon_group(self, self.folder_button, "Scan file")
        self._folder_group.setVisible(False)  # until the owner says the file exists

        self._separators: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, group in enumerate((live_group, run_group, fast_group, mode_group, self._folder_group)):
            if index:
                separator = ribbon_separator(self)
                self._separators.append(separator)
                layout.addWidget(separator)
            layout.addWidget(group)
        layout.addStretch(1)

        self.run_button.clicked.connect(lambda _checked=False: self._clicked(fast=False))
        self.fast_run_button.clicked.connect(lambda _checked=False: self._clicked(fast=True))
        self.folder_button.clicked.connect(lambda _checked=False: self.open_folder_requested.emit())
        self.mode_picker.mode_changed.connect(self._on_mode_picked)
        self._refresh_icons()

    # -- driven by the owner -------------------------------------------------------------------------------------------------

    def bind_live(self, module: AnalysisLiveModule) -> None:
        """Share the live / paused flag with the Spectra and Sensorgram panels (the owner makes the module)."""
        self.live_toggle.bind(module)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self.mode_picker.setEnabled(not self._running)  # the workers of a running scan are not changed under it
        self._refresh_icons()

    def mode(self) -> str:
        return self.mode_picker.mode()

    def set_mode(self, mode: str, *, announce: bool = False) -> None:
        """Show ``mode`` (a remembered one at launch: ``announce`` False, the owner sets the task itself)."""
        self.mode_picker.set_mode(mode, announce=announce)
        self.info_changed.emit()

    def set_pool_state(self, state: str, workers: int = 0) -> None:
        """What the worker pool of Performance mode is doing (from the scan task): shown in the Mode tooltip and the "i" text; a failure is announced."""
        self.mode_picker.set_pool_state(state, workers)
        self.info_changed.emit()
        if state == "failed":
            self.status_message.emit(self.mode_picker.pool_state_line())

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

    def is_running(self) -> bool:
        return self._running

    def info_html(self) -> str:
        """The "i" icon's text while this tab is open: what the tab does, then what the last scan did."""
        return (
            "<b>Scan</b><br>Scan (play): read every frame of every cube again and save the mean, spread and pixel counts of every ROI to "
            "this session's <i>analysis/scan.h5</i>; the Spectra and Sensorgram panels draw from it. Fast scan (skip): read again only "
            "the cubes whose inputs changed. While a scan runs, the button that started it is Stop (finished cubes stay saved). Folder "
            "icon (when a scan file exists): open its folder."
            f"<br><br><b>Mode</b><br>{html.escape(LABELS[self.mode_picker.mode()])}: {html.escape(DESCRIPTIONS[self.mode_picker.mode()])}."
            f"{('<br>' + html.escape(self.mode_picker.pool_state_line())) if self.mode_picker.pool_state_line() else ''}"
            f"<br><br>{html.escape(self._status).replace(chr(10), '<br>')}"
        )

    def refresh_theme(self) -> None:
        self.live_toggle.refresh_theme()
        for button in (self.run_button, self.fast_run_button, self.folder_button):
            style_general_icon_button(button)
        self.mode_picker.refresh_theme()
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

    def _on_mode_picked(self, mode: str) -> None:
        self.info_changed.emit()
        self.mode_changed.emit(mode)

    def _clicked(self, *, fast: bool) -> None:
        if self._running:  # only the button that started the run is enabled, so a click on it means Stop
            self.stop_requested.emit()
            return
        self._running_fast = fast
        self.run_requested.emit(fast)

    def _refresh_icons(self, *_args: object) -> None:
        theme = get_active_theme()
        self._set_run_button(
            self.run_button, self._run_caption, stop=self._running and not self._running_fast, other_busy=self._running and self._running_fast,
            icon="player-play", color=theme.accent_green, text="Scan", tip=_RUN_TIP,
        )
        self._set_run_button(
            self.fast_run_button, self._fast_caption, stop=self._running and self._running_fast, other_busy=self._running and not self._running_fast,
            icon="player-skip-forward", color=_FAST_COLOR, text="Fast scan", tip=_FAST_TIP,
        )
        self.folder_button.setIcon(self._icon("folder-open", theme.text_dim))
        self._sync_separators()

    def _set_run_button(self, button: QToolButton, caption, *, stop: bool, other_busy: bool, icon: str, color: str, text: str, tip: str) -> None:
        """A run button is idle (its own icon), the Stop button (it started the scan in progress) or greyed out (the other one did)."""
        if stop:
            button.setIcon(self._icon("player-stop", _STOP_COLOR))
            button.setToolTip(_STOP_TIP)
            caption.setText("Stop")
        else:
            button.setIcon(self._icon(icon, get_active_theme().text_dim if other_busy else color))
            button.setToolTip(f"{tip}\n\n{self._status}" if self._status else tip)
            caption.setText(text)
        button.setEnabled(not other_busy)
