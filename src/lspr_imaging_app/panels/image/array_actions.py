"""What the Array group's buttons do (Image panel, ROIs tab). Calls only public interfaces.

`ArrayControls` (widgets) -> this -> `roi/array_task.ArrayAction` (thread) -> `RoiToolbox.place_array` /
`refine_rois` (one undo step). The panel owns no ROI state: this turns clicks into commands.

Rules (maintainer, 2026-10-08)
- Always the **reference wavelength**, in the **current cube** (not necessarily the reference cube). Started on
  another wavelength, the user is asked whether to jump there first.
- Replace everything **within the selection** (nothing selected: the array is added next to the others).
- A tilt above 1 degree: after the detection the user is asked whether to rotate the image (yes: rotate and
  detect again; the existing ROIs follow the rotation through the geometry sync).
- Refused while an analysis runs (renumbering ROIs under a running analysis would corrupt it), like Undo.
- Scope toggle (Persistent / Individual, like the Mask): not built yet; every edit is Persistent, as ROIs always were.

Positions: the thread works on the displayed image of that frame; results are mapped back to the reference
frame (inverse of the chromatic affine of that frame, the identity at the reference wavelength) before they are
stored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from ...dataset.model import is_dark_frame_wavelength
from ...image_tools.background.module import BackgroundModule
from ...image_tools.chromatic.affine import apply_affine_to_points
from ...image_tools.chromatic.module import ChromaticModule
from ...image_tools.geometry.module import GeometryModule
from ...roi import RoiToolbox
from ...roi.array_task import DETECT, PLACE, REFINE, ArrayAction, ArrayOutcome
from ...roi.scope import RoiEditTarget
from ...selection import SelectionModule
from .array_controls import ArrayControls

logger = logging.getLogger(__name__)


def _ask(parent: QWidget | None, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def _tell(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


class ArrayActions(QObject):
    status = pyqtSignal(str)

    def __init__(
        self,
        controls: ArrayControls,
        action: ArrayAction,
        *,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        geometry: GeometryModule,
        background: BackgroundModule,
        chromatic: ChromaticModule,
        load_plane: Callable[[int, float], np.ndarray],
        has_dataset: Callable[[], bool],
        resolve_reference: Callable[[], tuple[int, float] | None],
        analysis_running: Callable[[], bool],
        dialog_parent: QWidget | None = None,
        edit_target: RoiEditTarget | None = None,
        ask: Callable[[str, str], bool] | None = None,
        tell: Callable[[str, str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controls = controls
        self._action = action
        self._toolbox = toolbox
        self._selection = selection
        self._geometry = geometry
        self._background = background
        self._chromatic = chromatic
        self._load_plane = load_plane
        self._has_dataset = has_dataset
        self._resolve_reference = resolve_reference
        self._analysis_running = analysis_running
        self._ask = ask or (lambda title, text: _ask(dialog_parent, title, text))
        self._tell = tell or (lambda title, text: _tell(dialog_parent, title, text))
        self._rotated_once = False
        self._last_request: dict | None = None
        self._edit_target = edit_target

        controls.run_requested.connect(self._on_run)
        controls.refine_requested.connect(self._on_refine)
        controls.cancel_requested.connect(action.cancel)
        selection.roi_selection_changed.connect(lambda ids: controls.set_selection_count(len(ids)))
        controls.set_selection_count(len(selection.selected_roi_ids()))
        action.running_changed.connect(controls.set_running)
        action.completed.connect(self._on_completed)
        action.failed.connect(self._on_failed)
        action.cancelled.connect(lambda: self.status.emit("Array: cancelled."))

    # -- start --------------------------------------------------------------------------

    def _on_run(self, mode: str) -> None:
        self._rotated_once = False
        if mode == "manual":
            grid = self._controls.grid()
            if grid is None:
                self._fail_message("Manual placement needs rows, columns, pitch and disk diameter (Array settings).")
                return
            self._begin(PLACE, grid=grid)
        else:
            self._begin(DETECT)

    def _on_refine(self) -> None:
        self._rotated_once = False
        if not self._selection.selected_roi_ids():
            self._fail_message("Select the ROIs to refine first.")
            return
        self._begin(REFINE)

    def _begin(self, kind: str, *, grid: tuple[int, int, float, float] | None = None) -> None:
        if self._action.is_running():
            return
        if self._analysis_running():
            self._fail_message("An analysis is running. Wait for it to finish (or cancel it) before changing the ROIs.")
            return
        if not self._has_dataset():
            self._fail_message("No dataset loaded.")
            return
        reference = self._resolve_reference()
        if reference is None:
            self._fail_message("Choose a reference frame first (Workflow panel, Reference frame).")
            return
        wavelength = float(reference[1])
        if is_dark_frame_wavelength(wavelength):
            self._fail_message("The reference wavelength is the dark frame (0 nm). Choose a spectral wavelength as reference.")
            return
        if abs(float(self._selection.current_wavelength()) - wavelength) > 1e-6:
            if not self._ask(
                "Array",
                f"The Array tools work on the reference wavelength ({wavelength:g} nm), but {self._selection.current_wavelength():g} nm "
                "is shown. Go to the reference wavelength and continue?",
            ):
                return
            self._selection.set_wavelength(wavelength)
        cube = int(self._selection.current_cube())
        settings = self._controls.settings()
        selected = tuple(sorted(self._selection.selected_roi_ids()))
        request: dict = dict(
            settings=settings, cube_index=cube, wavelength_nm=wavelength, roi_ids=selected, grid=grid,
        )
        centers = diameters = None
        if kind == REFINE:
            affine = np.asarray(self._chromatic.affine_for((cube, wavelength)), dtype=np.float64)
            keep = [roi for roi in self._toolbox.rois_at(cube) if roi.area_roi_id in set(selected)]
            positions = self._toolbox.display_positions((cube, wavelength), affine)
            ids_all = [roi.area_roi_id for roi in self._toolbox.rois()]  # same order as `rois_at`
            centers = np.array([positions[ids_all.index(roi.area_roi_id)] for roi in keep], dtype=np.float64)
            diameters = np.array([roi.sample_diameter_px for roi in keep], dtype=np.float64)
            request["roi_ids"] = tuple(roi.area_roi_id for roi in keep)
        self._last_request = dict(kind=kind, **request)
        self.status.emit("Array: working...")
        self._action.start(
            kind,
            settings,
            cube_index=cube,
            wavelength_nm=wavelength,
            geometry=self._geometry.settings(),
            background=self._background.settings(),
            load_plane=self._load_plane,
            roi_ids=request["roi_ids"],
            centers_xy=centers,
            sample_diameters_px=diameters,
            grid=grid,
        )

    # -- finish -------------------------------------------------------------------------

    def _on_failed(self, message: str) -> None:
        self._fail_message(message)

    def _fail_message(self, message: str) -> None:
        self.status.emit(f"Array: {message}")
        self._tell("Array", message)

    def _on_completed(self, outcome: ArrayOutcome) -> None:
        result = outcome.result
        if outcome.kind == DETECT and result.rotate_suggestion_deg is not None and not self._rotated_once:
            tilt = float(result.rotate_suggestion_deg)
            if self._ask(
                "Array",
                f"The array is tilted by {tilt:.2f}°. Rotate the image by {tilt:.2f}° to level it and detect again?\n\n"
                "(Existing ROIs follow the rotation.)",
            ):
                self._rotated_once = True
                self._geometry.set_rotation(float(self._geometry.settings().rotation_angle_deg) + tilt)
                self._begin(DETECT)
                return
        if self._analysis_running():
            self._fail_message("An analysis started meanwhile; the result was not applied.")
            return
        try:
            self._apply(outcome)
        except (ValueError, KeyError) as error:
            self._fail_message(str(error))
            return
        text = result.report
        if result.warnings:
            text += "  Note: " + " ".join(result.warnings)
        self.status.emit(f"Array: {text}")

    def _apply(self, outcome: ArrayOutcome) -> None:
        result = outcome.result
        cube, wavelength = outcome.cube_index, outcome.wavelength_nm
        affine = np.asarray(self._chromatic.affine_for((cube, wavelength)), dtype=np.float64)
        full = np.eye(3)
        full[:2, :] = affine[:2, :]  # the affine may be stored as 2 x 3
        try:
            inverse = np.linalg.inv(full)[:2, :]
        except np.linalg.LinAlgError as error:
            raise ValueError("The chromatic correction of this frame cannot be inverted.") from error
        centers = apply_affine_to_points(np.asarray(result.centers_xy, dtype=np.float64), inverse)
        if outcome.kind == REFINE:
            updates = {
                int(roi_id): (
                    float(centers[i, 0]), float(centers[i, 1]), float(result.sample_diameters_px[i]),
                    float(result.ring_inner_px[i]), float(result.ring_outer_px[i]),
                )
                for i, roi_id in enumerate(outcome.roi_ids)
            }
            self._toolbox.refine_rois(updates, **({} if self._edit_target is None else self._edit_target.kwargs()))
            return
        located = result.found if (outcome.kind == DETECT or outcome.settings.snap_to_image) else None
        self._toolbox.place_array(
            centers,
            result.sample_diameters_px,
            result.ring_inner_px,
            result.ring_outer_px,
            rows=result.rows,
            cols=result.cols,
            pitch_x_px=result.pitch_x_px,
            pitch_y_px=result.pitch_y_px,
            rotation_deg=0.0 if np.isnan(result.tilt_deg) else float(result.tilt_deg),
            located=None if located is None else [bool(v) for v in located],
            replace_ids=outcome.roi_ids,
            label="Place array" if outcome.kind == PLACE else "Detect array",
        )
