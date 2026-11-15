"""How a selection looks: the colour and the infill of each of its three outlines (2026-11-16, maintainer request: a View pop-up in the selection
toolbar, "able to change the colour of the Venn borders, and the opacity, only 5 steps, when 0 then no fill; the colours are for the borders and
also the infill; the defaults are the colours the borders have now; synced with the Venn icons").

State only: per role (`selection_palette.PAST` / `PRESENT` / `RESULT`) a colour, a fill step (0 .. `len(FILL_OPACITY_STEPS) - 1`, step 0 = no
fill) and whether the border is drawn (the maintainer's addition the same day: with the borders off only the static infill is left and the crawl
stops, which is what makes a big filled selection cheap). It is display state, owned by whoever draws, like the overlay tints (`histogram_highlight_overlay_controls.py` says why): no selection logic
reads it. Three listeners, all through `changed`:

* the outlines on the image (`selection_outlines.py`): dashes and fill,
* the Venn mode icons of the toolbar (`combine_mode_icons.py`): the left circle's border is the *past* colour, the right circle's the *present* one
  (the *result* has no colour on the icons: their fill is a fixed grey, chosen for contrast),
* the View pop-up (`selection_view_form.py`), the only writer.

Remembered across restarts (`bind_ui_state`; keys `ui_state_keys.SELECTION_STYLE_KEYS`).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor

from ...storage.ui_state_keys import SELECTION_STYLE_KEYS, read
from ..ui_state import UiStateStore
from .selection_palette import COLORS, FILL_OPACITY_STEPS

MAX_FILL_STEP = len(FILL_OPACITY_STEPS) - 1


def normalized(color: str) -> str | None:
    """``"#rrggbb"`` (lower case) for any colour Qt understands, else ``None``."""
    qcolor = QColor(color)
    return qcolor.name() if qcolor.isValid() else None


class SelectionStyle(QObject):
    changed = pyqtSignal()  # a colour or a fill step changed (the new values are read back with `color` / `step`)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._colors = dict(COLORS)
        self._steps = {role: 0 for role in COLORS}
        self._borders = {role: True for role in COLORS}

    # -- read ------------------------------------------------------------------------------------------------

    def color(self, role: str) -> str:
        """The outline's colour as ``"#rrggbb"``."""
        return self._colors[role]

    def border_shown(self, role: str) -> bool:
        """Whether the outline's line (white under coloured crawling dashes) is drawn. Off = only the infill, which is static."""
        return self._borders[role]

    def step(self, role: str) -> int:
        return self._steps[role]

    def fill_alpha(self, role: str) -> float:
        """0.0 (no fill) to 1.0."""
        return FILL_OPACITY_STEPS[self._steps[role]]

    def fill_color(self, role: str) -> QColor:
        """The infill's colour with its opacity (alpha 0 at step 0)."""
        color = QColor(self._colors[role])
        color.setAlphaF(self.fill_alpha(role))
        return color

    # -- write -----------------------------------------------------------------------------------------------

    def set_color(self, role: str, color: str) -> bool:
        """Pick `color` for `role`; an invalid colour is refused (returns ``False``). Reports only a real change."""
        fixed = normalized(color)
        if fixed is None:
            return False
        if fixed != self._colors[role]:
            self._colors[role] = fixed
            self.changed.emit()
        return True

    def set_step(self, role: str, step: int) -> None:
        """Pick the fill step, held to 0 .. `MAX_FILL_STEP`."""
        step = max(0, min(MAX_FILL_STEP, int(step)))
        if step != self._steps[role]:
            self._steps[role] = step
            self.changed.emit()

    def set_border_shown(self, role: str, shown: bool) -> None:
        shown = bool(shown)
        if shown != self._borders[role]:
            self._borders[role] = shown
            self.changed.emit()

    # -- remembered --------------------------------------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Apply what was saved (a wrong value falls back to the default), then save every later change. Restoring comes first, so it is
        not saved back."""
        for role, keys in SELECTION_STYLE_KEYS.items():
            saved = read(store, keys["color"])
            if isinstance(saved, str):
                self.set_color(role, saved)
            step = read(store, keys["fill"])
            if isinstance(step, int):
                self.set_step(role, step)
            border = read(store, keys["border"])
            if isinstance(border, bool):
                self.set_border_shown(role, border)
        self.changed.connect(lambda: self._save(store))

    def _save(self, store: UiStateStore) -> None:
        for role, keys in SELECTION_STYLE_KEYS.items():
            store.set(keys["color"].key, self._colors[role])
            store.set(keys["fill"].key, self._steps[role])
            store.set(keys["border"].key, self._borders[role])
