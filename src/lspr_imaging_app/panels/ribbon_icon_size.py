"""The one size of every icon button in the Image panel's ribbon tabs (Mask, Image tools, ROIs,
View, Chromatic, Background and the "General" group).

A leaf module on purpose (no imports): `image/general_group.py` and `mask_edit_common.py` both need
these numbers and importing one from the other would close an import cycle. The ribbon files take them
through `general_group` (`BUTTON_SIZE`, `ICON_SIZE`, `CAPTIONED_GROUP_HEIGHT`) or `mask_edit_common`.

28 / 22 px until 2026-10-18, then 24 / 18 (maintainer: smaller icons to save width; the 22 / 16 px canvas
bar in `image/canvas_tools.py` is a separate, older size). To try another size change these two numbers only.
"""

from __future__ import annotations

BUTTON_SIZE = 24
ICON_SIZE = 18
# Height of a captioned icon group (`ribbon_group.labeled_icon_group`): the icon row, 2 px of group
# spacing and one line of 9 px caption text (12 px, measured with `QLabel.sizeHint()`, not guessed).
CAPTIONED_GROUP_HEIGHT = BUTTON_SIZE + 2 + 12
