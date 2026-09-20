"""Cross-module undo/redo (see ``undo/manager.py`` for the design)."""

from .manager import FunctionCommand, UndoableCommand, UndoManager, undo_manager

__all__ = ["FunctionCommand", "UndoableCommand", "UndoManager", "undo_manager"]
