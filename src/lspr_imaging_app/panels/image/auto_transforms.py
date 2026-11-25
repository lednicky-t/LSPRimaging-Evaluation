"""What the Image tools tab's **Auto rotate** and **Auto crop** buttons do (2026-11-25: moved here from the retired Array group's `array_actions.py`).

Calls only public interfaces. The panel owns no ROI state: this turns clicks into commands.

Rules (maintainer, 2026-10-08 and later)
- **Auto rotate** (button in the Rotation group): finds the spot array with the **array finder** (`roi/array_finder.py`, the engine behind Array > Find; since 2026-11-25 the
  old detector is gone) on the **reference wavelength** of the current cube, then rotates the image by the measured tilt of the array's rows, however small (no question).
  **No ROIs are placed or changed** (existing ROIs follow the rotation through the geometry sync). One pass: the tilt is measured on the image as it is now shown, so it
  adds to the current rotation. Started on another wavelength, the user is asked whether to jump to the reference first. The finder is guided as in Array > Find: by the
  selected ROIs (their size, shape and contrast), or, with none selected, it looks for the most common round spot; its other settings (dark / bright spots, the mask, the
  thresholds) are the Find pop-out's own, as last set (`finder_settings`), except that the extent is never cut to two corner ROIs.
- **Auto crop** (button in the Crop group): crops to the ROI lattice - half a lattice constant of border beyond the outermost ROI centres
  (`dataset.chunk_plan.lattice_crop`; that docstring says why half a pitch is the number). Goal (maintainer): a smaller export whose chunk grid lines up with the ROI array.

  It **does the crop and nothing else** (maintainer, 2026-10-21, after a first version that levelled and detected too): levelling the image, placing the ROIs and fitting
  the chromatic correction are its three **dependencies**, which the user runs themselves. `auto_crop_readiness()` reports which are met; the button is green when all three
  are and amber otherwise, and an amber click opens a menu of the missing steps instead of cropping (`transforms_settings.py`). Each dependency is measured, never
  remembered: the lattice tilt from the ROI centres as they are now (`chunk_plan.lattice_tilt_deg`, within `LEVEL_TOLERANCE_DEG`), the ROIs from the toolbox, the chromatic
  correction from its fitted models. So all three survive a session reload and follow later edits.

Threading: the search runs in `roi/array_find_task.ArrayFindTask` (a plain thread, never `QThreadPool`: the plane is an OME-Zarr read); its progress joins the status-bar task
indicator under its own id (`TASK_ID`), and it can be cancelled there. Positions are in the processed space of the image shown (rotation and crop applied), like the ROIs.
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
from ...domain.roi_shapes import nominal_diameter
from ...image_tools.background.module import BackgroundModule
from ...image_tools.chromatic.module import ChromaticModule
from ...image_tools.geometry.model import GeometrySettings
from ...image_tools.geometry.module import GeometryModule
from ...image_tools.geometry.transform import spatial_output_shape
from ...roi import RoiToolbox
from ...roi.array_find_task import ArrayFindTask
from ...roi.array_finder import FinderResult, FinderSeed, FinderSettings
from ...selection import SelectionModule

logger = logging.getLogger(__name__)

TASK_ID = "auto_rotate"
TASK_LABEL = "Auto rotate"

_LEVEL_EPS_DEG = 0.005
"""A tilt below this is not worth a rotation: it is below what the rotation field shows (2 decimals)."""
MAX_TILT_DEG = 45.0
"""The finder names the lattice vector that lies most along x the row direction, so a real tilt is within +-45 degrees; more means rows and columns were taken the wrong way
round (a single column of spots, say) and the number cannot be trusted."""
_READINESS_DEBOUNCE_MS = 150
"""Auto crop's checklist waits this long after the last ROI/geometry edit before it is recomputed."""
LEVEL_TOLERANCE_DEG = 0.2
"""Auto crop calls the lattice straight within this. Not tighter: Auto rotate leaves ~0.1 deg of residual tilt (the measurement's own accuracy), and over a 1200 px array
0.2 deg moves a centre by about 4 px - small against a ~40 px margin, so the crop and the chunk grid are still right."""


@dataclass(frozen=True)
class AutoCropReadiness:
    """Which of Auto crop's three dependencies are met (`AutoTransforms.auto_crop_readiness`). ``levelled=None`` = it cannot be judged, which counts as not met but reads
    differently to the user - ``level_note`` says why in words, so the button's checklist never has to guess."""

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


class AutoTransforms(QObject):
    status = pyqtSignal(str)
    # `AutoCropReadiness`, whenever anything it is measured from changes (the ROIs, the image geometry the ROIs are positioned in, the chromatic correction). The Image panel
    # passes it on to the Auto crop button.
    auto_crop_readiness_changed = pyqtSignal(object)
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message (the task's own, re-emitted under this task's id)
    task_finished = pyqtSignal(str, str, str)  # task_id, "completed" | "failed" | "cancelled", message

    def __init__(
        self,
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
        finder_settings: Callable[[], FinderSettings] = FinderSettings,
        dialog_parent: QWidget | None = None,
        ask: Callable[[str, str], bool] | None = None,
        tell: Callable[[str, str], None] | None = None,
        task: ArrayFindTask | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._toolbox = toolbox
        self._selection = selection
        self._geometry = geometry
        self._background = background
        self._chromatic = chromatic
        self._load_plane = load_plane
        self._has_dataset = has_dataset
        self._raw_plane_shape = raw_plane_shape
        self._resolve_reference = resolve_reference
        self._finder_settings = finder_settings
        self._ask = ask or (lambda title, text: _ask(dialog_parent, title, text))
        self._tell = tell or (lambda title, text: _tell(dialog_parent, title, text))
        # The Auto rotate level the array was last put at. Auto crop's "tilt" is normally measured from the ROI lattice, but the maintainer's flow is Auto rotate *first*,
        # when there are no ROIs to measure yet (2026-10-21 report: "the leveling array is still present even though I run it"). Auto rotate did measure the tilt on the
        # image, so its result stands in until a lattice can confirm it - and only while the rotation is still the one it set.
        self._levelled_at: float | None = None

        # Its own finder task, not the Find pop-out's: a search started here must never cancel or answer that one.
        self._task = task if task is not None else ArrayFindTask(self)
        self._serial = 0
        self._phase = "idle"  # idle | search | build
        self._settings = FinderSettings()
        self._task.searched.connect(self._on_searched)
        self._task.built.connect(self._on_built)
        self._task.task_progress.connect(lambda _id, _label, fraction, message: self.task_progress.emit(TASK_ID, TASK_LABEL, fraction, message))
        self._task.task_finished.connect(self._on_task_finished)

        self._readiness_timer = QTimer(self)
        self._readiness_timer.setSingleShot(True)
        self._readiness_timer.setInterval(_READINESS_DEBOUNCE_MS)
        self._readiness_timer.timeout.connect(self._emit_auto_crop_readiness)
        # Auto crop's dependency state: the same inputs the Export tab's ROI layout watches (`export_tab.py`). Debounced, because `geometry_changed` fires on every sample of
        # a ROI drag and this only feeds a button's colour and its tooltip - nobody reads either mid-drag (2026-10-21).
        toolbox.geometry_changed.connect(self._schedule_auto_crop_readiness)
        toolbox.roi_ids_renumbered.connect(self._schedule_auto_crop_readiness)
        geometry.geometry_changed.connect(self._schedule_auto_crop_readiness)
        chromatic.chromatic_model_changed.connect(self._schedule_auto_crop_readiness)

    @property
    def task(self) -> ArrayFindTask:
        return self._task

    def is_running(self) -> bool:
        return self._phase != "idle"

    def cancel(self) -> None:
        self._task.cancel()

    def shutdown(self) -> None:
        """The application is quitting: stop the thread (the task waits briefly for it)."""
        self._task.shutdown()

    # -- Auto rotate (Image tools tab, Rotation group) ------------------------------------

    def auto_rotate(self) -> None:
        """Find the array on the reference wavelength and rotate the image by its tilt. Places no ROIs."""
        if self.is_running():
            return
        if not self._has_dataset():
            self._refuse("No dataset loaded.")
            return
        reference = self._resolve_reference()
        if reference is None:
            self._refuse("Choose a reference frame first (the Set reference or Find reference button beside the Cube and Wavelength sliders).")
            return
        wavelength = float(reference[1])
        if is_dark_frame_wavelength(wavelength):
            self._refuse("The reference wavelength is the dark frame (0 nm). Choose a spectral wavelength as reference.")
            return
        if abs(float(self._selection.current_wavelength()) - wavelength) > 1e-6:
            if not self._ask(
                "Auto rotate",
                f"Auto rotate works on the reference wavelength ({wavelength:g} nm), but {self._selection.current_wavelength():g} nm "
                "is shown. Go to the reference wavelength and continue?",
            ):
                return
            self._selection.set_wavelength(wavelength)
        cube = int(self._selection.current_cube())
        seeds = self._guide(cube)
        if seeds is None:
            return
        # A tilt needs the whole array, never a box between two corner ROIs; nothing is made, so nothing about missing nodes either.
        self._settings = replace(self._finder_settings(), seeds_are_corners=False, place_missing=False)
        self._serial += 1
        self._phase = "search"
        self.status.emit("Auto rotate: looking for the array...")
        self._task.search(
            self._serial, seeds, self._settings, cube_index=cube, wavelength_nm=wavelength, geometry=self._geometry.settings(),
            background=self._background.settings(), load_plane=self._load_plane,
        )

    def _guide(self, cube: int) -> tuple[FinderSeed, ...] | None:
        """The selected ROIs as the finder's guide (``()`` = none selected: it looks for the most common round spot); ``None`` after a refusal."""
        ids = tuple(sorted(int(i) for i in self._selection.selected_roi_ids()))
        if not ids:
            return ()
        try:
            clip = self._toolbox.copy_rois(ids, cube=cube)
        except (ValueError, KeyError) as error:
            self._refuse(str(error).strip("'\""))
            return None
        return tuple(
            FinderSeed(
                item.geometry.center_x, item.geometry.center_y, nominal_diameter(item.geometry.shape, item.geometry.center_x, item.geometry.center_y),
                item.geometry.shape, item.geometry.border,
            )
            for item in clip.rois
        )

    def _on_searched(self, key: object, candidates: object, error: str) -> None:
        if key != self._serial or self._phase != "search":
            return  # an answer to a run that is over
        if error or candidates is None:
            self._phase = "idle"
            self._refuse(error or "Nothing found.")
            return
        self._phase = "build"
        self._task.build(self._serial, candidates, self._settings)  # (a quick step; its answer carries the serial)

    def _on_built(self, serial: int, result: object, error: str) -> None:
        if serial != self._serial or self._phase != "build":
            return
        self._phase = "idle"
        if error or result is None:
            self._refuse(error or "Nothing found.")
            return
        assert isinstance(result, FinderResult)
        self._finish_auto_rotate(result)

    def _on_task_finished(self, _task_id: str, outcome: str, message: str) -> None:
        self.task_finished.emit(TASK_ID, outcome, message)
        if outcome == "cancelled" and self._phase != "idle":
            self._phase = "idle"
            self.status.emit("Auto rotate: cancelled.")

    def _refuse(self, message: str) -> None:
        """Nothing was done: the reason in the status bar and in a box (the run was asked for by a click, so a silent no-op would look like a bug)."""
        self.status.emit(f"Auto rotate: {message}")
        self._tell("Auto rotate", message)

    def _finish_auto_rotate(self, result: FinderResult) -> None:
        tilt = float(result.tilt_deg)
        if not result.ordered or not np.isfinite(tilt):
            self.status.emit("Auto rotate: no tilt could be measured (the spots found do not form a regular array); the rotation was not changed.")
            return
        if abs(tilt) > MAX_TILT_DEG:
            self.status.emit(
                f"Auto rotate: the measured tilt ({tilt:+.1f}°) is too large to trust (the finder may have taken the columns for rows); the rotation was not changed."
            )
            return
        current = float(self._geometry.settings().rotation_angle_deg)
        # Either way the array is now level as far as this measurement can tell: remember at which rotation, so Auto crop's checklist can tick "Level the array" before any
        # ROI exists (`_levelled_at`).
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
        """The correction is switched on and something is fitted - the cheap question (`has_fitted_models`), the only one the readiness check needs."""
        return bool(self._chromatic.settings().chromatic_correction_enabled) and bool(self._chromatic.has_fitted_models)

    @staticmethod
    def _centers_of(rois: Sequence) -> np.ndarray:
        return np.array([(float(roi.center_x), float(roi.center_y)) for roi in rois], dtype=np.float64)

    def _lattice(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The ROI centres and their read boxes in processed space, for the crop itself (not for the readiness check, which must stay cheap). ``None`` without ROIs.

        The boxes are deliberately **not** clipped to the image: a ring already reaching past the current crop has to widen the new one, not be cut to it. The chromatic
        correction is added as one conservative pair of numbers (`chunk_plan.affine_reach`, which says why that is not a box union).
        """
        rois = tuple(self._toolbox.rois())
        if not rois:
            return None
        settings = self._toolbox.detection_settings()
        huge = (1 << 30, 1 << 30)
        boxes = cp.roi_read_boxes(rois, huge, default_border=settings.default_border())
        centers = self._centers_of(rois)
        reach = cp.affine_reach(centers, self._chromatic.fitted_affines())
        return centers, cp.grow_boxes(boxes, centers, *reach)

    def _schedule_auto_crop_readiness(self, *_args: object) -> None:
        """Coalesce a burst of ROI edits (a drag) into one readiness check once it goes quiet."""
        self._readiness_timer.start()

    def _emit_auto_crop_readiness(self, *_args: object) -> None:
        self.auto_crop_readiness_changed.emit(self.auto_crop_readiness())

    def _levelled_by_auto_rotate(self) -> bool:
        """Auto rotate levelled the array and the rotation has not been touched since."""
        return self._levelled_at is not None and abs(float(self._geometry.settings().rotation_angle_deg) - self._levelled_at) < _LEVEL_EPS_DEG

    def auto_crop_readiness(self) -> AutoCropReadiness:
        """Which of Auto crop's dependencies are met.

        The tilt is **measured** from the ROI lattice whenever there is one to measure - that is the authoritative answer, it survives a session reload and it follows later
        rotation edits. Before the ROIs exist (the maintainer's order of work: Auto rotate, then the ROIs, then the chromatic correction) there is nothing to measure, so
        Auto rotate's own measurement stands in until the rotation moves again.
        """
        chromatic = self._chromatic_on()
        # Centres only, no read boxes and no chromatic affines: this runs on every ROI edit, a drag's every sample included (2026-10-21, after walking the fitted models
        # here cost 300 ms per sample).
        centers = self._centers_of(self._toolbox.rois())
        rois = bool(len(centers))
        tilt = cp.lattice_tilt_deg(centers) if rois else None
        if tilt is not None:
            return AutoCropReadiness(
                levelled=abs(tilt) <= LEVEL_TOLERANCE_DEG, rois=rois, chromatic=chromatic, tilt_deg=tilt, level_note=f"tilt {tilt:+.2f}°",
            )
        if self._levelled_by_auto_rotate():
            return AutoCropReadiness(
                levelled=True, rois=rois, chromatic=chromatic,
                level_note="levelled by Auto rotate" + ("; the ROIs are not a lattice to re-measure it on" if rois else ""),
            )
        return AutoCropReadiness(
            levelled=None, rois=rois, chromatic=chromatic,
            level_note=("the ROIs are not a lattice, so the tilt cannot be measured" if rois else "run Auto rotate, or place the ROIs to measure it"),
        )

    def auto_crop(self) -> None:
        """Crop to the ROI lattice: half a lattice constant of border beyond the outermost ROI centres. One undo step, nothing else changed - the levelling, the ROIs and the
        chromatic correction are dependencies the user runs themselves (`auto_crop_readiness`). Synchronous: integer arithmetic on the ROIs already placed, no image is read.

        The crop is computed on the **rotated + flipped canvas**, because that is the space `GeometrySettings.crop` is measured against (`transform.py`: rotate -> flip ->
        crop), while the ROIs are in processed space - i.e. relative to the crop in force now. Since Auto crop changes no rotation or flip, the two spaces differ by exactly
        the current crop's origin, the same shift `transform.py` applies.
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
        """Where processed pixel (0, 0) sits on the canvas - the current crop's clamped origin, clamped exactly as `transform.py`'s `_combined_export_transform` does, or
        (0, 0) with no crop in force."""
        crop = settings.crop
        if not (bool(settings.image_tools_enabled) and crop.enabled and crop.width > 0 and crop.height > 0):
            return np.zeros(2)
        return np.array([max(0, min(int(crop.x), int(canvas[1]) - 1)), max(0, min(int(crop.y), int(canvas[0]) - 1))], dtype=np.float64)

    @staticmethod
    def _auto_crop_report(crop: cp.LatticeCrop, canvas: tuple[int, int], image_tools_enabled: bool) -> str:
        text = (
            f"Auto crop: kept {crop.width} x {crop.height} px of {canvas[1]} x {canvas[0]} "
            f"({100.0 * crop.kept_fraction:.0f}% of the pixels) - {crop.cols} x {crop.rows} ROIs at pitch "
            f"{crop.pitch_x:.1f} x {crop.pitch_y:.1f} px, margin {crop.margin_x} x {crop.margin_y} px."
        )
        if crop.widened:
            text += (
                " The margin is wider than half the pitch: a reference rim (with the chromatic shift) reaches "
                "further, and cropping at half the pitch would have cut into the edge ROIs."
            )
        if crop.clamped:
            text += " One or more sides hit the image edge, so that margin is narrower than asked."
        if not bool(image_tools_enabled):
            text += " Image tools are switched off, so the crop is stored but not applied yet."
        return text
