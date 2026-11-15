"""The colours of a selection gesture, one source for the outlines on the image, the + / - / x badge and the mode icons (2026-11-16,
maintainer: "3 colours for selection (past, present, resulting), while icons will use fills, in the image area it will be just borders").

Every combine gesture has three sets, and each has its colour:

* **past**: the selection as it stands (pink). Also the colour of the outline when nothing is being dragged: it is "the selection".
* **present**: the shape being dragged now: the rectangle, the lasso, the brush stroke (green).
* **result**: what will be selected once the gesture is let go (blue: the colour the mode icons fill "ends up selected" with).

On the mode icons (`combine_mode_icons.py`) the left circle is the past (pink outline), the right circle the present (green outline) and the fill the
result: grey (`ICON_SELECTED_FILL`) for what ends up selected, the plain background for everything else. On the image the three sets are drawn as borders only,
and the result is blue there: a white line with the coloured dashes crawling over it.

**What a drag shows** (`OUTLINES_FOR_MODE`, one table, the canvas reads it):

| mode | drawn while dragging |
|---|---|
| Replace   | past + present (the old one goes when the button is let go) |
| Add       | past + present (both stay visible: what you add to, and what you add) |
| Subtract  | the result only: the old selection with the dragged shape cut out of it (the shape itself is not drawn) |
| Intersect | past + present + result (the overlap), so a drag that misses the selection is still seen |

Pure data: no Qt.
"""

from __future__ import annotations

from ...selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR

PAST, PRESENT, RESULT = "past", "present", "result"

PAST_COLOR = "#f472b6"  # pink-400: the selection as it stands
PRESENT_COLOR = "#4ade80"  # green-400: the shape being dragged
RESULT_COLOR = ADD_COLOR  # sky-400, the toolbar's blue: "ends up selected" on the mode icons
REMOVED_COLOR = SUBTRACT_COLOR  # orange: "take away", the colour of the - ring beside the pointer (the mode icons no longer fill anything with it)
ICON_SELECTED_FILL = "#606977"  # grey: "ends up selected" on the mode icons (the image draws the result blue); contrast with the pink and green circle lines 2.1 / 3.2 : 1

COLORS = {PAST: PAST_COLOR, PRESENT: PRESENT_COLOR, RESULT: RESULT_COLOR}

OUTLINES_FOR_MODE: dict[str, tuple[str, ...]] = {
    REPLACE: (PAST, PRESENT),
    ADD: (PAST, PRESENT),
    SUBTRACT: (RESULT,),
    INTERSECT: (PAST, PRESENT, RESULT),
}
