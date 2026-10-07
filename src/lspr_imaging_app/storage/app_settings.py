"""App-level settings - the one thing that isn't scoped to a dataset or a
session (2026-09-26): which dataset to reopen on launch, theme, window
geometry, and named panel-layout-preset blobs.

Before this module, the rewrite had **no app-level persistence at all** -
`panels/layout_presets.py`'s own docstring flagged "not persisted across
restarts yet - no app-level settings/QSettings layer in the rewrite" for
both theme and layout presets. This is that layer, built once so every
future app-level setting is one more dataclass field, not a new mechanism.

**A JSON file, not `QSettings`** - same reasoning `storage/session.py` is
Qt-free: testable with zero Qt, and inspectable/resettable like the other
settings files this suite already writes. Lives in the shared per-user
suite config directory (`platformdirs.user_config_dir("lspr-suite")`) that
`lspr_acq_shell.user_profile` already uses for `lspr_settings.json`/
`lspr_users.json`, under its own filename so it can collide with neither
that nor the stable LSPRi app's own `QSettings("LSPR", "LSPRImaging")`
registry key while both are launchable side by side from the Suite
Launcher.

**What is deliberately dataset/session-scoped instead, and stays out of
this file**: which *session* is active for a given dataset -
`storage/session_index.py`'s `sessions/index.json` lives inside the
dataset's own `home` folder, not here, so that choice travels with the
dataset (e.g. copied to another machine) rather than being tied to one
machine's app settings. This file only remembers the last dataset's
**path**, which is inherently machine-specific.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from platformdirs import user_config_dir

from .session import write_json_file
from .ui_state_keys import migrate_legacy_fields

logger = logging.getLogger(__name__)

APP_SETTINGS_SCHEMA_NAME = "lspri_rewrite_app_settings"
APP_SETTINGS_SCHEMA_VERSION = "1.1"
# 1.1 (2026-10-07): 26 per-panel fields moved into `ui_state` (see `ui_state_keys.py`);
# a 1.0 file is migrated on load, additive for older readers (unknown keys are ignored).

_SETTINGS_FILENAME = "lspri_eva_rewrite_settings.json"


def default_settings_path() -> Path:
    return Path(user_config_dir("lspr-suite", appauthor=False)) / _SETTINGS_FILENAME


@dataclass
class AppSettings:
    """One field per concern - same "one field per owning thing" shape as
    `session.SessionState`, so a new app-level setting is one field plus one
    line in `load_app_settings`/`save_app_settings`, not a new mechanism."""

    last_dataset_folder: str | None = None
    auto_reopen_last_dataset: bool = True
    theme: str = "dark"
    # base64-encoded QByteArray from QMainWindow.saveGeometry()/saveState() -
    # kept as plain strings here so this file stays Qt-free and testable
    # without a QApplication; the caller (app_rewrite.py) does the QByteArray
    # <-> base64 conversion.
    main_window_geometry: str | None = None
    main_window_state: str | None = None
    active_layout_preset: str | None = None
    layout_presets: dict[str, str] = field(default_factory=dict)
    auto_apply_preset_on_stage_change: bool = False
    # `WorkflowStage.name` (e.g. "IMAGE_TOOLS") of the top-level Workflow
    # accordion section left open at last quit - see
    # `panels/workflow/panel.py:WorkflowPanel`. A plain string, not the enum
    # itself, so this file stays Qt/app-free; `None` means "no saved stage
    # yet", which the reader falls back to each section's own hardcoded
    # default for (currently: Dataset starts open).
    active_workflow_stage: str | None = None
    # Expand/collapse state of *nested* Workflow sections (e.g. "Transforms"
    # under Image tools), keyed by `"<WorkflowStage.name>:<section title>"`
    # (see `panels/workflow/panel.py`'s `_subsection_key`). Unlike the single
    # top-level stage above, several nested sections can be open at once, so
    # this needs a dict rather than one string. A key missing from this dict
    # (first-ever launch, or a section title/stage that no longer exists)
    # falls back to that section's own hardcoded `expanded=` default, the
    # same graceful-degradation `active_workflow_stage` already relies on.
    expanded_subsections: dict[str, bool] = field(default_factory=dict)
    # Everything a user can change in a control (Export options, toggles, picks,
    # the Histogram's display options, the Image panel's view and overlays, the
    # Chromatic tab's values, the highlight range...), as "area/name" -> JSON
    # value; see `panels/ui_state.py`. The keys wired by hand, their defaults and
    # the retired fields they replaced are one table: `storage/ui_state_keys.py`.
    ui_state: dict[str, object] = field(default_factory=dict)


def _decode(payload: dict) -> AppSettings:
    known = {f.name for f in fields(AppSettings)}
    kwargs = {key: value for key, value in payload.items() if key in known}
    if not isinstance(kwargs.get("layout_presets"), dict):
        kwargs.pop("layout_presets", None)
    if not isinstance(kwargs.get("expanded_subsections"), dict):
        kwargs.pop("expanded_subsections", None)
    if not isinstance(kwargs.get("ui_state"), dict):
        kwargs.pop("ui_state", None)
    # Settings written before 2026-10-07 keep per-panel values as top-level fields.
    ui_state = dict(kwargs.get("ui_state", {}))
    if migrate_legacy_fields(payload, ui_state):
        kwargs["ui_state"] = ui_state
    try:
        return replace(AppSettings(), **kwargs)
    except (TypeError, ValueError):
        logger.warning("Ignoring an undecodable app settings file; using defaults", exc_info=True)
        return AppSettings()


def load_app_settings(path: Path | None = None) -> AppSettings:
    """Read the app settings file, or defaults if there isn't one yet.

    A missing file is the ordinary first-ever-launch case, not an error -
    same contract as `session.load_session`'s `None`. A file that is
    unreadable, isn't this schema, or is a future major version also falls
    back to defaults (unlike `load_session`, which raises): losing a
    dataset-scoped session file would look identical to a dataset that was
    never analyzed, which matters; losing app-level convenience settings
    (last folder, theme, window size) does not - starting fresh is a safe,
    silent recovery here, not a data-loss risk."""
    path = path if path is not None else default_settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read app settings at %s; using defaults", path, exc_info=True)
        return AppSettings()
    if not isinstance(payload, dict) or payload.get("schema_name") != APP_SETTINGS_SCHEMA_NAME:
        logger.warning("App settings at %s are not recognised; using defaults", path)
        return AppSettings()
    version = str(payload.get("schema_version", "0.0"))
    if version.split(".")[0] != APP_SETTINGS_SCHEMA_VERSION.split(".")[0]:
        logger.warning(
            "App settings at %s are schema version %s, which this build "
            "(supporting %s) cannot read; using defaults",
            path, version, APP_SETTINGS_SCHEMA_VERSION,
        )
        return AppSettings()
    return _decode(payload)


def save_app_settings(state: AppSettings, path: Path | None = None) -> Path:
    path = path if path is not None else default_settings_path()
    payload = {
        "schema_name": APP_SETTINGS_SCHEMA_NAME,
        "schema_version": APP_SETTINGS_SCHEMA_VERSION,
        **asdict(state),
    }
    write_json_file(path, payload)
    return path
