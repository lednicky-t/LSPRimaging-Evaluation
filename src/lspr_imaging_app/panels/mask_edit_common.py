"""Shared support for mask-edit widgets that otherwise have no common base
class - `panels/workflow/mask_highlight_actions.py`, `panels/image/
mask_edit_panels.py`, and `panels/image/mask_file_actions.py` all import
from here, so this lives directly under `panels/`, not nested in any one
of `workflow/`/`image/`.

**Base-mask resolution** (`resolve_mask_edit_base`): any mask-edit tool that
needs "what mask is already in effect at the current frame" before
computing a new candidate or replacement. Factored out 2026-10-02 while
wiring the Image panel's Morphology edit panel alongside the already-
existing Histogram-selection editor (`mask_highlight_editor.py`'s
`HistogramHighlightMaskEditor`, which had this exact logic private to
itself as `_resolve_base_mask` until now) - both tools need "the mask as it
currently stands at this frame, warped in if it was authored elsewhere",
the same resolve-then-warp step `apply_candidate`'s own docstring already
documents as the caller's job.

**Add/Subtract icon colors** (`ADD_COLOR`/`SUBTRACT_COLOR`): unified
2026-10-02 (maintainer request: "unify all +/- icons... make + one blue, -
one orange-red (red is too alarming)") across every "+"/"-" mask-edit
action button in every file above - previously each file hardcoded its own
`_ADD_COLOR`/`_SUBTRACT_COLOR` literals (green/red, the stable app's own
original choice), which could drift out of sync since nothing tied them
together. One shared pair now, imported by all three.

**`action_button`**: the one icon-button builder every mask-edit action
button in this ribbon now goes through (Add/Subtract, Clear/Load/Save,
Morphology's four operations) - factored out 2026-10-02 (maintainer
request: "make sure some icons are shared or reused when appropriate")
from three near-identical private copies that had accumulated across the
files above. See its own docstring for why it also fixes a real visual bug
(disabled icons losing their color) along the way.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import load_tabler_icon, transparent_icon_button_stylesheet

from ..image_tools import ChromaticModule, MaskModule

ADD_COLOR = "#38bdf8"  # GuiTheme.accent_blue
# Tailwind's "orange-500", not a GuiTheme token - chosen specifically to read
# as "remove/subtract" via a warm color without the alarm-color connotation
# of GuiTheme.accent_red (#ef4444), per the maintainer's own framing.
SUBTRACT_COLOR = "#f97316"

BUTTON_SIZE = 28
ICON_SIZE = 22
_RENDER_SIZE = ICON_SIZE * 2  # rendered at 2x, scaled down - crisper than a native bitmap
_STROKE_WIDTH = 2.1


def action_button(
    parent: QWidget,
    icon_name: str,
    color: str,
    tooltip: str,
    *,
    enabled: bool = True,
    disabled_tooltip_suffix: str = "",
) -> QToolButton:
    """A 28px icon-only `QToolButton`, this ribbon's one shared shape for
    every mask-edit action icon.

    **Keeps its icon's color even when disabled** - Qt's `QToolButton`
    auto-generates a desaturated pixmap for `QIcon.Mode.Disabled` by
    default, which is exactly why a not-yet-wired action (Threshold/Local-
    contrast/Draw's own +/- buttons, before their backend exists) looked
    different from an enabled one using the *same* `color` argument -
    Histogram selection's own +/- was the only pair ever actually enabled,
    so the only one that never hit this auto-desaturation (2026-10-02,
    maintainer request: "all [+/-] should be... same as histogram... but
    also in future"). Registering the identical full-color pixmap under
    both `Normal` and `Disabled` modes overrides Qt's default - a disabled
    button is still non-interactive by its real state (`isEnabled()`), it
    just no longer *looks* different doing so."""
    button = QToolButton(parent)
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
    button.setToolTip(tooltip if enabled else tooltip + disabled_tooltip_suffix)
    button.setIcon(disabled_safe_icon(icon_name, color))
    button.setEnabled(enabled)
    return button


def disabled_safe_icon(icon_name: str, color: str) -> QIcon:
    """The same full-color-even-when-disabled `QIcon` construction
    `action_button` uses internally, exposed separately for a caller that
    needs to re-color an existing button's icon later (e.g. on a toggle) -
    `action_button` itself only sets an icon once, at construction."""
    pixmap = load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH).pixmap(
        _RENDER_SIZE, _RENDER_SIZE
    )
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    return icon


def resolve_mask_edit_base(
    mask: MaskModule,
    chromatic: ChromaticModule,
    target_frame: tuple[int, float],
    raw_shape: tuple[int, int],
) -> np.ndarray:
    """The raw-space mask already in effect at `target_frame`, warped into
    that frame's geometry if it was authored at a different one. No existing
    change yet means an all-clear raw canvas, not an error - the common
    "first edit" case."""
    resolution = mask.resolve_mask_source(target_frame)
    if resolution is None:
        return np.zeros(raw_shape, dtype=bool)
    authored_frame, authored_mask, _scope = resolution
    if authored_frame == target_frame:
        return authored_mask
    return chromatic.warp_mask_between(authored_mask, authored_frame, target_frame)
