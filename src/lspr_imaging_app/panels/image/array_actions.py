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
- **Auto rotate** (2026-10-20, maintainer request; button in the Image tools tab, Rotation group): the same
  detection as Detect, then the image is rotated by the measured tilt, however small (no 1 degree threshold, no
  question), and **no ROIs are placed or changed** (existing ROIs follow the rotation through the geometry sync).
  One pass: the tilt is measured on the image as it is now shown, so it adds to the current rotation.
- **Auto crop** (2026-10-21, maintainer request; button in the Image tools tab, Crop group): crops to the ROI
  lattice - half a lattice constant of border beyond the outermost ROI centres
  (`dataset.chunk_plan.lattice_crop`; that docstring says why half a pitch is the number). Goal (maintainer): a
  smaller export whose chunk grid lines up with the ROI array.

  It **does the crop and nothing else** (maintainer, 2026-10-21, after a first version that levelled and detected
  too): levelling the image, placing the ROIs and fitting the chromatic correction are its three **dependencies**,
  which the user runs themselves. `auto_crop_readiness()` reports which are met; the button is green when all
  three are and amber otherwise, and an amber click opens a menu of the missing steps instead of cropping
  (`transforms_settings.py`). Each dependency is measured, never remembered: the lattice tilt from the ROI centres
  as they are now (`chunk_plan.lattice_tilt_deg`, within `LEVEL_TOLERANCE_DEG`), the ROIs from the toolbox, the
  chromatic correction from its fitted models. So all three survive a session reload and follow later edits.
- Scope toggle (Persistent / Individual, like the Mask): not built yet; every edit is Persistent, as ROIs always were.

Positions: the thread works on the displayed image of that frame; results are mapped back to the reference
frame (inverse of the chromatic affine of that frame, the identity at the reference wavelength) before they are
stored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from ...dataset import chunk_plan as cp
from ...dataset.model import is_dark_frame_wavelength
from ...image_tools.background.module import BackgroundModule
from ...image_tools.chromatic.affine import apply_affine_to_points
from ...image_tools.chromatic.module import ChromaticModule
from ...image_tools.geometry.model import GeometrySettings
from ...image_tools.geometry.module import GeometryModule
from ...image_tools.geometry.transform import spatial_output_shape
from ...roi import RoiToolbox
from ...roi.ordering import grid_band_px, order_by_grid
from ...roi.array_task import DETECT, PLACE, REFINE, ArrayAction, ArrayOutcome
from ...roi.scope import RoiEditTarget
from ...selection import SelectionModule
from .array_controls import ArrayControls

logger = logging.getLogger(__name__)

_LEVEL_EPS_DEG = 0.005
"""A tilt below this is not worth a rotation: it is below what the rotation field shows (2 decimals)."""
_READINESS_DEBOUNCE_MS = 150
"""Auto crop's checklist waits this long after the last ROI/geometry edit before it is recomputed."""
LEVEL_TOLERANCE_DEG = 0.2
"""Auto crop calls the lattice straight within this. Not tighter: Auto rotate leaves ~0.1 deg of residual tilt
(the detection's own accuracy), and over a 1200 px array 0.2 deg moves a centre by about 4 px - small against a
~40 px margin, so the crop and the chunk grid are still right."""


@dataclass(frozen=True)
class AutoCropReadiness:
    """Which of Auto crop's three dependencies are met (`ArrayActions.auto_crop_readiness`). ``levelled=None`` =
    it cannot be judged, which counts as not met but reads differently to the user - ``level_note`` says why in
    words, so the button's checklist never has to guess."""

    levelled: bool | None
    rois: bool
    chromatic: bool
    tilt_deg: float | None = None
    level_note: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.levelled) and self.rois and self.chromatic


def _ask(parent: QWidget | None, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def _tell(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


class ArrayActions(QObject):
    status = pyqtSignal(str)
    # `AutoCropReadiness`, whenever anything it is measured from changes (the ROIs, the image geometry the ROIs
    # are positioned in, the chromatic correction). The Image panel passes it on to the Auto crop button.
    auto_crop_readiness_changed = pyqtSignal(object)

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
        raw_plane_shape: Callable[[], tuple[int, int] | None] | None = None,
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
        # Auto crop needs the raw (height, width) to work out the levelled canvas; a header read, never a plane
        # (`DatasetModule.raw_plane_shape`), so nothing is loaded on the GUI thread.
        self._raw_plane_shape = raw_plane_shape
        self._resolve_reference = resolve_reference
        self._analysis_running = analysis_running
        self._ask = ask or (lambda title, text: _ask(dialog_parent, title, text))
        self._tell = tell or (lambda title, text: _tell(dialog_parent, title, text))
        self._rotated_once = False
        self._auto_rotate = False  # the running Detect only measures the tilt for Auto rotate
        # The rotation Auto rotate left behind. Auto crop's "levelled" tick is normally measured from the ROI
        # lattice, but the maintainer's flow is Auto rotate *first*, when there are no ROIs to measure yet
        # (2026-10-21 report: "the leveling array is still present even though I run it"). Auto rotate did
        # measure the tilt on the image, so its result stands in until a lattice can confirm it - and only while
        # the rotation is still the one it set.
        self._levelled_at: float | None = None
        self._last_request: dict | None = None
        self._edit_target = edit_target

        controls.run_requested.connect(self._on_run)
        controls.refine_requested.connect(self._on_refine)
        controls.reorder_requested.connect(self._on_reorder)
        controls.cancel_requested.connect(action.cancel)
        selection.roi_selection_changed.connect(lambda ids: controls.set_selection_count(len(ids)))
        controls.set_selection_count(len(selection.selected_roi_ids()))
        action.running_changed.connect(controls.set_running)
        action.completed.connect(self._on_completed)
        action.failed.connect(self._on_failed)
        action.cancelled.connect(self._on_cancelled)
        # Auto crop's dependency state: the same inputs the Export tab's ROI layout watches (`export_tab.py`).
        # Debounced, because `geometry_changed` fires on every sample of a ROI drag and this only feeds a button's
        # colour and its tooltip - nobody reads either mid-drag (2026-10-21).
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setSingleShot(True)
        self._readiness_timer.setInterval(_READINESS_DEBOUNCE_MS)
        self._readiness_timer.timeout.connect(self._emit_auto_crop_readiness)
        toolbox.geometry_changed.connect(self._schedule_auto_crop_readiness)
        toolbox.roi_ids_renumbered.connect(self._schedule_auto_crop_readiness)
        geometry.geometry_changed.connect(self._schedule_auto_crop_readiness)
        chromatic.chromatic_model_changed.connect(self._schedule_auto_crop_readiness)

    # -- start --------------------------------------------------------------------------

    def auto_rotate(self) -> None:
        """Detect the array and rotate the image by its tilt, however small. Places no ROIs."""
        if self._action.is_running():
            return
        self._auto_rotate = True
        self._begin(DETECT)
        if not self._action.is_running():  # refused before starting (no dataset, analysis running, ...)
            self._auto_rotate = False

    def _on_run(self, mode: str) -> None:
        self._auto_rotate = False
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
        self._auto_rotate = False
        self._rotated_once = False
        if not self._selection.selected_roi_ids():
            self._fail_message("Select the ROIs to refine first.")
            return
        self._begin(REFINE)

    def _on_reorder(self, direction: str) -> None:
        """Renumber the selected ROIs (all if none selected) like an array, top-left first. Only the numbers the
        chosen ROIs already hold are shuffled among them. Positions are the stored (reference frame) centres on
        the current cube."""
        if self._analysis_running():
            self._fail_message("An analysis is running. Wait for it to finish (or cancel it) before reordering the ROIs.")
            return
        selected = {int(i) for i in self._selection.selected_roi_ids()}
        cube = int(self._selection.current_cube())
        rois = [roi for roi in self._toolbox.rois_at(cube) if not selected or roi.area_roi_id in selected]
        if len(rois) < 2:
            self.status.emit("Array: nothing to reorder (fewer than two ROIs).")
            return
        band = grid_band_px([roi.sample_diameter_px for roi in rois])
        order = order_by_grid(
            [roi.area_roi_id for roi in rois],
            [(float(roi.center_x), float(roi.center_y)) for roi in rois],
            band,
            column_major=direction == "columns",
        )
        self._toolbox.reorder_subset(order)
        scope = "the selected ROIs" if selected else "all ROIs"
        self.status.emit(f"Array: numbered {scope} {'column by column' if direction == 'columns' else 'row by row'}, starting top-left.")

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
        self._auto_rotate = False

    def _on_cancelled(self) -> None:
        was_auto, self._auto_rotate = self._auto_rotate, False
        self.status.emit("Auto rotate: cancelled." if was_auto else "Array: cancelled.")

    def _fail_message(self, message: str) -> None:
        title = "Auto rotate" if self._auto_rotate else "Array"  # a refusal before an Auto rotate run starts
        self.status.emit(f"{title}: {message}")
        self._tell(title, message)

    def _finish_auto_rotate(self, outcome: ArrayOutcome) -> None:
        tilt = float(outcome.result.tilt_deg)
        if not np.isfinite(tilt):
            self.status.emit("Auto rotate: no tilt could be measured; the rotation was not changed.")
            return
        current = float(self._geometry.settings().rotation_angle_deg)
        # Either way the array is now level as far as this measurement can tell: remember at which rotation, so
        # Auto crop's checklist can tick "Level the array" before any ROI exists (`_levelled_at`).
        if abs(tilt) < _LEVEL_EPS_DEG:
            self._levelled_at = current
            self.status.emit(f"Auto rotate: the array is already level (tilt {tilt:+.3f}°); rotation stays {current:.2f}°.")
        else:
            self._levelled_at = current + tilt
            self._geometry.set_rotation(current + tilt)
            self.status.emit(f"Auto rotate: levelled the array, tilt {tilt:+.2f}° (rotation now {current + tilt:.2f}°).")
        self._emit_auto_crop_readiness()

    # -- Auto crop (Image tools tab, Crop group) -----------------------------------------

    def _chromatic_on(self) -> bool:
        """The correction is switched on and something is fitted - the cheap question (`has_fitted_models`), the
        only one the readiness check needs."""
        return (
            self._chromatic is not None
            and bool(self._chromatic.settings().chromatic_correction_enabled)
            and bool(self._chromatic.has_fitted_models)
        )

    @staticmethod
    def _centers_of(rois: Sequence) -> np.ndarray:
        return np.array([(float(roi.center_x), float(roi.center_y)) for roi in rois], dtype=np.float64)

    def _lattice(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The ROI centres and their read boxes in processed space, for the crop itself (not for the readiness
        check, which must stay cheap). ``None`` without ROIs.

        The boxes are deliberately **not** clipped to the image: a ring already reaching past the current crop has
        to widen the new one, not be cut to it. The chromatic correction is added as one conservative pair of
        numbers (`chunk_plan.affine_reach`, which says why that is not a box union).
        """
        rois = tuple(self._toolbox.rois())
        if not rois:
            return None
        settings = self._toolbox.detection_settings()
        huge = (1 << 30, 1 << 30)
        boxes = cp.roi_read_boxes(
            rois, huge,
            default_inner_diameter_px=settings.reference_inner_diameter_px,
            default_outer_diameter_px=settings.reference_outer_diameter_px,
        )
        centers = self._centers_of(rois)
        reach = cp.affine_reach(centers, self._chromatic.fitted_affines() if self._chromatic is not None else None)
        return centers, cp.grow_boxes(boxes, centers, *reach)

    def _schedule_auto_crop_readiness(self, *_args: object) -> None:
        """Coalesce a burst of ROI edits (a drag) into one readiness check once it goes quiet."""
        self._readiness_timer.start()

    def _emit_auto_crop_readiness(self, *_args: object) -> None:
        self.auto_crop_readiness_changed.emit(self.auto_crop_readiness())

    def _levelled_by_auto_rotate(self) -> bool:
        """Auto rotate levelled the array and the rotation has not been touched since."""
        return (
            self._levelled_at is not None
            and abs(float(self._geometry.settings().rotation_angle_deg) - self._levelled_at) < _LEVEL_EPS_DEG
        )

    def auto_crop_readiness(self) -> AutoCropReadiness:
        """Which of Auto crop's dependencies are met.

        The tilt is **measured** from the ROI lattice whenever there is one to measure - that is the authoritative
        answer, it survives a session reload and it follows later rotation edits. Before the ROIs exist (the
        maintainer's order of work: Auto rotate, then the ROIs, then the chromatic correction) there is nothing to
        measure, so Auto rotate's own measurement stands in until the rotation moves again.
        """
        chromatic = self._chromatic_on()
        # Centres only, no read boxes and no chromatic affines: this runs on every ROI edit, a drag's every
        # sample included (2026-10-21, after walking the fitted models here cost 300 ms per sample).
        centers = self._centers_of(self._toolbox.rois())
        rois = bool(len(centers))
        tilt = cp.lattice_tilt_deg(centers) if rois else None
        if tilt is not None:
            return AutoCropReadiness(
                levelled=abs(tilt) <= LEVEL_TOLERANCE_DEG, rois=rois, chromatic=chromatic, tilt_deg=tilt,
                level_note=f"tilt {tilt:+.2f}°",
            )
        if self._levelled_by_auto_rotate():
            return AutoCropReadiness(
                levelled=True, rois=rois, chromatic=chromatic,
                level_note="levelled by Auto rotate" + ("; the ROIs are not a lattice to re-measure it on" if rois else ""),
            )
        return AutoCropReadiness(
            levelled=None, rois=rois, chromatic=chromatic,
            level_note=("the ROIs are not a lattice, so the tilt cannot be measured" if rois
                        else "run Auto rotate, or place the ROIs to measure it"),
        )

    def auto_crop(self) -> None:
        """Crop to the ROI lattice: half a lattice constant of border beyond the outermost ROI centres. One undo
        step, nothing else changed - the levelling, the ROIs and the chromatic correction are dependencies the
        user runs themselves (`auto_crop_readiness`). Synchronous: integer arithmetic on the ROIs already placed,
        no image is read.

        The crop is computed on the **rotated + flipped canvas**, because that is the space
        `GeometrySettings.crop` is measured against (`transform.py`: rotate -> flip -> crop), while the ROIs are
        in processed space - i.e. relative to the crop in force now. Since Auto crop changes no rotation or flip,
        the two spaces differ by exactly the current crop's origin, the same shift `transform.py` applies.
        """
        raw_shape = self._raw_plane_shape() if self._raw_plane_shape is not None else None
        if raw_shape is None:
            self.status.emit("Auto crop: no dataset is loaded.")
            return
        lattice = self._lattice()
        if lattice is None:
            self.status.emit("Auto crop: place the ROIs first - the crop is built from the ROI lattice.")
            return
        centers, boxes = lattice
        settings = self._geometry.settings()
        uncropped = replace(settings, image_tools_enabled=True, crop=replace(settings.crop, enabled=False))
        canvas = spatial_output_shape((int(raw_shape[0]), int(raw_shape[1])), uncropped)
        origin = self._crop_origin(settings, canvas)
        crop = cp.lattice_crop(centers + origin, canvas, boxes=boxes + np.tile(origin, 2))
        if crop is None:
            self.status.emit("Auto crop: the ROIs are not a lattice (at least two rows and two columns are needed).")
            return
        self._geometry.set_crop(crop.x, crop.y, crop.width, crop.height)
        self.status.emit(self._auto_crop_report(crop, canvas, settings.image_tools_enabled))

    @staticmethod
    def _crop_origin(settings: GeometrySettings, canvas: tuple[int, int]) -> np.ndarray:
        """Where processed pixel (0, 0) sits on the canvas - the current crop's clamped origin, clamped exactly as
        `transform.py`'s `_combined_export_transform` does, or (0, 0) with no crop in force."""
        crop = settings.crop
        if not (bool(settings.image_tools_enabled) and crop.enabled and crop.width > 0 and crop.height > 0):
            return np.zeros(2)
        return np.array([max(0, min(int(crop.x), int(canvas[1]) - 1)), max(0, min(int(crop.y), int(canvas[0]) - 1))],
                        dtype=np.float64)

    @staticmethod
    def _auto_crop_report(crop: cp.LatticeCrop, canvas: tuple[int, int], image_tools_enabled: bool) -> str:
        text = (
            f"Auto crop: kept {crop.width} x {crop.height} px of {canvas[1]} x {canvas[0]} "
            f"({100.0 * crop.kept_fraction:.0f}% of the pixels) - {crop.cols} x {crop.rows} ROIs at pitch "
            f"{crop.pitch_x:.1f} x {crop.pitch_y:.1f} px, margin {crop.margin_x} x {crop.margin_y} px."
        )
        if crop.widened:
            text += (" The margin is wider than half the pitch: a reference ring (with the chromatic shift) reaches "
                     "further, and cropping at half the pitch would have cut into the edge ROIs.")
        if crop.clamped:
            text += " One or more sides hit the image edge, so that margin is narrower than asked."
        if not bool(image_tools_enabled):
            text += " Image tools are switched off, so the crop is stored but not applied yet."
        return text

    # -- finish (array runs) -------------------------------------------------------------

    def _on_completed(self, outcome: ArrayOutcome) -> None:
        if self._auto_rotate:
            self._auto_rotate = False
            self._finish_auto_rotate(outcome)
            return
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
