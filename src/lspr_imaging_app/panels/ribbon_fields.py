"""The one look of a number field and a drop-down in the Image panel's ribbon tabs (2026-10-19).

No frame, no fill, exactly one icon button high (`BUTTON_SIZE`) and only as wide as the widest text the
field can show. Same height as the icons beside it means the caption of a group (`ribbon_group.
labeled_icon_group`) sits at the same height under a field as under an icon; a framed field was 26 px
(content + padding + border) and pushed its caption 2 px down. Before this module the Mask tab had a private
copy of the number-field look (`_narrow_spinbox`, maintainer request 2026-10-16: "number controls without
field frame so it is more compact") and the Background tab drew framed fields.

**A bare `setFixedWidth` does not hold** (found 2026-10-16, measured offscreen: boxes were 56 px wide
around 34 px of text). The theme's `QSpinBox { min-width: 0px }` rule makes Qt reset `minimumWidth()` to 0
every time the widget is polished, which defeats the "already past the threshold" back-off in
`lspr_ui.ui_helpers._apply_content_based_minimum_width`, so a box that went through `make_compact_spinbox`
later got the width of its widest *technical* value ("2000 px") anyway. The width is therefore also written
into the box's own `min-width` / `max-width` style rules, which beat the theme's rule and survive every
polish. `ensurePolished()` comes first so the text is measured in the font the box is drawn with (Segoe UI
9 pt from the theme), not the application default it has before its style sheet applies.

A box is the widest text plus 14 px wide in total (measured 2026-10-19 in the real panel: 54 px for
"2000 px", whose text is 40 px): in a style sheet `min-width` / `max-width` are the width of the *content*,
and the 2 px of side padding are added outside it, so the rule asks for text + 10 and the box ends up 4 px
wider. (The Mask tab's earlier private helper had the same result, with a comment that miscounted the
padding as inside the 10; the widths the maintainer approved on 2026-10-16 are therefore unchanged.) Pass
the widest text the field can really show (`"2000 px"`, not `"99 px"`): a narrower sample clips a long entry.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox

from .ribbon_icon_size import BUTTON_SIZE

_SIDE_PADDING_PX = 2
_CONTENT_EXTRA_PX = 10  # room beyond the widest text, inside the padding (the line edit keeps ~4 of it free itself)
_TOTAL_EXTRA_PX = _CONTENT_EXTRA_PX + 2 * _SIDE_PADDING_PX  # what the whole box is wider than its widest text


def _frameless_rule(selector: str, extra: str = "") -> str:
    # The theme's own `min-height: 18px` rule is beaten by this one (a widget rule wins over the app-wide
    # one), and the min/max pair, not `setFixedHeight`, is what a style-sheet-drawn box obeys: the height is
    # the content (BUTTON_SIZE) because there is no vertical padding and no border.
    return (
        f"{selector} {{ border: none; background: transparent; padding: 0px {_SIDE_PADDING_PX}px; "
        f"min-height: {BUTTON_SIZE}px; max-height: {BUTTON_SIZE}px;{extra} }}"
    )


def style_ribbon_number_field(spinbox: QAbstractSpinBox, widest_text: str) -> None:
    """Give a spin box the ribbon look (frameless, `BUTTON_SIZE` high, no arrows, right-aligned), just wide
    enough for ``widest_text``. Call it after the range, prefix and suffix are set."""
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    spinbox.setStyleSheet(_frameless_rule("QAbstractSpinBox"))
    spinbox.ensurePolished()
    content = QFontMetrics(spinbox.font()).horizontalAdvance(widest_text) + _CONTENT_EXTRA_PX
    spinbox.setStyleSheet(_frameless_rule("QAbstractSpinBox", f" min-width: {content}px; max-width: {content}px;"))
    spinbox.setFixedSize(content + 2 * _SIDE_PADDING_PX, BUTTON_SIZE)  # the same total the style sheet works out


def style_ribbon_combo(combo: QComboBox) -> str:
    """Give a drop-down the same frameless, `BUTTON_SIZE`-high look as the number fields, just wide enough
    for its widest item (add the items first). Returns the style sheet it set, so a caller that temporarily
    changes the style (a highlight) can put exactly this back."""
    style = _frameless_rule("QComboBox")
    combo.setStyleSheet(style)
    combo.ensurePolished()
    metrics = QFontMetrics(combo.font())
    widest = max((metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
    combo.setFixedSize(widest + _TOTAL_EXTRA_PX, BUTTON_SIZE)
    return style


def style_ribbon_check(box: QCheckBox) -> None:
    """A check box one icon button high (`BUTTON_SIZE`), so the caption of its group sits at the same height as under a field or an icon."""
    box.setFixedHeight(BUTTON_SIZE)
