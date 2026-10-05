"""Remember small UI choices across restarts with one line per control.

`UiStateStore.bind(key, widget)` puts a checkbox / checkable button / spin box
/ combo box back to its saved value and saves every later change. For
state that does not live in a widget (a module's mode, a toggle inside a
custom class) use `get`/`set` directly.

Why a store and not one `AppSettings` field per control: the maintainer's
rule is that every user-changeable control is remembered by default
(2026-10-05), which would otherwise mean a field, a constructor argument and a
signal for each one. Values live in `AppSettings.ui_state` (a plain
JSON dict), written at most once per short pause so sliders and spin-box
arrows do not rewrite the file on every tick.

Keys are short paths ("export/chunk_size"). Values must be JSON-safe (bool,
int, float, str, None, or lists/dicts of those). A saved value that no longer
fits its widget (a removed combo entry, an out-of-range number) is ignored and
the widget keeps its default.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QAbstractButton, QComboBox, QDoubleSpinBox, QSpinBox, QWidget

logger = logging.getLogger(__name__)

_SAVE_DELAY_MS = 400


class UiStateStore(QObject):
    def __init__(
        self,
        values: dict[str, object] | None = None,
        on_changed: Callable[[dict[str, object]], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._values: dict[str, object] = dict(values or {})
        self._on_changed = on_changed
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object) -> None:
        if key in self._values and self._values[key] == value:
            return
        self._values[key] = value
        self._dirty = True
        self._timer.start()

    def flush(self) -> None:
        """Write now (also called at quit so the last change is never lost)."""
        self._timer.stop()
        if self._dirty and self._on_changed is not None:
            self._dirty = False
            self._on_changed(dict(self._values))

    def bind(self, key: str, widget: QWidget) -> None:
        """Restore `widget` from the saved value (if any and valid), then save its changes."""
        if isinstance(widget, QAbstractButton):
            if not widget.isCheckable():
                raise TypeError(f"{key}: only checkable buttons hold state")
            saved = self._values.get(key)
            if isinstance(saved, bool):
                widget.setChecked(saved)
            widget.toggled.connect(lambda checked: self.set(key, bool(checked)))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            saved = self._values.get(key)
            if isinstance(saved, (int, float)) and not isinstance(saved, bool):
                if widget.minimum() <= saved <= widget.maximum():
                    widget.setValue(saved)
                else:
                    logger.warning("Ignoring saved %s=%r: outside the control's range", key, saved)
            widget.valueChanged.connect(lambda value: self.set(key, value))
        elif isinstance(widget, QComboBox):
            saved = self._values.get(key)
            index = widget.findData(saved) if saved is not None else -1
            if index >= 0:
                widget.setCurrentIndex(index)
            widget.currentIndexChanged.connect(lambda _i: self.set(key, widget.currentData()))
        else:
            raise TypeError(f"{key}: no persistence rule for {type(widget).__name__}")
