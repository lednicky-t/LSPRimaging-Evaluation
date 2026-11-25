"""Find* (2026-11-25): fill in an array from the image, guided by the selected ROI(s) or an area selection, with a live preview on the canvas.

Screen purpose: the ROIs tab's "Array*" section has a second button, **Find***. It opens a pop-out (`ArrayFindPanel`, a child widget of the Image panel just under the
button, like the manual Array* one). The selected ROI(s) (or, without any, the area selection, or nothing at all) guide a search of the image; the pop-out shows what
was found as outlines on the canvas (blue = ROIs Apply makes, grey = a node of the array with no spot, amber = a similar spot that is not on the lattice). Apply makes the
new ROIs (`RoiToolbox.place_found_array`, one undo step); Cancel or the button again closes it and makes nothing.

Design: `docs/array_finder_design_2026-11-25.md`. This file wires the pieces and owns no ROI state; the maths is `roi/array_finder.py`, the threads are
`roi/array_find_task.py`, the ROI command is `RoiToolbox.place_found_array`. The old Array group stays (the maintainer compares the two).

Work is split in two (the Feature map's pattern): the **search** (image -> candidates; slow, cached) runs when the guide, the frame or the image settings change; the
**build** (candidates -> array; quick) runs on every change of a slider or switch. Both run off the GUI thread; the old picture stays until the new one is ready.

The image: **the reference wavelength of the cube on screen**, processed as the Image panel shows it (the Array group's rule); positions are therefore in the reference
frame ROIs are stored in. The outlines are drawn through the chromatic affine of the frame on screen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset.model import is_dark_frame_wavelength
from ...domain.roi_shapes import Border, MaskShape, Shape, nominal_diameter
from ...image_tools.background.module import BackgroundModule
from ...image_tools.chromatic.affine import invert_affine_matrix
from ...image_tools.chromatic.module import ChromaticModule
from ...image_tools.chromatic.warp import warp_boolean_mask_affine
from ...image_tools.geometry.module import GeometryModule
from ...image_tools.mask.module import MaskModule
from ...roi import RoiToolbox
from ...roi.array_find_task import ArrayFindTask
from ...roi.array_finder import Candidates, FinderResult, FinderSeed, FinderSettings, candidate_key
from ...roi.mask_regions import mask_regions
from ...roi.rim_area import equal_area_thickness
from ...roi.shape_guess import guess_shape
from ...selection import AreaSelectionModule, SelectionModule
from ...storage.ui_state_keys import ARRAY_FIND_KEYS, read
from ..ui_state import UiStateStore
from .array_controls import find_array_icon
from .array_find_ghosts import EMPTY, MADE, STRAY, found_outlines
from .array_find_panel import ArrayFindPanel
from .array_star import PopupFrame
from .array_star_ghosts import preview_alphas
from .general_group import style_general_icon_button
from .popout_placer import PopoutPlacer
from .sample_circles_item import SampleCirclesItem

GHOST_Z = 25
GHOST_WIDTH = 1.5
STRAY_COLOR = "#f59e0b"
SLIDER_DELAY_MS = 30
GUIDE_DELAY_MS = 150
MAX_AREA_BLOBS = 20
"""An area selection of more blobs than this is not a guide (it would make a template of unrelated things)."""


@dataclass(frozen=True)
class Guide:
    seeds: tuple[FinderSeed, ...]
    seed_ids: tuple[int, ...]
    """The ROIs the seeds are (empty for an area selection or no guide)."""
    text: str
    """One short line for the pop-out."""
    problem: str = ""
    tip: str = ""
    """What the finder does with this guide (the line's tooltip)."""


class ArrayFindController(QObject):
    status = pyqtSignal(str)  # one line for the status bar
    opened_changed = pyqtSignal(bool)

    def __init__(
        self,
        *,
        plot: pg.PlotItem,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        area_selection: AreaSelectionModule,
        image_shape: Callable[[], tuple[int, int] | None],
        chromatic: ChromaticModule,
        geometry: GeometryModule,
        background: BackgroundModule,
        mask: MaskModule,
        task: ArrayFindTask,
        current_frame: Callable[[], tuple[int, float]],
        resolve_reference: Callable[[], tuple[int, float] | None],
        load_plane: Callable[[int, float], np.ndarray],
        has_dataset: Callable[[], bool],
        host: QWidget,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._toolbox, self._selection, self._area_selection, self._image_shape = toolbox, selection, area_selection, image_shape
        self._chromatic, self._geometry, self._background, self._mask, self._task = chromatic, geometry, background, mask, task
        self._current_frame, self._resolve_reference, self._load_plane, self._has_dataset = current_frame, resolve_reference, load_plane, has_dataset
        self._store: UiStateStore | None = None
        self._host = host
        self._open = False

        self.button = QToolButton(host)
        self.button.setCheckable(True)
        self.button.setText("Find*")
        self.button.setToolTip(
            "Find*: let the image fill in an array. Select one ROI on a spot of the array (or two at its corners, or draw an area selection around a spot, or select nothing "
            "and let the finder look for the most common round spot); the preview shows the array it finds. Apply makes the new ROIs as one undo step."
        )
        style_general_icon_button(self.button)
        self.button.toggled.connect(self._on_button_toggled)

        self.popup = PopupFrame(host)  # a child of the Image panel, like the Array* pop-out
        self.popup.setObjectName("ArrayFindPopup")
        self.popup.hide()
        self.panel = ArrayFindPanel(geometry, self.popup)
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        self._placer = PopoutPlacer(self.popup, self.panel, self.button, host, self)
        self.panel.cancel_requested.connect(self.close)
        self.panel.apply_requested.connect(self.apply)
        self.panel.settings_changed.connect(self._on_settings_changed)
        self.panel.advanced_toggled.connect(lambda _opened: (self._placer.fit(), self._save()))
        self.panel.opacity_changed.connect(self._on_opacity)
        self.panel.content_resized.connect(lambda: self._open and self._placer.fit())

        self._ghosts = SampleCirclesItem(GHOST_WIDTH)
        self._ghosts.setZValue(GHOST_Z)
        self._ghosts.set_style(*preview_alphas(self.panel.opacity()))
        plot.addItem(self._ghosts)
        self._ghosts.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._recompute)
        selection.roi_selection_changed.connect(lambda *_a: self._schedule(GUIDE_DELAY_MS))
        area_selection.selection_changed.connect(lambda: self._schedule(GUIDE_DELAY_MS))
        toolbox.geometry_changed.connect(lambda *_a: self._schedule(GUIDE_DELAY_MS))
        task.searched.connect(self._on_searched)
        task.built.connect(self._on_built)

        self._guide = Guide((), (), "")
        self._candidates: Candidates | None = None
        self._candidates_key: object = None
        self._wanted_key: object = None
        self._searching_key: object = None
        self._mask_digest: tuple[np.ndarray, bytes] | None = None  # (the mask object, its hash): hashed once per mask, not per slider move
        self._serial = 0
        self._result: FinderResult | None = None
        self._result_settings: FinderSettings | None = None
        self._result_guide: Guide | None = None
        self._pending = False
        self._problem = True
        self._build_settings = FinderSettings()
        self._build_guide = self._guide
        self.refresh_theme()

    # -- open / close ---------------------------------------------------------------------------------------------

    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        if not self._open:
            self._open = True
            self.popup.show()
        self._placer.set_active(True)
        self._placer.fit()
        self.popup.raise_()
        self._set_button_checked(True)
        self._ghosts.setVisible(True)
        self._recompute()
        self.opened_changed.emit(True)

    def close(self) -> None:
        """Close without making anything (Cancel, or the Find* button again). A search still running is cancelled."""
        if not self._open:
            return
        self._open = False
        self._placer.set_active(False)
        self._timer.stop()
        self._task.cancel()
        self.popup.hide()
        self._ghosts.clear()
        self._ghosts.setVisible(False)
        self._set_button_checked(False)
        self._pending = False
        self.opened_changed.emit(False)

    def _on_button_toggled(self, checked: bool) -> None:
        self.open() if checked else self.close()

    def _set_button_checked(self, checked: bool) -> None:
        blocked = self.button.blockSignals(True)
        self.button.setChecked(checked)
        self.button.blockSignals(blocked)

    def _on_opacity(self, opacity: float) -> None:
        self._ghosts.set_style(*preview_alphas(opacity))
        self._save()

    # -- what triggers a new picture ------------------------------------------------------------------------------

    def on_frame(self) -> None:
        """The frame on screen changed (wavelength, cube, chromatic shift): redraw there; a new cube means a new search."""
        if self._open:
            self._schedule(GUIDE_DELAY_MS)

    def _on_settings_changed(self) -> None:
        self._save()
        self._schedule(SLIDER_DELAY_MS)

    def _schedule(self, delay_ms: int) -> None:
        if self._open:
            self._timer.start(delay_ms)

    def ghost_count(self) -> int:
        """How many outlines are drawn (tests)."""
        return self._ghosts.circle_count()

    # -- guide ----------------------------------------------------------------------------------------------------

    def _gather_guide(self, cube: int, settings: FinderSettings) -> Guide:
        ids = tuple(sorted(int(i) for i in self._selection.selected_roi_ids()))
        if ids:
            taken = [roi for roi in self._toolbox.rois() if roi.area_roi_id in ids and roi.array_id]
            if taken:
                return Guide((), ids, "", f"ROI {taken[0].area_roi_id} already belongs to {taken[0].array_id}: select ROIs that are not in an array.")
            clip = self._toolbox.copy_rois(ids, cube=cube)
            seeds = tuple(
                FinderSeed(item.geometry.center_x, item.geometry.center_y, nominal_diameter(item.geometry.shape, item.geometry.center_x, item.geometry.center_y), item.geometry.shape, item.geometry.border)
                for item in clip.rois
            )
            names = ", ".join(str(i) for i in ids) if len(ids) < 4 else f"{len(ids)} ROIs"
            corners = len(ids) == 2 and settings.seeds_are_corners
            text = f"Guide: ROI {names}" if len(ids) == 1 else (f"Guide: ROIs {names}" + (" (corners)" if corners else ""))
            tip = "The selected ROI(s) show the finder what to look for: size, shape and contrast of the spot."
            if corners:
                tip += " Two ROIs are taken as the corners of the array, so only the box between them is filled (Advanced: switch this off)."
            return Guide(seeds, ids, text, tip=tip + " The selected ROIs stay as they are.")
        blobs = self._area_blobs()
        if blobs is not None:
            return blobs
        return Guide(
            (), (), "Guide: none",
            tip="No ROI is selected and there is no area selection, so the finder looks for the most common round spot in the image. Select a ROI on a spot of the array "
            "(or two at its corners), or draw an area selection around a spot, to say what to look for.",
        )

    def _area_blobs(self) -> Guide | None:
        shape = self._image_shape()
        plane = None if shape is None else self._area_selection.selected_mask(shape)
        if plane is None or not plane.any():
            return None
        affine = np.asarray(self._chromatic.affine_for(self._current_frame()), dtype=np.float64)
        if not np.array_equal(affine[:2, :3], np.eye(2, 3)):  # the selection is of the frame on screen; ROIs are stored in the reference frame
            plane = warp_boolean_mask_affine(plane, invert_affine_matrix(affine), output_shape=plane.shape)
        regions = mask_regions(plane, together=False)
        if not regions or len(regions) > MAX_AREA_BLOBS:
            return Guide((), (), "", f"The area selection has {len(regions)} blobs: select one spot (or a few spots of the same kind).")
        gap = self._toolbox.detection_settings().default_border().gap_px
        seeds, kinds = [], []
        for region, _cx, _cy in regions:
            blob = np.zeros(plane.shape, dtype=bool)
            blob[region.y0 : region.y0 + region.mask.shape[0], region.x0 : region.x0 + region.mask.shape[1]] = region.mask
            guess = guess_shape(blob)
            kinds.append(guess.kind)
            rim = Border(gap, equal_area_thickness(guess.shape, gap))
            seeds.append(FinderSeed(guess.center_x, guess.center_y, nominal_diameter(guess.shape, guess.center_x, guess.center_y), guess.shape, rim))
        what = kinds[0] if len(set(kinds)) == 1 else "mixed shapes"
        return Guide(
            tuple(seeds), (), f"Guide: area selection ({what})",
            tip=f"The area selection holds {len(seeds)} spot{'s' if len(seeds) > 1 else ''}; each is fitted as a circle, a rectangle or (when neither fits well) a pixel map, and "
            "shows the finder what to look for. The new ROIs are made in that shape.",
        )

    # -- search and build ------------------------------------------------------------------------------------------

    def _recompute(self) -> None:
        self._timer.stop()
        if not self._open:
            return
        cube = int(self._selection.current_cube())
        settings = self.panel.settings()
        self._guide = self._gather_guide(cube, settings)
        self.panel.set_guide(self._guide.text or "Guide: none usable", self._guide.tip)
        if self._guide.problem:
            self._show_problem(self._guide.problem)
            return
        if not self._has_dataset():
            self._show_problem("No dataset loaded.", quiet=True)
            return
        reference = self._resolve_reference()
        if reference is None:
            self._show_problem("Choose a reference frame first (the Set reference or Find reference button beside the Cube and Wavelength sliders).")
            return
        wavelength = float(reference[1])
        if is_dark_frame_wavelength(wavelength):
            self._show_problem("The reference wavelength is the dark frame (0 nm). Choose a spectral wavelength as reference.")
            return
        mask_in, mask_id = self._mask_inputs(cube, wavelength, settings)
        key = (cube, wavelength, repr(self._geometry.settings()), repr(self._background.settings()), candidate_key(settings, self._guide.seeds), mask_id)
        self._wanted_key = key
        if self._candidates is not None and self._candidates_key == key:
            self._build(settings)
            return
        if self._task.is_searching() and self._searching_key == key:
            return  # this very search is already running: its answer builds the picture
        self._searching_key = key
        self._pending = True
        self.panel.set_apply_enabled(False)
        self.panel.set_message("Searching the image..." if self._guide.seeds else "Searching the image for the spot size (about a second)...")
        self._task.search(
            key, self._guide.seeds, settings, cube_index=cube, wavelength_nm=wavelength, geometry=self._geometry.settings(),
            background=self._background.settings(), load_plane=self._load_plane, authored_mask=None if mask_in is None else mask_in[0],
            mask_warp_affine=None if mask_in is None else mask_in[1],
        )

    def _mask_inputs(self, cube: int, wavelength: float, settings: FinderSettings) -> tuple[tuple[np.ndarray, np.ndarray | None] | None, object]:
        """``((authored mask, chromatic matrix or None), identity of that mask)`` for the frame searched, or ``(None, None)`` when the switch is off or no mask applies. The
        identity (frame, shape, a hash of the pixels, the matrix) is part of the search key: a changed mask is a new search, a moved slider is not."""
        if not settings.use_mask:
            return None, None
        resolution = self._mask.resolve_mask_source((cube, wavelength))
        if resolution is None:
            return None, None
        authored_frame, authored, _scope = resolution
        warp = None if authored_frame == (cube, wavelength) else np.asarray(self._chromatic.affine_between(authored_frame, (cube, wavelength)), dtype=np.float64)
        if self._mask_digest is None or self._mask_digest[0] is not authored:
            self._mask_digest = (authored, hashlib.blake2b(np.packbits(np.asarray(authored, dtype=bool)), digest_size=16).digest())
        return (authored, warp), (authored_frame, authored.shape, self._mask_digest[1], None if warp is None else warp.tobytes())

    def _on_searched(self, key: object, candidates: object, error: str) -> None:
        if key == self._searching_key:
            self._searching_key = None
        if not self._open or key != self._wanted_key:
            return  # a newer search is on its way (or the pop-out was closed)
        if error or candidates is None:
            self._candidates = None
            self._show_problem(error or "Nothing found.")
            return
        self._candidates, self._candidates_key = candidates, key
        self._build(self.panel.settings())

    def _build(self, settings: FinderSettings) -> None:
        assert self._candidates is not None
        self._serial += 1
        self._pending = True
        self._build_settings = settings
        self._build_guide = self._guide
        self.panel.set_apply_enabled(False)
        self._task.build(self._serial, self._candidates, settings)

    def _on_built(self, serial: int, result: object, error: str) -> None:
        if not self._open or serial != self._serial:
            return
        self._pending = False
        if error or result is None:
            self._show_problem(error or "Nothing found.")
            return
        assert isinstance(result, FinderResult)
        self._result, self._result_settings, self._result_guide = result, self._build_settings, self._build_guide
        self._draw(result)
        seeds_used = min(len(self._result_guide.seed_ids), int(result.is_seed.sum()))
        will_make = result.count - seeds_used
        problem = will_make <= 0
        self.panel.set_result(result)
        self.panel.set_message(" ".join(result.warnings), problem=False)  # the numbers are in the rows; the message keeps what to be careful about
        self.panel.set_count(f"{will_make} new ROI{'s' if will_make != 1 else ''}" + (f", {len(result.stray_xy)} stray" if len(result.stray_xy) else ""))
        self.panel.set_apply_enabled(not problem)
        self._problem = problem

    def _draw(self, result: FinderResult) -> None:
        frame = self._current_frame()
        affine = np.asarray(self._chromatic.affine_for(frame), dtype=np.float64)
        seeds = result.seeds
        template: Shape | None = seeds[0].shape if seeds and self._build_settings.fix_type and not isinstance(seeds[0].shape, MaskShape) else None
        seed_diameter = float(np.median([s.diameter_px for s in seeds])) if seeds else 0.0
        xs, ys, kinds = found_outlines(result, template, seed_diameter, affine)
        theme = get_active_theme()
        colours = {MADE: theme.accent_blue, EMPTY: theme.text_dim, STRAY: STRAY_COLOR}
        self._ghosts.set_circles(xs, ys, [colours[kind] for kind in kinds])

    def _show_problem(self, text: str, *, quiet: bool = False) -> None:
        """Nothing can be made right now: no outlines (a stale picture would mislead), the reason in the pop-out, Apply off."""
        self._ghosts.clear()
        self._result = None
        self._pending = False
        self.panel.set_result(None)
        self.panel.set_message(text, problem=not quiet)
        self.panel.set_count("")
        self.panel.set_apply_enabled(False)
        self._problem = True

    # -- apply ------------------------------------------------------------------------------------------------------

    def apply(self) -> None:
        """Make the ROIs of what is shown (one undo step), say what was made, close the pop-out."""
        result, settings, guide = self._result, self._result_settings, self._result_guide
        if result is None or settings is None or guide is None or self._pending or self._problem:
            self.status.emit("Find*: nothing made" + (" yet: the search is still running." if self._pending else ". " + self.panel.message()))
            return
        try:
            new = self._toolbox.place_found_array(result, settings, guide.seed_ids, self.panel.numbering_spec(), cube=int(self._selection.current_cube()))
        except (ValueError, KeyError) as error:
            text = str(error).strip("'\"")
            self.panel.set_message(text, problem=True)
            self.status.emit(f"Find*: nothing made. {text}")
            return
        shape = f"{result.rows} × {result.cols} " if result.ordered else "free "
        self.status.emit(f"Find*: added {len(new)} ROIs ({shape}{result.kind} array; Ctrl+Z undoes it).")
        self.close()

    # -- remembering ----------------------------------------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put the settings back as last left, then save each later change."""
        v = {name: read(store, key) for name, key in ARRAY_FIND_KEYS.items()}
        self.panel.set_values(strictness=float(v["slider"]), similarity=float(v["similarity"]), strength=float(v["strength"]), position=float(v["position_tol"]), size=float(v["size_tol"]))  # type: ignore[arg-type]
        for box, name in ((self.panel.fix_type, "fix_type"), (self.panel.fix_size, "fix_size"), (self.panel.place_missing, "place_missing"), (self.panel.corners, "corners"),
                          (self.panel.bright, "bright"), (self.panel.individual, "individual"), (self.panel.use_mask, "use_mask")):
            blocked = box.blockSignals(True)
            box.setChecked(bool(v[name]))
            box.blockSignals(blocked)
        try:
            self.panel.numbering.set_numbering(str(v["number_by"]), float(v["number_x"]), float(v["number_y"]))  # type: ignore[arg-type]
        except ValueError:
            pass
        self.panel.set_advanced_open(bool(v["advanced"]))
        self.panel.set_opacity(float(v["opacity"]))  # type: ignore[arg-type]
        self._ghosts.set_style(*preview_alphas(self.panel.opacity()))
        self._store = store

    def _save(self) -> None:
        if self._store is None:
            return
        s, p = self.panel.settings(), self.panel
        start = p.numbering.start()
        values = {
            "slider": p.strictness_position(), "similarity": s.similarity, "strength": s.strength, "position_tol": s.position_tol, "size_tol": s.size_tol,
            "fix_type": s.fix_type, "fix_size": s.fix_size, "place_missing": s.place_missing, "corners": s.seeds_are_corners, "bright": s.bright, "use_mask": s.use_mask,
            "individual": s.size_mode == "individual", "advanced": p.is_advanced_open(), "number_by": p.numbering.by(), "number_x": start[0], "number_y": start[1],
            "opacity": p.opacity(),
        }
        for name, value in values.items():
            self._store.set(ARRAY_FIND_KEYS[name].key, value)

    # -- look -------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        style_general_icon_button(self.button)
        theme = get_active_theme()
        self.button.setIcon(find_array_icon(theme.text_primary, theme.accent_blue))
        self.popup.update()
        self.panel.refresh_theme()
