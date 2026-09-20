"""One shared undo/redo stack for every module (2026-09-20 design - not in
the original sketch/AGENTS.md, added when RoiToolbox's command methods
needed it and there was nowhere for it to live).

**Design decision, not a guess**: the maintainer explicitly asked for a
"proper (not minimal)" cross-module design here, not a RoiToolbox-only
stack. Modeled on this package's one other cross-cutting infrastructure
piece, :data:`diagnostics_hub` (``diagnostics/instrumented.py``) - a single
process-wide instance every module imports directly and calls into, rather
than a constructor-injected dependency threaded through every module's
``__init__``. Same reasoning applies here: undo/redo, like diagnostics, is
infrastructure, not domain state - it doesn't belong to any one module, and
AGENTS.md's "no module reaches into another's internals" boundary is about
*domain* state ownership (ROIs, masks, chromatic models), not this kind of
shared utility.

**One global stack, not one per module.** Matches the old app's single
Ctrl+Z history and ordinary user expectation: undoing "move ROI" then "crop
image" then "add landmark" should step back through all three in the order
they happened, regardless of which module did them. A per-module stack
would mean undoing "the last thing I did" requires knowing which module did
it.

**Explicit, typed undo/redo closures per operation, not a generic
before/after deep-clone of module state.** Mirrors this codebase's existing
"typed payload, never left to a naming-convention" rule for
``RoiCosmeticChange``/``RoiComputationalChange`` (``change_events.py``), and
avoids the old app's `_push_undo_point`'s real, measured cost: a `deepcopy`
of the *entire* app state (every ROI, every mask array, every chromatic
model) on every single edit, including a rapid burst of arrow-key nudges or
mouse-drag samples (see `_move_selected_rois`'s own `commit_undo=False`
batching workaround in `gui/roi_geometry_mixin.py` on `develop`/`main` -
that workaround exists specifically because the deepcopy was too expensive
to do on every intermediate drag sample). A command here instead captures
only the few fields its own operation actually changed.

**Each module authors its own undo/redo closures inline** in its command
methods (see `RoiToolbox.move_roi` for the first real example) and pushes
one :class:`FunctionCommand` - there is deliberately no per-operation
dataclass hierarchy (`MoveRoiCommand`, `DeleteRoiCommand`, ...): a module
already has direct access to its own private state in its own method body,
so a pair of closures there is simpler than a family of dataclasses that
would need back-references into that same private state anyway. Only
:class:`RoiToolbox` uses this today; Geometry/Mask/Chromatic/Background
adopt the identical pattern once their own command methods are built past
the `NotImplementedError` stub stage (AGENTS.md should be updated to
document this once a second module actually does).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from PyQt6.QtCore import QObject, pyqtSignal


@runtime_checkable
class UndoableCommand(Protocol):
    """Anything with a label plus reversible undo()/redo() is pushable -
    a module is free to implement this itself instead of using
    :class:`FunctionCommand`, e.g. for an operation whose redo isn't just
    "run the original mutation again" (rare; no current caller needs this).
    """

    label: str

    def undo(self) -> None: ...

    def redo(self) -> None: ...


class FunctionCommand:
    """The common case: a module already knows how to write its own
    undo/redo closures inline (they close over the module's own private
    state), so this just holds the two callables plus a label - no
    per-operation dataclass needed.
    """

    __slots__ = ("label", "_undo_fn", "_redo_fn")

    def __init__(self, label: str, undo_fn: Callable[[], None], redo_fn: Callable[[], None]) -> None:
        self.label = label
        self._undo_fn = undo_fn
        self._redo_fn = redo_fn

    def undo(self) -> None:
        self._undo_fn()

    def redo(self) -> None:
        self._redo_fn()


@dataclass
class _BatchCommand:
    """Coalesces every command pushed between begin_batch()/end_batch()
    into one undo-stack entry - e.g. a mouse-drag's many intermediate
    move_roi calls become a single "Move ROI" undo step, mirroring the old
    app's prepare/commit-snapshot pattern for the same reason (one undo
    step per user gesture, not per intermediate sample)."""

    label: str
    commands: list[UndoableCommand] = field(default_factory=list)

    def undo(self) -> None:
        for command in reversed(self.commands):
            command.undo()

    def redo(self) -> None:
        for command in self.commands:
            command.redo()


class UndoManager(QObject):
    """One shared undo/redo stack. Modules import the module-level
    :data:`undo_manager` instance below rather than constructing their own
    (see module docstring) - the class itself stays independently
    constructible so a test can build an isolated instance instead of
    reaching for the shared one, and to keep this file importable with zero
    other-module dependency.
    """

    changed = pyqtSignal()  # can_undo/can_redo state changed - cosmetic, UI-only (menu/toolbar enable-state)

    def __init__(self, parent: QObject | None = None, *, max_depth: int = 200) -> None:
        super().__init__(parent)
        self._undo_stack: list[UndoableCommand] = []
        self._redo_stack: list[UndoableCommand] = []
        self._max_depth = max_depth
        self._active_batch: _BatchCommand | None = None

    # -- command entry point ---------------------------------------------

    def push(self, command: UndoableCommand) -> None:
        """Record `command` as already-applied - the caller performs its
        own mutation first, then calls push(); this never calls redo()
        itself. Pushing during an open batch (see begin_batch) appends to
        that batch instead of the main stack. Any push clears the redo
        stack (the old app's own behavior: redoing past a fresh edit would
        silently resurrect a change the user already abandoned)."""
        if self._active_batch is not None:
            self._active_batch.commands.append(command)
            return
        self._undo_stack.append(command)
        if len(self._undo_stack) > self._max_depth:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.changed.emit()

    # -- batching (mirrors the old app's prepare/commit drag pattern) ----

    def begin_batch(self, label: str) -> None:
        """Start coalescing every push() until end_batch() into one entry.
        Nested begin_batch calls raise rather than silently overwriting the
        pending batch - the old app's equivalent bug class (a forgotten
        commit leaving a stale pending snapshot) should fail loudly here
        instead of silently dropping history.
        """
        if self._active_batch is not None:
            raise RuntimeError(f"Undo batch '{self._active_batch.label}' is already open.")
        self._active_batch = _BatchCommand(label=label)

    def end_batch(self) -> None:
        """Close the current batch and push it as one undo-stack entry - a
        no-op (not an error) if nothing was actually pushed during the
        batch, so e.g. a drag gesture that didn't end up moving anything
        doesn't leave a useless no-op undo step."""
        batch = self._active_batch
        if batch is None:
            raise RuntimeError("end_batch() called with no open batch.")
        self._active_batch = None
        if batch.commands:
            self.push(batch)

    def cancel_batch(self) -> None:
        """Discard the current batch without pushing it - e.g. an
        Escape-cancelled drag."""
        self._active_batch = None

    # -- undo/redo ---------------------------------------------------------

    def undo(self) -> None:
        if not self._undo_stack:
            return
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        self.changed.emit()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        command = self._redo_stack.pop()
        command.redo()
        self._undo_stack.append(command)
        self.changed.emit()

    # -- query interface (menu/toolbar enable-state) ------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_label(self) -> str | None:
        return self._undo_stack[-1].label if self._undo_stack else None

    @property
    def redo_label(self) -> str | None:
        return self._redo_stack[-1].label if self._redo_stack else None

    def clear(self) -> None:
        """Wipe all history - e.g. on loading a new dataset, where "undo"
        past the load boundary makes no sense. Also the right call for a
        test's setUp/tearDown when reusing the shared `undo_manager`
        instance across tests, to avoid cross-test history leaking."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._active_batch = None
        self.changed.emit()


# One process-wide instance, matching diagnostics_hub's exact pattern -
# modules import this rather than constructing their own (see module
# docstring for why: undo, like diagnostics, is cross-cutting infrastructure,
# not domain state any one module owns).
undo_manager = UndoManager()
