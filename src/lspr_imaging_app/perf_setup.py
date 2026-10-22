"""LSPRi's side of `lspr_core.perf` (2026-10-22): where the learned settings live, and the user's choices from Edit -> Preferences.

`lspr_core.perf` decides how many workers a parallel job (export, scan) may use and tunes the number while the job runs
(`docs/performance_tuning.md` at the repo root). This file is the only place that knows LSPRi's paths and its saved-state keys:

* the learned settings are one small JSON file next to the app settings (`perf_profile.json`): the worker counts that "Tune automatically"
  found. Standard mode uses them, the Preferences button "Forget saved counts" deletes them, and the file may be deleted at any time;
* the four preferences are remembered in the UI-state store (`storage/ui_state_keys.py`, `perf/...`), like every other control.

`install_runtime` runs in `main()` only (like `configure_logging`): a test that builds the window gets the plain default runtime and
never touches the learned-settings file of the person running the tests. No Qt in this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from lspr_core.perf import PerfPreferences, PerfRuntime, ProfileStore, get_runtime, set_runtime
from platformdirs import user_config_dir

from .storage.ui_state_keys import PERF_LOW_PRIORITY, PERF_MANUAL_WORKERS, PERF_MODE, PERF_RESERVED_CORES, read

PROFILE_FILENAME = "perf_profile.json"
EXPORT_TASK_KEY = "export.ome_zarr"


class _Store(Protocol):
    def get(self, key: str, default: object = None) -> object: ...


class _Writable(_Store, Protocol):
    def set(self, key: str, value: object) -> None: ...


def perf_profile_path() -> Path:
    """Next to `lspri_eva_rewrite_settings.json`, in the shared suite config folder."""
    return Path(user_config_dir("lspr-suite", appauthor=False)) / PROFILE_FILENAME


def read_perf_preferences(store: _Store) -> PerfPreferences:
    """The saved choices (a wrong type or a value out of range falls back to the default)."""
    return PerfPreferences(
        mode=str(read(store, PERF_MODE)),
        manual_workers=int(read(store, PERF_MANUAL_WORKERS)),
        reserved_cores=int(read(store, PERF_RESERVED_CORES)),
        low_priority=bool(read(store, PERF_LOW_PRIORITY)),
    ).normalized()


def write_perf_preferences(store: _Writable, preferences: PerfPreferences) -> list[str]:
    """Save the choices that changed; returns the names of those (nothing is written for an unchanged OK)."""
    saved = read_perf_preferences(store)
    new = preferences.normalized()
    changed: list[str] = []
    for key, name, old_value, new_value in (
        (PERF_MODE, "mode", saved.mode, new.mode),
        (PERF_MANUAL_WORKERS, "manual_workers", saved.manual_workers, new.manual_workers),
        (PERF_RESERVED_CORES, "reserved_cores", saved.reserved_cores, new.reserved_cores),
        (PERF_LOW_PRIORITY, "low_priority", saved.low_priority, new.low_priority),
    ):
        if new_value != old_value:
            store.set(key.key, new_value)
            changed.append(name)
    return changed


def install_runtime(store: _Store) -> PerfRuntime:
    """Called once from `main()`: the app's runtime, with the saved preferences and the learned-settings file."""
    runtime = PerfRuntime(read_perf_preferences(store), store=ProfileStore(perf_profile_path()))
    set_runtime(runtime)
    return runtime


def apply_saved_preferences(store: _Store) -> None:
    """Window start-up: the runtime (the installed one, or the default of a test) takes the saved preferences."""
    get_runtime().set_preferences(read_perf_preferences(store))


_TASK_NAMES = {EXPORT_TASK_KEY: "export", "mask.scan.screen": "scan", "analysis.scan": "analysis"}


def saved_count() -> int:
    """How many tuned counts are saved for this computer (enables the "Forget saved counts" button)."""
    return len(get_runtime().learned_all())


def status_text() -> str:
    """Short lines for the Preferences dialog: this computer, and the counts tuning saved for it (Standard mode uses them)."""
    runtime = get_runtime()
    lines = [f"This computer: {runtime.last_system().describe()}."]
    saved = runtime.learned_all()
    if saved:
        parts = []
        for task, kind, learned in saved:
            name = _TASK_NAMES.get(task, task)
            where = f" to a {kind} disk" if kind and kind != "unknown" and task == EXPORT_TASK_KEY else ""
            parts.append(f"{name}{where}: {learned.workers} workers ({learned.rate:.0f}/s)")
        lines.append("Saved by tuning: " + "; ".join(parts) + ".")
    return "\n".join(lines)
