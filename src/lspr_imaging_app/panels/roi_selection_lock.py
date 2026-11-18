"""Words of the ROI selection lock (2026-11-16), in one place: the lock button on the ROIs tab, the "Lock selection" entry of both ROI
context menus (the Image panel's and the table's) and the status line of a refused change say the same thing.

The lock itself is `SelectionModule.set_roi_selection_locked` (`selection/module.py`: what it blocks and what passes it). Nothing here
computes anything.
"""

MENU_TEXT = "Lock ROI selection"

TIP = (
    "Lock the ROI selection: clicks on the image and in the table no longer change which ROIs are selected "
    "(Group, Delete, the plots and the other commands keep acting on them). Click again to unlock."
)

REFUSED_TEXT = (
    "The ROI selection is locked, so it was not changed. Unlock it (the lock button on the ROIs tab, or right-click > Lock ROI selection) to change it."
)
