"""The area selection and the lock, seen from the editing tools (2026-11-15; lifted out of `ImagePanel`, which had grown past its size budget:
`tests/unit/test_lspri_panel_size_budget.py`).

Widget-free. The Image panel owns one (`ImagePanel.selection_regions`) and the mask tools ask it for the region they may touch, in **raw**
pixels (the space masks are authored in): the displayed-space selection is mapped back with `area_selection_to_raw` (exact without a rotation,
about one pixel at the edge under one). Three different questions, three methods:

- `selection_raw_mask`: the region editing may reach (the selection if there is one, else the lock, else nothing restricts: `None`). What the Eraser
  and the Feature map's Apply stay inside.
- `selected_raw_mask`: the *selection itself*, clipped to the lock (`None` when nothing is selected: a lock alone selects nothing, so "Add selection to
  the mask" with only a lock adds nothing).
- `lock_raw_mask`: the lock (`None` with no lock): what "Replace mask with selection" keeps the mask's own pixels outside of.

And the two commands of the Lock / Unlock buttons, which say in the status bar what happened (and why nothing did).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ...image_tools import GeometryModule
from ...image_tools.preprocess import area_selection_to_raw
from ...selection import AreaSelectionModule


class SelectionRegions:
    def __init__(
        self,
        area_selection: AreaSelectionModule,
        geometry: GeometryModule,
        image_shape: Callable[[], tuple[int, int] | None],
        status: Callable[[str], None],
    ) -> None:
        self._selection = area_selection
        self._geometry = geometry
        self._image_shape = image_shape  # (height, width) of the image on screen
        self._status = status

    def displayed_shape(self) -> tuple[int, int] | None:
        """``(height, width)`` of the image on screen (the space the area selection lives in), or ``None`` with no image."""
        return self._image_shape()

    def _to_raw(self, region: np.ndarray | None, raw_shape: tuple[int, int]) -> np.ndarray | None:
        if region is None:
            return None
        return area_selection_to_raw(region, raw_shape, self._geometry.settings())

    def selection_raw_mask(self, raw_shape: tuple[int, int]) -> np.ndarray | None:
        """The region editing may reach (the selection, else the lock) as a raw-space editable mask, or `None` when nothing restricts."""
        shape = self._image_shape()
        return None if shape is None else self._to_raw(self._selection.mask(shape), raw_shape)

    def selected_raw_mask(self, raw_shape: tuple[int, int]) -> np.ndarray | None:
        """The *selection itself* (clipped to the lock) as a raw-space mask, or `None` when nothing is selected."""
        shape = self._image_shape()
        return None if shape is None else self._to_raw(self._selection.selected_mask(shape), raw_shape)

    def lock_raw_mask(self, raw_shape: tuple[int, int]) -> np.ndarray | None:
        """The lock (the fence) as a raw-space mask, or `None` with no lock (a lock is a canvas: a tool inside it must not change what is
        outside)."""
        shape = self._image_shape()
        return None if shape is None else self._to_raw(self._selection.lock_mask(shape), raw_shape)

    def lock_to_selection(self) -> None:
        """The selection becomes the lock (the fence): what is outside is dimmed, tools and new selections stay inside it. The status bar
        says what happened, including why nothing did."""
        shape = self._image_shape()
        selection = self._selection
        if shape is None:
            self._status("Lock: no image on screen.")
        elif not selection.has_selection():
            self._status("Lock: select an area first (the lock turns the selection into the fence).")
        elif selection.lock_selection(shape):
            fence = selection.lock_region()
            count = int(np.count_nonzero(fence)) if fence is not None else 0
            self._status(
                f"Locked to {count:,} px ({100.0 * count / (shape[0] * shape[1]):.1f} % of the image): selections and edits stay inside it. "
                "Unlock (selection toolbar) leaves it."
            )
        else:
            self._status("Lock: the selection covers no pixel of the image.")

    def unlock_selection(self) -> None:
        """Drop the lock; the selection stays."""
        if self._selection.unlock():
            self._status("Unlocked: the whole image can be selected and edited again.")
