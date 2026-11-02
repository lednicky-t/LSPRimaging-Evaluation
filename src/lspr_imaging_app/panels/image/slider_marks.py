"""What the Image panel's two navigation sliders mark besides their handle: the reference frame, the dark background, the ignored
wavelengths and cubes, the ROI row (a mark wherever the ROI layout changes), the slider colours and the tick labels.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 3). The code is the code that was in `ImagePanel`; the state of
the ROI row (the "only selected ROIs" switch, the cached ROI states, the update timer) is owned here. The panel still creates the sliders,
the row and the ribbon (they sit in its layout) and hands them in; what else it needs comes in as small functions.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from ...dataset import DatasetModule
from ...domain.roi_model import SCOPE_PERSISTENT
from ...roi import RoiToolbox
from ...selection import CubeDisplayModule, SelectionModule
from ...selection.frame_roles_module import FrameRolesModule
from ...storage.ui_state_keys import SLIDER_COLOR_KEYS, read
from ..ui_state import UiStateStore
from . import roi_layout_menu
from .data_axis_slider import DataAxisSlider
from .mark_colors import roi_color
from .slider_colors import SliderColors
from .slider_ribbon import SliderRibbon
from .slider_ticks import cube_slider_major_ticks, wavelength_slider_major_ticks
from .timeline_strip import StripMark, TimelineStrip


def _count_phrase(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class SliderMarks:
    def __init__(
        self,
        dataset: DatasetModule,
        selection: SelectionModule,
        roi_toolbox: RoiToolbox,
        frame_roles: FrameRolesModule | None,
        cube_display: CubeDisplayModule,
        *,
        cube_slider: DataAxisSlider,
        wavelength_slider: DataAxisSlider,
        roi_row: QWidget,
        roi_strip: TimelineStrip,
        slider_colors: SliderColors,
        slider_ribbon: SliderRibbon,
        menu_parent: QWidget,
        current_cube: Callable[[], int],
        current_wavelength: Callable[[], float],
        resolve_reference_frame: Callable[[], tuple[int, float] | None],
        roi_revision: Callable[[], int],
        status: Callable[[str], None],
        coalesce_ms: int,
    ) -> None:
        """``menu_parent``: the widget the ROI-row menu belongs to (the panel); it also owns the update timer. ``current_cube()`` /
        ``current_wavelength()``: the frame on screen. ``resolve_reference_frame()``: the reference frame, or ``None``. ``roi_revision()``:
        bumped by every ROI-toolbox change (the key of the cached ROI states). ``status(text)``: a transient status-bar message.
        ``coalesce_ms``: one ROI-row update per burst of ROI edits."""
        self._dataset = dataset
        self._selection = selection
        self._roi_toolbox = roi_toolbox
        self._frame_roles = frame_roles
        self._cube_display = cube_display
        self._cube_slider = cube_slider
        self._wavelength_slider = wavelength_slider
        self._roi_row = roi_row
        self._roi_strip = roi_strip
        self._slider_colors = slider_colors
        self._slider_ribbon = slider_ribbon
        self._menu_parent = menu_parent
        self._current_cube = current_cube
        self._current_wavelength = current_wavelength
        self._resolve_reference_frame = resolve_reference_frame
        self._roi_revision = roi_revision
        self._status = status
        self._roi_row_selected_only = False
        self._roi_states: tuple[int, ...] = ()  # see `roi_state_cubes`
        self._roi_states_key: tuple | None = None
        self._roi_marks_timer = QTimer(menu_parent)  # the ROI marks follow ROI edits, one update per burst (a drag)
        self._roi_marks_timer.setSingleShot(True)
        self._roi_marks_timer.setInterval(coalesce_ms)
        self._roi_marks_timer.timeout.connect(self.update_roi_marks)

    def forget_roi_states(self) -> None:
        """Another dataset: the ROI states are looked up again."""
        self._roi_states_key = None

    def update_marks(self) -> None:
        """What the sliders mark besides the handle (colours: `slider_colors.py`):
        the reference frame, the dark background and the ignored wavelengths.
        Run on every cube / wavelength step, so it only looks at the viewed
        cube's own wavelengths. The ignored *cubes* are marked by
        `_update_ignored_cube_ticks`, which only runs when they or the cube
        list change."""
        self._update_reference_highlight()
        self._update_wavelength_role_ticks()

    def update_all(self) -> None:
        """Every mark, after a change that is not a step (roles, colours, theme, a new dataset)."""
        self.update_marks()
        self._update_ignored_cube_ticks()
        self.update_roi_marks()

    # -- the ROI row (timeline_strip.py) ------------------------------------------------------------

    def on_roi_row_toggled(self, shown: bool) -> None:
        self._roi_row.setVisible(bool(shown))
        if shown:
            self.update_roi_marks()

    def on_roi_selected_only_toggled(self, only_selected: bool) -> None:
        self._roi_row_selected_only = bool(only_selected)
        self.update_roi_marks()

    def set_roi_row(self, shown: bool, selected_only: bool) -> None:
        """Put the ROI row's two toggles back as saved (no signal, so nothing is saved again)."""
        self._roi_row_selected_only = bool(selected_only)
        self._roi_row.setVisible(bool(shown))
        self._slider_ribbon.set_roi_row(shown, selected_only)
        self.update_roi_marks()

    def schedule_roi_marks(self, *_args: object) -> None:
        if not self._roi_row.isHidden():
            self._roi_marks_timer.start()

    def on_roi_selection_for_marks(self, *_args: object) -> None:
        if self._roi_row_selected_only:
            self.schedule_roi_marks()

    def _cube_phrase(self, cube: int) -> str:
        label = self._cube_display.label_for(cube)
        return f"Cube {cube}" if label == str(cube) else f"Cube {cube} ({label})"

    def on_mark_menu(self, mark: StripMark, global_pos: object) -> None:
        """Right click on a mark of the ROI row: go to its cube, make it individual / persistent, or delete it. Each acts on
        the whole point (every ROI's change at that cube; the menu says how many) and is one undo step. The first cube's
        mark is the start layout: only "Go to this cube" is offered there."""
        cubes = self._dataset.spectral_cubes()
        if not 0 <= mark.index < len(cubes):
            return
        cube = cubes[mark.index]
        toolbox = self._roi_toolbox
        is_start = mark.index == 0
        point = toolbox.layout_points().get(cube)
        if is_start:
            roi_ids = frozenset(roi.area_roi_id for roi in toolbox.rois())
            kind = SCOPE_PERSISTENT
        elif point is None:
            return  # the mark was stale (its change was undone meanwhile)
        else:
            roi_ids, kind = frozenset(point.roi_ids), point.kind
        shown = (roi_ids & self._selection.selected_roi_ids()) if self._roi_row_selected_only else roi_ids
        phrase = self._cube_phrase(cube)
        choice = roi_layout_menu.show_layout_point_menu(
            self._menu_parent, cube_text=phrase, kind=kind, is_start=is_start,
            roi_count=len(roi_ids), hidden_count=len(roi_ids) - len(shown), pos=global_pos,
        )
        if choice is None:
            return
        what = roi_layout_menu.who(len(roi_ids))
        try:
            if choice == roi_layout_menu.GO_TO:
                self._cube_slider.setValue(mark.index)
            elif choice == roi_layout_menu.DELETE and not is_start:
                toolbox.delete_layout_point(cube)
                self._status(f"{phrase}: layout change deleted ({what}).")
            elif choice == roi_layout_menu.MAKE_INDIVIDUAL and not is_start:
                toolbox.make_layout_point_individual(cube)
                self._status(f"{phrase} is now an individual change ({what}); the cubes after it go back to the earlier layout.")
            elif choice == roi_layout_menu.MAKE_PERSISTENT and not is_start:
                toolbox.make_layout_point_persistent(cube)
                self._status(f"{phrase} is now a persistent change ({what}); its layout carries on to the cubes after it.")
        except (ValueError, KeyError) as exc:
            self._status(f"ROI row: {exc}")

    def roi_state_cubes(self) -> tuple[int, ...]:
        """The cubes the ROI row marks, ascending: the first cube (the start layout) and every cube where the layout
        changes. What Previous / Next and Play step through in the "ROI states" mode, and what the wheel over the row hops
        between. Follows the "only selected ROIs" filter like the marks do (not whether the row is shown). Asked on every
        Play step in that mode, so it is cached on what it depends on."""
        cubes = self._dataset.spectral_cubes()
        selected = frozenset(self._selection.selected_roi_ids()) if self._roi_row_selected_only else None
        key = (self._roi_revision(), selected, cubes[:1], len(cubes))
        if key != self._roi_states_key:
            if cubes and self._roi_toolbox.rois():
                known = set(cubes)
                states = {cubes[0], *(cube for cube in self._roi_toolbox.layout_points(selected) if cube in known)}
                self._roi_states = tuple(sorted(states))
            else:
                self._roi_states = ()
            self._roi_states_key = key
        return self._roi_states

    def update_roi_marks(self) -> None:
        """The ROI row: a ``>`` at the first cube (the start layout, always persistent) and one mark per cube where the
        layout changes (`RoiToolbox.layout_points`: ``>`` persistent, a line individual), counting only the selected ROIs
        when that toggle is on. Cost grows with the number of ROI changes, not with the dataset; it runs when ROIs, the
        selection filter, the colour or the dataset change, never per cube step."""
        if self._roi_row.isHidden():
            return
        strip = self._roi_strip
        strip.set_color(roi_color())  # no colour control for the row (maintainer); follows the theme
        cubes = self._dataset.spectral_cubes()
        strip.setEnabled(bool(cubes))
        strip.set_axis_count(len(cubes))
        if not cubes:
            strip.set_marks(())
            strip.set_cursor(None)
            return
        toolbox = self._roi_toolbox
        existing = {roi.area_roi_id for roi in toolbox.rois()}
        only = (self._selection.selected_roi_ids() & existing) if self._roi_row_selected_only else None
        noun = "selected ROI" if only is not None else "ROI"
        marks: list[StripMark] = []
        if existing:
            count = len(existing) if only is None else len(only)
            marks.append(
                StripMark(0, SCOPE_PERSISTENT, f"{self._cube_phrase(cubes[0])} - start layout (persistent): {_count_phrase(count, noun)}")
            )
        position = {cube: index for index, cube in enumerate(cubes)}
        for cube, point in toolbox.layout_points(only).items():
            index = position.get(cube)
            if index is None or (index == 0 and point.kind == SCOPE_PERSISTENT):
                continue  # no such cube in this dataset; or the start layout, already marked
            how = "persistent, holds from here on" if point.kind == SCOPE_PERSISTENT else "individual, this cube only"
            marks.append(StripMark(index, point.kind, f"{self._cube_phrase(cube)} - {how}: {_count_phrase(len(point.roi_ids), noun)} changed"))
        strip.set_marks(marks)
        strip.set_cursor(position.get(self._current_cube()))

    def _update_wavelength_role_ticks(self) -> None:
        """The wavelength slider's dark and ignored ticks. The dark is a wavelength,
        not a cube, so it is marked only where the viewed cube has that wavelength,
        and the cube slider has no dark tick. An ignored wavelength is ignored in
        every cube, so its ticks are red on this slider whichever cube is viewed."""
        roles = self._frame_roles
        dark = roles.dark_wavelength() if roles is not None else None
        ignored = frozenset(float(wl) for wl in roles.ignored_wavelengths()) if roles is not None else frozenset()
        dark_index = None
        ignored_indices: frozenset[int] = frozenset()
        if (dark is not None or ignored) and self._dataset.spectral_cubes():
            wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
            if dark is not None:
                dark_index = next((i for i, wl in enumerate(wavelengths) if abs(float(wl) - float(dark)) < 1e-6), None)
            ignored_indices = frozenset(i for i, wl in enumerate(wavelengths) if float(wl) in ignored)
        colors = self._slider_colors
        self._wavelength_slider.set_dark_tick(dark_index, colors.color("dark") if dark_index is not None else None)
        self._wavelength_slider.set_ignored_ticks(ignored_indices, colors.color("ignored"))

    def _update_ignored_cube_ticks(self) -> None:
        """The cube slider's ticks of the ignored cubes (ignoring a cube drops every
        frame of that time point)."""
        roles = self._frame_roles
        cubes = self._dataset.spectral_cubes()
        ignored = roles.ignored_cubes() if roles is not None else ()
        indices = frozenset(cubes.index(cube) for cube in ignored if cube in cubes)
        self._cube_slider.set_ignored_ticks(indices, self._slider_colors.color("ignored"))

    def on_slider_color_changed(self, role: str, color: str | None) -> None:
        """A swatch in the ribbon was picked (or reset): keep it, show it on the swatch and the sliders. (The panel then
        refreshes the dock subtitle, whose Dark / Reference / Ignored word has the same colour.)"""
        self._slider_colors.set(role, color)
        self._slider_ribbon.slider_colors.refresh()
        self.update_all()

    def restore_colors(self, store: UiStateStore) -> None:
        """The slider colours back as last left, and keep saving them."""
        for role, key in SLIDER_COLOR_KEYS.items():
            saved = read(store, key)
            self._slider_colors.set(role, saved if isinstance(saved, str) else None)
        self._slider_ribbon.slider_colors.refresh()
        self.update_all()
        self._slider_ribbon.slider_colors.color_changed.connect(
            lambda role, color: store.set(SLIDER_COLOR_KEYS[role].key, color)
        )

    def _update_reference_highlight(self) -> None:
        """Highlights the slider handle (in the reference colour) whenever the
        currently-displayed cube/wavelength is the
        reference - ported from the stable app's
        `_update_reference_navigation_styles` (the stable app also recolored
        the spin boxes next to the sliders; this panel has none since
        2026-10-18, and the slider handle color carries the information)."""
        reference = self._resolve_reference_frame()
        if reference is None:
            self._cube_slider.set_reference_highlight(None)
            self._wavelength_slider.set_reference_highlight(None)
            self._cube_slider.set_reference_tick(None)
            self._wavelength_slider.set_reference_tick(None)
            return
        reference_cube, reference_wavelength = reference
        # Mark the reference's own tick on each rail (not just the handle
        # when it happens to sit on it). The wavelength tick only exists if
        # the *current* cube has that wavelength.
        cubes = self._dataset.spectral_cubes()
        cube_tick = cubes.index(int(reference_cube)) if int(reference_cube) in cubes else None
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength_tick = next(
            (i for i, wl in enumerate(wavelengths) if abs(float(wl) - float(reference_wavelength)) < 1e-6), None
        )
        color = self._slider_colors.color("reference")
        self._cube_slider.set_reference_tick(cube_tick, color)
        self._wavelength_slider.set_reference_tick(wavelength_tick, color)
        cube_is_reference = int(self._current_cube()) == int(reference_cube)
        wavelength_is_reference = abs(float(self._current_wavelength()) - float(reference_wavelength)) < 1e-6
        self._cube_slider.set_reference_highlight(color if cube_is_reference else None)
        self._wavelength_slider.set_reference_highlight(color if wavelength_is_reference else None)

    # -- slider tick labels ---------------------------------------------------

    @staticmethod
    def wavelength_slider_major_ticks(values: tuple[float, ...], view: tuple[float, float] | None = None) -> dict[int, str]:
        """See `slider_ticks.wavelength_slider_major_ticks`; `view`: the stretch on screen when the axis is zoomed."""
        return wavelength_slider_major_ticks(values, view)

    def cube_slider_major_ticks(self, values: tuple[int, ...]) -> dict[int, str]:
        """See `slider_ticks.cube_slider_major_ticks`; labels follow the cube display unit and the stretch on screen."""
        return cube_slider_major_ticks(values, self._cube_display.label_for, self._cube_slider.axis_window().view_for(len(values)))

    def refresh_cube_labels(self) -> None:
        """The Cube axis was zoomed or panned: label the ticks that are on screen now."""
        cubes = self._dataset.spectral_cubes()
        if cubes and len(cubes) == self._cube_slider.axis_window().count():
            self._cube_slider.set_ticks(list(cubes), self.cube_slider_major_ticks(cubes))

    def refresh_wavelength_labels(self) -> None:
        """The wavelength axis was zoomed or panned: label the ticks that are on screen now."""
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        window = self._wavelength_slider.axis_window()
        if wavelengths and len(wavelengths) == window.count():
            self._wavelength_slider.set_ticks(
                list(wavelengths), self.wavelength_slider_major_ticks(wavelengths, window.view_for(len(wavelengths)))
            )
