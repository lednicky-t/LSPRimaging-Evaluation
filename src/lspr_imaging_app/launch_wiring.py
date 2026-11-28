"""Small pieces of launch wiring that would not fit into `app_rewrite.py` (its size budget, `test_lspri_panel_size_budget.py`).

* `start_gui_stall_watchdog`: the GUI-thread stall recorder (`applog/gui_stall.py`).
* `wire_file_stats_after_startup`: the dataset's size label (remembered between launches, counted after the start-up).

Imports only PyQt6, `applog` and `storage`; the saved-UI-state store and the modules are passed in.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .applog import LogSystem
from .applog.gui_stall import BEAT_MS
from .storage.ui_state_keys import FILE_STATS, read_file_stats, with_file_stats

FILE_STATS_FALLBACK_MS = 60_000  # the dataset's file count waits this long for the start-up to end


class _Store(Protocol):
    def get(self, key: str, default: object = None) -> object: ...
    def set(self, key: str, value: object) -> None: ...


class _Dataset(Protocol):
    def set_file_stats_memory(self, recall: Callable, remember: Callable) -> None: ...
    def hold_file_stats_walk(self, hold: bool) -> None: ...


class _Startup(Protocol):
    task_finished: object  # a pyqtSignal(str, str, str): task id, outcome, message


def start_gui_stall_watchdog(app: QApplication, system: LogSystem) -> None:
    """One `gui.stall` DEBUG record per GUI-thread block over 300 ms, with the Python function it was in (`applog/gui_stall.py`).
    Call right before `app.exec()`; the GUI thread beats from a 50 ms timer, a plain thread watches. Does nothing without a log system."""
    watchdog = system.gui_stall
    if watchdog is None:
        return
    beat_timer = QTimer(app)
    beat_timer.setInterval(BEAT_MS)
    beat_timer.timeout.connect(watchdog.beat)
    beat_timer.start()
    watchdog.start()
    app.aboutToQuit.connect(watchdog.stop)


def wire_file_stats_after_startup(dataset: _Dataset, startup: _Startup, ui_state: _Store) -> None:
    """The dataset's size label (2026-11-28). Counting the files of a big OME-Zarr takes ~21 s even on an idle machine (89 GB in 71,720
    files in 146,042 folders) and 41 s during a launch, so (a) the count of the last time is shown at once (kept in the saved UI state,
    one entry per dataset folder) and replaced when the new one is in, and (b) the new count starts when the start-up has ended, not
    while the dataset, session and scan are loading. A launch whose start-up never ends (nothing reopened, never finished) lets the
    count run after `FILE_STATS_FALLBACK_MS`."""
    dataset.set_file_stats_memory(
        lambda folder: read_file_stats(ui_state, folder),
        lambda folder, stats: ui_state.set(FILE_STATS, with_file_stats(ui_state, folder, stats)),
    )
    dataset.hold_file_stats_walk(True)

    def release(*_args: object) -> None:  # the start-up's signal carries (task id, outcome, message); any outcome ends the wait
        dataset.hold_file_stats_walk(False)

    startup.task_finished.connect(release)  # type: ignore[attr-defined]
    QTimer.singleShot(FILE_STATS_FALLBACK_MS, release)
