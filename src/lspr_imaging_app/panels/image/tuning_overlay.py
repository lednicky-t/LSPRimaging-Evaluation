"""The crosses of ROI position tuning on the Image plot (2026-10-29, step D2; design: ``docs/roi_position_tuning_plan_2026-10-28.md``).

For every tuned ROI, one small cross at each centre that was tried, coloured from the worst position of that ROI (dark) to the best (bright) for the chosen criterion;
the best position of each ROI has a larger cross. The colours are a viridis-like ramp, which is perceptually even, reads on grey images and does not rely on red-green
(maintainer, 2026-10-28). A result that is out of date is drawn faint.

This is a drawing and nothing else: it is handed a :class:`~...analysis.roi_tuning_module.TuningView` and the chromatic affine of the frame on screen, reads no module,
and keeps the last thing it drew so a call that changes nothing costs a comparison. The positions are the ROIs' processed-image centres plus the lattice offsets,
carried to the frame on screen by the same affine as the ROI circles, exactly as the chromatic correction carries a shifted ROI at every wavelength.
(The ROIs drawn at their best centres - the star on the Tuning tab - are the ROI overlay's business: the panel moves their display centres.)
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt

from ...analysis.roi_tuning_module import TuningView
from ...image_tools.chromatic.affine import apply_affine_to_points

CROSS_SIZE = 9
BEST_CROSS_SIZE = 15
CROSS_WIDTH = 1.5
BEST_CROSS_WIDTH = 2.5
STALE_OPACITY = 0.35
NO_VALUE_RGB = (140, 140, 140)  # a position that has no value (grey, whatever the ramp)
LEVELS = 32  # colours are quantised to this many steps so that the pens can be shared (4,000 crosses cost 32 pens, not 4,000)
_RAMP = (  # viridis at 0, 25, 50, 75, 100 %
    (0.00, (68, 1, 84)), (0.25, (59, 82, 139)), (0.50, (33, 144, 141)), (0.75, (93, 200, 99)), (1.00, (253, 231, 37)),
)


def ramp_rgb(scores: np.ndarray) -> np.ndarray:
    """``uint8 [n, 3]``: the colour of each score (0 = worst .. 1 = best); NaN gets :data:`NO_VALUE_RGB`. Scores outside 0..1 are clipped."""
    scores = np.asarray(scores, dtype=np.float64)
    stops = [p for p, _ in _RAMP]
    out = np.empty(scores.shape + (3,), dtype=np.uint8)
    clipped = np.clip(np.nan_to_num(scores, nan=0.0), 0.0, 1.0)
    for channel in range(3):
        out[..., channel] = np.rint(np.interp(clipped, stops, [rgb[channel] for _, rgb in _RAMP])).astype(np.uint8)
    out[np.isnan(scores)] = NO_VALUE_RGB
    return out


class TuningOverlay:
    def __init__(self, plot: pg.PlotItem) -> None:
        self._plot = plot
        self._item = pg.ScatterPlotItem(pxMode=True, symbol="+")
        self._item.setZValue(30)  # above the ROI circles
        self._item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # a drawing: clicks go to the image and the tools underneath
        plot.addItem(self._item)
        self._item.hide()
        self._styles: dict[tuple[int, bool], tuple] = {}
        self._key: tuple | None = None

    # -- what is drawn (for tests and the panel) -------------------------------------------------------------------------------

    def point_count(self) -> int:
        return len(self._item.data) if self._item.isVisible() else 0

    def positions(self) -> np.ndarray:
        """``float64 [n, 2]`` of the plot positions of the crosses on screen (empty when hidden)."""
        if not self._item.isVisible():
            return np.empty((0, 2))
        return np.column_stack(self._item.getData())

    def sizes(self) -> np.ndarray:
        return np.asarray(self._item.data["size"], dtype=np.float64) if self._item.isVisible() else np.empty(0)

    def opacity(self) -> float:
        return float(self._item.opacity())

    def pen_rgb(self) -> np.ndarray:
        """``uint8 [n, 3]`` of the colour of each cross on screen."""
        if not self._item.isVisible():
            return np.empty((0, 3), dtype=np.uint8)
        return np.asarray([[p.color().red(), p.color().green(), p.color().blue()] for p in self._item.data["pen"]], dtype=np.uint8)

    # -- drawing ---------------------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        self._key = None
        self._item.setData([], [])
        self._item.hide()

    def update(self, view: TuningView | None, affine: np.ndarray) -> None:
        """Draw ``view`` for the frame whose chromatic affine is ``affine`` (None, or crosses switched off, draws nothing)."""
        if view is None or not view.show_crosses:
            if self._key is not None:
                self.clear()
            return
        key = (view.revision, np.asarray(affine, dtype=np.float64).tobytes())
        if key == self._key:
            return
        self._key = key
        n_positions, n_rois = view.scores.shape
        # Position-major, like the arrays: crosses[p * n_rois + r] is ROI r at lattice position p.
        points = (view.roi_xy[None, :, :] + view.offsets[:, None, :]).reshape(-1, 2)
        shown = apply_affine_to_points(points, affine)
        scores = view.scores.reshape(-1)
        is_best = np.zeros((n_positions, n_rois), dtype=bool)
        ranked = view.best >= 0
        is_best[view.best[ranked], np.flatnonzero(ranked)] = True
        is_best = is_best.reshape(-1)
        levels = np.where(np.isnan(scores), -1, np.rint(np.nan_to_num(scores, nan=0.0) * (LEVELS - 1)).astype(int))
        drawn = [self._style(int(level), bool(best)) for level, best in zip(levels, is_best, strict=True)]
        self._item.setData(
            x=shown[:, 0], y=shown[:, 1], size=np.where(is_best, BEST_CROSS_SIZE, CROSS_SIZE), pen=[pen for pen, _ in drawn],
            brush=[brush for _, brush in drawn],
        )
        self._item.setOpacity(STALE_OPACITY if view.stale else 1.0)
        self._item.show()

    def _style(self, level: int, best: bool):
        """The (pen, brush) of one cross: the same colour for the outline and the fill, so the plus is solid."""
        key = (level, best)
        style = self._styles.get(key)
        if style is None:
            rgb = NO_VALUE_RGB if level < 0 else tuple(int(v) for v in ramp_rgb(np.array([level / (LEVELS - 1)]))[0])
            style = self._styles[key] = (pg.mkPen(rgb, width=BEST_CROSS_WIDTH if best else CROSS_WIDTH), pg.mkBrush(rgb))
        return style
