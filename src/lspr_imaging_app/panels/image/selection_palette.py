"""The colours of a selection gesture, one source for the outlines on the image, the + / - / x badge and the mode icons (2026-11-16,
maintainer: "3 colours for selection (past, present, resulting), while icons will use fills, in the image area it will be just borders").

Every combine gesture has three sets, and each has its colour:

* **past**: the selection as it stands (pink). Also the colour of the outline when nothing is being dragged: it is "the selection".
* **present**: the shape being dragged now: the rectangle, the lasso, the brush stroke (green).
* **result**: what will be selected once the gesture is let go. It is drawn in the **past's colour** (2026-11-16, the maintainer found a third colour and
  a third row in the View pop-up confusing): it is the selection as it will stand. So the image shows two colours, pink and green.

On the mode icons (`combine_mode_icons.py`) the left circle is the past (pink outline), the right circle the present (green outline) and the fill the
result: grey (`ICON_SELECTED_FILL`) for what ends up selected, the plain background for everything else. On the image the sets are drawn as borders (and an
optional infill): a white line with the coloured dashes crawling over it.

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
RESULT_COLOR = ADD_COLOR  # sky-400, the toolbar's blue: the + and x rings beside the pointer ("ends up selected"); no outline wears it any more
REMOVED_COLOR = SUBTRACT_COLOR  # orange: "take away", the colour of the - ring beside the pointer (the mode icons no longer fill anything with it)
ICON_SELECTED_FILL = "#606977"  # grey: "ends up selected" on the mode icons; contrast with the pink and green circle lines 2.1 / 3.2 : 1

# The looks the user can set (View pop-up: colour, infill, border): two, the left and the right circle of the mode icons. `selection_style.SelectionStyle`
# starts from these colours.
COLORS = {PAST: PAST_COLOR, PRESENT: PRESENT_COLOR}
# The three outlines the image can draw, and which of the two looks each wears. **The result has no look of its own** (maintainer, 2026-11-16: a third
# row in the pop-up was confusing; the result only exists mid-drag in Subtract / Intersect and has no circle on the icons): it is "the selection as it will
# stand", so it wears the selection's look.
OUTLINE_ROLES = (PAST, PRESENT, RESULT)
LOOK_OF = {PAST: PAST, PRESENT: PRESENT, RESULT: PAST}

# The infill of an outline (2026-11-16, maintainer: "opacity, only 5 steps, when 0 then no fill"): the share of its colour that tints the pixels
# inside it. Step 0 = no fill (the default: the image shows borders only, as before). One tuple, so a different ladder is one edit.
FILL_OPACITY_STEPS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

OUTLINES_FOR_MODE: dict[str, tuple[str, ...]] = {
    REPLACE: (PAST, PRESENT),
    ADD: (PAST, PRESENT),
    SUBTRACT: (RESULT,),
    INTERSECT: (PAST, PRESENT, RESULT),
}
