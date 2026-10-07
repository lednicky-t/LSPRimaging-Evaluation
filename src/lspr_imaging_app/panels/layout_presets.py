"""Named, user-editable panel-layout presets
(``docs/rewrite_gui_shell_design_2026-09.md`` §5).

Four built-in presets, each naming which of the five display docks it
shows (Workflow is outside this - always visible across every preset,
independent of preset choice, per the design doc §4). Ported *as an idea*,
not as code, from singleLSPR Acquisition's ``gui/main_window_state.py``
(named slots, each save-current-layout-editable, reset-to-default) - that
app's actual snapshot format (a hand-rolled per-field dict: splitter sizes,
visibility flags) predates real dock-widget layout there and had to
reconstruct geometry field by field. This app's docks (§1 of the design
doc) already serialize their own geometry/floating/tabification state
through ``QMainWindow.saveState()``, so a preset slot here is just that
blob, once the user has captured one.

**Built-in defaults are visibility-only, not a hand-authored geometry
blob.** Authoring a realistic default arrangement (sizes, split
orientation, tab order) directly in code would mean guessing at numbers no
one has actually looked at - instead, applying a preset that has never been
customized just shows/hides the right docks and leaves whatever geometry is
already on screen alone. The first time a user arranges a preset the way
they want and saves it (``LayoutPresetManager.save_current``), that
preset's slot gets the *real* ``saveState()`` blob and behaves exactly like
sLSPR acq's version from then on.

**Not persisted across restarts yet.** There is no app-level settings/
``QSettings`` layer in the rewrite (only ``storage/session.py``'s
per-dataset session state) - custom preset blobs saved with
``save_current`` live only in ``LayoutPresetManager``'s own memory for this
run. Flagged rather than silently limited; wiring this to real persistence
is separate future work once such a layer exists.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QActionGroup, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu

from .dock_container import PanelContainer
from .workflow import WorkflowStage

logger = logging.getLogger(__name__)

# Keyed by each display dock's PanelContainer title (see app_rewrite.py's
# build_main_window, which constructs the docks these names must match).
PRESET_PANELS: dict[str, frozenset[str]] = {
    "Image Tools": frozenset({"Image", "Histogram"}),
    "ROI": frozenset({"Image", "ROI / Groups"}),
    "Analysis": frozenset({"Image", "Spectra", "Sensorgram", "ROI / Groups"}),
    "Visualization": frozenset({"Image", "Spectra", "Sensorgram"}),
}
PRESET_NAMES: tuple[str, ...] = tuple(PRESET_PANELS)

# Renamed from "Results" 2026-09-24 (design doc §4a/§5) - that name now
# belongs to the Outputs Workflow stage below, an unrelated concept (export/
# visualization *settings*, not a panel arrangement); keeping both named
# "Results" would have collided in the View menu.

# All 5 workflow stages now map to a preset - Dataset intentionally reuses
# Image Tools' preset (maintainer's own call: browsing/loading a dataset
# doesn't need a different arrangement than Image Tools work does), and
# Outputs reuses Visualization (viewing/formatting results before export is
# the same display arrangement as viewing them for analysis).
STAGE_TO_PRESET: dict[WorkflowStage, str] = {
    WorkflowStage.DATASET: "Image Tools",
    WorkflowStage.IMAGE_TOOLS: "Image Tools",
    WorkflowStage.ROI_SELECTION: "ROI",
    WorkflowStage.ANALYSIS: "Analysis",
    WorkflowStage.OUTPUTS: "Visualization",
}


class LayoutPresetManager:
    """Applies/saves/resets the four named presets against one
    ``QMainWindow``'s docks. Owns no scientific state - purely window
    chrome, so it does not follow the sketch §7 QObject-module pattern
    reserved for domain modules."""

    def __init__(
        self,
        window: QMainWindow,
        workflow_dock: PanelContainer,
        docks_by_name: dict[str, PanelContainer],
        *,
        state_version: int = 0,
    ) -> None:
        self._window = window
        self._workflow_dock = workflow_dock
        self._docks = docks_by_name
        self._custom_blobs: dict[str, QByteArray] = {}
        self._current: str | None = None
        self._on_applied: list = []
        # Passed through to every saveState()/restoreState() call below - a
        # saved preset blob is exactly as vulnerable to a dock-topology
        # change as the plain main-window layout state is (see
        # app_rewrite.py's _DOCK_LAYOUT_STATE_VERSION docstring for the
        # full incident): restoreState() silently mis-applies an
        # old-topology blob's sizes onto the new tree's nodes rather than
        # rejecting it, which looks like a stuck/unresizable layout, not
        # like what it actually is.
        self._state_version = state_version

    def current(self) -> str | None:
        return self._current

    def apply(self, name: str) -> None:
        if name not in PRESET_PANELS:
            raise ValueError(f"Unknown layout preset: {name!r}")
        blob = self._custom_blobs.get(name)
        if blob is not None:
            self._window.restoreState(blob, self._state_version)
            # restoreState() restores whatever visibility the blob was
            # saved with - re-assert Workflow visible regardless, since
            # it's meant to survive independently of preset choice (§4).
            self._workflow_dock.setVisible(True)
        else:
            wanted = PRESET_PANELS[name]
            for panel_name, dock in self._docks.items():
                dock.setVisible(panel_name in wanted)
        self._current = name
        for callback in self._on_applied:
            callback(name)

    def save_current(self, name: str | None = None) -> None:
        """Capture the live dock arrangement into *name*'s slot (or the
        currently active preset, if any). A no-op if there is no active
        preset and none was given - saving "the current layout" only means
        something relative to a named slot."""
        target = name or self._current
        if target is None:
            logger.debug("save_current called with no active preset - nothing to save into")
            return
        self._custom_blobs[target] = self._window.saveState(self._state_version)

    def reset_to_default(self, name: str) -> None:
        """Discard *name*'s saved blob, reverting it to the visibility-only
        built-in default. Re-applies immediately if it's the active preset."""
        self._custom_blobs.pop(name, None)
        if self._current == name:
            self.apply(name)

    def on_applied(self, callback) -> None:
        """Register a callback(name: str) run after every apply() - used by
        wire_view_menu to keep the menu's checked action in sync, including
        when auto-apply (not a menu click) is what triggered it."""
        self._on_applied.append(callback)

    def custom_blobs(self) -> dict[str, QByteArray]:
        """Every preset slot that has been saved-to at least once, keyed by
        preset name - what `storage/app_settings.py`'s persistence layer
        reads to serialize (2026-09-26)."""
        return dict(self._custom_blobs)

    def load_custom_blobs(self, blobs: dict[str, QByteArray]) -> None:
        """Restore previously-saved preset blobs (2026-09-26) - call once at
        startup, before any preset is applied. A real setter rather than
        reaching into `_custom_blobs` from outside: this class owns that
        dict, matching CLAUDE.md's "no module reaches into another's
        internals" rule even though this particular class is chrome, not a
        domain module."""
        self._custom_blobs = dict(blobs)


def wire_view_menu(
    view_menu: QMenu,
    options_menu: QMenu,
    window: QMainWindow,
    workflow_panel,
    workflow_dock: PanelContainer,
    docks_by_name: dict[str, PanelContainer],
    *,
    initial_auto_apply: bool = False,
    on_auto_apply_changed=None,
    state_version: int = 0,
) -> LayoutPresetManager:
    """Builds View -> Panel Presets (apply/save/reset, Ctrl+Shift+1-4) and
    the Options menu's auto-apply-on-stage-change toggle (design doc §5).

    The toggle lives in Options rather than a Preferences dialog because
    the rewrite has no Preferences dialog yet - stands in for one until it
    exists, flagged here rather than silently placed as if this were the
    final location.

    `initial_auto_apply`/`on_auto_apply_changed` (2026-09-26): this toggle's
    checked state is now persisted by `storage/app_settings.py` - the
    caller reads the saved value in and gets told about every change so it
    can write it back out; this function still owns no persistence itself,
    same "chrome, not storage" split every other module here follows."""
    manager = LayoutPresetManager(window, workflow_dock, docks_by_name, state_version=state_version)

    presets_menu = view_menu.addMenu("Panel Presets")
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[str, "object"] = {}
    for index, name in enumerate(PRESET_NAMES, start=1):
        action = presets_menu.addAction(name)
        action.setCheckable(True)
        action.setShortcut(QKeySequence(f"Ctrl+Shift+{index}"))
        action.triggered.connect(lambda checked, n=name: manager.apply(n) if checked else None)
        group.addAction(action)
        actions[name] = action
    presets_menu.addSeparator()
    save_action = presets_menu.addAction("Save Current Layout to Active Preset")
    save_action.triggered.connect(lambda: manager.save_current())
    reset_action = presets_menu.addAction("Reset Active Preset to Default")
    reset_action.triggered.connect(lambda: manager.reset_to_default(manager.current()) if manager.current() else None)

    def sync_checked_action(name: str) -> None:
        action = actions.get(name)
        if action is not None:
            action.setChecked(True)

    manager.on_applied(sync_checked_action)

    auto_apply_action = options_menu.addAction("Automatically Apply Layout Preset on Stage Change")
    auto_apply_action.setCheckable(True)
    # Off by default on a truly fresh settings file (design doc §5) - never
    # surprises a new user; a returning one gets back whatever they last set.
    auto_apply_action.setChecked(initial_auto_apply)

    def on_stage_changed(stage: WorkflowStage) -> None:
        if not auto_apply_action.isChecked():
            return
        preset_name = STAGE_TO_PRESET.get(stage)
        if preset_name is not None:
            manager.apply(preset_name)

    workflow_panel.stage_changed.connect(on_stage_changed)

    if on_auto_apply_changed is not None:
        auto_apply_action.toggled.connect(on_auto_apply_changed)

    return manager
