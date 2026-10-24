"""``AnalysisLiveModule``: are the analysis displays live or paused? (2026-10-24)

One flag shared by the Analysis tab, the Spectra panel, the Sensorgram panel and the scan-results checker. **Live** (the start state): the plots
follow every change (a ROI moved, a setting changed, a cube picked) and the background check of which cubes are out of date runs after a
change. **Paused**: none of that starts; the plots keep showing what they show, and the owners remember that something was asked. Back to
live, each owner catches up once. A scan the user starts still runs and still writes its file; its results wait for live like any other change.

Display state only: it is not saved with the session and not remembered between runs (a pause is a working state; starting paused would
open the app with empty plots). The module holds the flag and tells; what to hold back is each owner's decision.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class AnalysisLiveModule(QObject):
    live_changed = pyqtSignal(bool)  # True = live, False = paused

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._live = True

    def is_live(self) -> bool:
        return self._live

    def set_live(self, live: bool) -> None:
        live = bool(live)
        if live == self._live:
            return
        self._live = live
        self.live_changed.emit(live)
