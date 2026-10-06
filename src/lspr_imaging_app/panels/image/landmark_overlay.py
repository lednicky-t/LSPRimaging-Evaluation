"""Chromatic landmark overlay drawing for the Image panel (display only).

Split out of `panel.py` 2026-10-06. `draw_landmarks` fills three pyqtgraph items
from the chromatic module's landmarks; it reads module state and never writes it.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QColor

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule
from ...image_tools.chromatic.affine import identity_affine_matrix, invert_affine_matrix
from ...wavelength_color import wavelength_to_rgb


def draw_landmarks(
    chromatic: ChromaticModule,
    dataset: DatasetModule,
    observed_item: pg.ScatterPlotItem,
    fitted_item: pg.ScatterPlotItem,
    line_item: pg.PlotCurveItem,
    *,
    current_cube: int,
    current_wavelength: float,
    all_wavelengths: bool,
) -> None:
    """Chromatic landmarks (display only), each wavelength in its own colour.

    Positions are in processed image space, like ROIs. *Estimated* =
    tracked on the image, only at the wavelengths that were tracked
    (crosses). *Fitted* = reference positions pushed through the fitted
    model, at every wavelength (dots), whether or not the correction is
    switched on.

    Current-wavelength mode: a dot per landmark, a cross where it was
    estimated, and a short line joining each cross to its dot (the misfit,
    so a landmark that does not agree with the fit is plain to see).
    All-wavelengths mode: a small dot per landmark per wavelength of the
    reference cube joined by a line per landmark (the fitted shift path),
    plus a cross at every estimated position.

    **While the correction is switched on, every position is shown
    corrected**: pushed through the inverse of that wavelength's model, i.e.
    expressed in the reference frame. Each landmark's dots then fall exactly
    on one point and its crosses scatter around that point by the fit error,
    so the overlay is a direct check of the correction. Switched off, the
    raw positions are shown (the chromatic shift itself)."""
    reference = chromatic.settings()
    if reference.reference_wavelength_nm is None:
        return
    reference_cube = int(reference.reference_spectral_cube_index)
    reference_marks = chromatic.landmarks_for_image((reference_cube, float(reference.reference_wavelength_nm)))
    if not reference_marks:
        return
    base = np.array([[mark.x_px, mark.y_px] for mark in reference_marks], dtype=np.float64)
    ids = [mark.landmark_id for mark in reference_marks]

    corrected = bool(reference.chromatic_correction_enabled)

    def correction_for(wavelength_nm: float) -> np.ndarray:
        """Matrix taking this wavelength's raw positions into what is shown."""
        if not corrected:
            return identity_affine_matrix()
        return invert_affine_matrix(chromatic.fitted_affine_for((reference_cube, float(wavelength_nm))))

    def shown(points: np.ndarray, wavelength_nm: float) -> np.ndarray:
        matrix = correction_for(wavelength_nm)
        return points @ matrix[:, :2].T + matrix[:, 2]

    def fitted_at(wavelength_nm: float) -> np.ndarray:
        matrix = chromatic.fitted_affine_for((reference_cube, float(wavelength_nm)))
        return shown(base @ matrix[:, :2].T + matrix[:, 2], wavelength_nm)

    def estimated_at(wavelength_nm: float) -> dict[int, tuple[float, float]]:
        marks = chromatic.landmarks_for_image((reference_cube, float(wavelength_nm)))
        if not marks:
            return {}
        moved = shown(np.array([[mark.x_px, mark.y_px] for mark in marks], dtype=np.float64), wavelength_nm)
        return {mark.landmark_id: (float(x), float(y)) for mark, (x, y) in zip(marks, moved)}

    if all_wavelengths:
        wavelengths = sorted(w for w in dataset.wavelengths_for_cube(reference_cube) if w > 0.0)
        dot_x, dot_y, dot_brushes = [], [], []
        cross_x, cross_y, cross_pens = [], [], []
        path = {landmark_id: ([], []) for landmark_id in ids}
        for wavelength in wavelengths:
            color = QColor(*wavelength_to_rgb(wavelength))
            estimated = estimated_at(wavelength)
            fitted = fitted_at(wavelength)
            for index, landmark_id in enumerate(ids):
                dot_x.append(fitted[index, 0])
                dot_y.append(fitted[index, 1])
                dot_brushes.append(pg.mkBrush(color))
                path[landmark_id][0].append(fitted[index, 0])
                path[landmark_id][1].append(fitted[index, 1])
                if landmark_id in estimated:
                    cross_x.append(estimated[landmark_id][0])
                    cross_y.append(estimated[landmark_id][1])
                    cross_pens.append(pg.mkPen(color, width=1.5))
        line_x, line_y = [], []
        for xs, ys in path.values():
            line_x += xs + [np.nan]
            line_y += ys + [np.nan]
        line_item.setData(line_x, line_y, pen=pg.mkPen(QColor(255, 255, 255, 110), width=1))
        fitted_item.setData(dot_x, dot_y, symbol="o", size=5, pen=pg.mkPen(None), brush=dot_brushes)
        if cross_x:
            observed_item.setData(cross_x, cross_y, symbol="+", size=10, pen=cross_pens, brush=pg.mkBrush(None))
        return

    wavelength = float(current_wavelength)
    color = QColor(*wavelength_to_rgb(wavelength))
    fitted = fitted_at(wavelength)
    fitted_item.setData(
        fitted[:, 0], fitted[:, 1], symbol="o", size=7, pen=pg.mkPen(None), brush=pg.mkBrush(color)
    )
    if int(current_cube) != reference_cube:
        return
    estimated = estimated_at(wavelength)
    if not estimated:
        return
    cross_x, cross_y, line_x, line_y = [], [], [], []
    for index, landmark_id in enumerate(ids):
        if landmark_id not in estimated:
            continue
        ex, ey = estimated[landmark_id]
        cross_x.append(ex)
        cross_y.append(ey)
        line_x += [ex, fitted[index, 0], np.nan]
        line_y += [ey, fitted[index, 1], np.nan]
    line_item.setData(line_x, line_y, pen=pg.mkPen(color, width=1.5))
    observed_item.setData(
        cross_x, cross_y, symbol="+", size=14, pen=pg.mkPen(color, width=2), brush=pg.mkBrush(None)
    )
