"""``MaskRows`` - the two Mask rows under the sliders (2026-10-16, maintainer request): one under the Cube slider, one under
the wavelength slider. Like the ROIs row (`timeline_strip.py`), each is a thin row of marks on its slider's axis.

- **Cube row**: a mark at every cube that has a mask change of its own. ``>`` = a persistent mask (it applies to the whole
  cube and to every cube after it, until the next persistent mask); a thin line = individual masks (masks of single
  wavelengths of that cube); a **filled** ``>`` = both. No mark where nothing was drawn: there is no mask until you draw one.
- **Wavelength row**: the wavelengths *of the cube shown now* that have an individual mask of their own (thin lines). A
  persistent mask belongs to the whole cube, not to a wavelength, so it is on the Cube row only.

The data is `MaskModule`'s (`persistent_points`, `individual_cubes`, `individual_wavelengths`); this class only shows it, runs the
right-click menu (go to / make individual / make persistent / delete, all undoable: `MaskModule`'s row commands) and the wheel
over the wavelength row (the wheel over the Cube row steps through the slideshow's row-marks mode, wired by the panel).
**The wheel over either row hops between that row's marks, masks and standing scan pointers alike** (2026-10-18), by the app's one
rule for sets: scroll inside the set; when the scroll leaves it, go to the next / previous available set and land on its lowest /
highest element. The Cube row hops between `marked_cubes()` (on the wavelength shown now; the marked cubes are the one set). The
wavelength row hops between the marked wavelengths of the cube shown and, past the last (first), goes on into the nearest later
(earlier) cube that has one, to its first (last) marked wavelength. A row with nowhere to go leaves the wheel alone.
There is no selection filter: there is only one ignore mask.

**The scan's pointers** (2026-10-17, `MaskPointersModule`): both rows also show where the Mask-tab scan found something (a
particle, a hair, a bubble crossing the field). They are short ticks of their own colour (pink) in the *upper half* of the row, so
they never hide a mask mark; they are pointers, not masks, and change nothing. The Cube row has a tick at every cube with an event,
the wavelength row at the wavelengths of the cube shown that have one. A right click in the upper half opens their menu (go to,
dismiss, restore, and **mask** the particle events of that cube or frame: the same "Auto mask" step as the ribbon's, for one cube or
one frame); dismissed ones stay, drawn faintly, and the masking step skips them.

The Cube row's marks are rebuilt when the mask changes (coalesced, 100 ms) or the dataset does, never per cube step; the
wavelength row depends on the cube shown and is refreshed on every cube step, so it only looks at that cube's own wavelengths
(`MaskModule.individual_wavelengths` is O(1)).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...dataset import DatasetModule
from ...image_tools import MaskModule
from ...image_tools.mask.transient_detection import KIND_ARTEFACT, KIND_GLOBAL, KIND_PARTICLE, is_resting
from ...image_tools.mask.mask_pointers import MaskPointersModule
from ...image_tools.mask.mask_pointers_state import MaskPointer
from ...image_tools.mask.transient_masks import group_by_frame, maskable
from ...roi.model import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT
from ...selection import SelectionModule
from .data_axis_slider import AxisWindow
from .mask_row_menu import MenuEntry, show_entries_menu
from .timeline_strip import KIND_EVENT, KIND_EVENT_DISMISSED, KIND_MIXED, StripMark, TimelineStrip

MASK_COLOR = "#f97316"  # orange: green, red, purple, blue and gold are taken (Reference, Ignored, Dark, ROIs, the Cube handle)
EVENT_COLOR = "#f472b6"  # pink: the scan's pointers (Tailwind pink-400; not one of the colours above)
_COALESCE_MS = 100
_LISTED = 4  # wavelengths named in a tooltip before "..."

GO_TO = "go_to"
DELETE_PERSISTENT = "delete_persistent"
MAKE_INDIVIDUAL = "make_individual"
DELETE_INDIVIDUAL = "delete_individual"
MAKE_PERSISTENT = "make_persistent"
DELETE_ONE = "delete_one"
DISMISS = "dismiss"
RESTORE = "restore"
MASK_EVENTS = "mask_events"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _wavelength_list(wavelengths: tuple[float, ...]) -> str:
    shown = ", ".join(f"{wl:g}" for wl in wavelengths[:_LISTED])
    return f"{shown}, ..." if len(wavelengths) > _LISTED else shown


class MaskRows(QObject):
    def __init__(
        self,
        parent_widget: QWidget,
        mask: MaskModule,
        dataset: DatasetModule,
        selection: SelectionModule,
        cube_label: Callable[[int], str],
        *,
        cube_window: AxisWindow,
        wavelength_window: AxisWindow,
        title_style: str,
        status: Callable[[str], None],
        events: MaskPointersModule | None = None,
        auto_mask: Callable[[Sequence[MaskPointer]], None] | None = None,
    ) -> None:
        """``cube_label(cube)``: the name of a cube in the unit shown (number or start time). ``status(text)``: where a sentence
        for the user goes (the status line). The two windows are the sliders' own (`DataAxisSlider.axis_window`)."""
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._mask = mask
        self._dataset = dataset
        self._selection = selection
        self._cube_label = cube_label
        self._status = status
        self._events = events
        self._auto_mask = auto_mask  # the Auto mask step for some of the pointers (the ribbon's wand does all of them)
        self._shown = True
        self._wavelengths: tuple[float, ...] = ()  # the wavelengths of the cube shown, as the wavelength slider has them
        self._wavelength = 0.0  # the wavelength shown

        self.cube_strip = TimelineStrip(parent_widget)
        self.wavelength_strip = TimelineStrip(parent_widget)
        self.cube_strip.set_window(cube_window)
        self.wavelength_strip.set_window(wavelength_window)
        for strip in (self.cube_strip, self.wavelength_strip):
            strip.set_color(MASK_COLOR)
            strip.set_event_color(EVENT_COLOR)
        self.titles: list[QLabel] = []  # the panel gives them the width of the slider titles
        self.cube_row = self._make_row(self.cube_strip, title_style, "Mask: where the Cube axis has a mask of its own.")
        self.wavelength_row = self._make_row(
            self.wavelength_strip, title_style, "Mask: the wavelengths of the cube shown that have an individual mask."
        )

        self._timer = QTimer(self)  # one rebuild per burst of mask changes
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COALESCE_MS)
        self._timer.timeout.connect(self.refresh_now)
        mask.mask_changed.connect(self._schedule)
        mask.undo_skipped.connect(status)
        self.cube_strip.mark_menu_requested.connect(self._on_cube_menu)
        self.wavelength_strip.mark_menu_requested.connect(self._on_wavelength_menu)
        self.wavelength_strip.step_requested.connect(self._hop_wavelength)
        if events is not None:
            events.pointers_changed.connect(self._schedule)
            self.cube_strip.event_menu_requested.connect(self._on_cube_event_menu)
            self.wavelength_strip.event_menu_requested.connect(self._on_wavelength_event_menu)

    def _make_row(self, strip: TimelineStrip, title_style: str, tooltip: str) -> QWidget:
        row = QWidget(self._parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        title = QLabel("Mask", row)
        title.setStyleSheet(title_style)
        title.setToolTip(
            tooltip + " > holds from there on, a line is single wavelengths, a filled > is both. Right-click a mark for more. "
            "Pink ticks above them are the scan's pointers (not masks)."
        )
        layout.addWidget(title)
        layout.addWidget(strip, 1)
        self.titles.append(title)
        return row

    # -- showing / hiding ------------------------------------------------------------------------

    def set_shown(self, shown: bool) -> None:
        self._shown = bool(shown)
        self.cube_row.setVisible(self._shown)
        self.wavelength_row.setVisible(self._shown)
        if self._shown:
            self.refresh_now()

    # -- the marks -------------------------------------------------------------------------------

    def _schedule(self, *_args: object) -> None:
        if self._shown:
            self._timer.start()

    def refresh_now(self) -> None:
        self.refresh_cube_row()
        self.refresh_wavelength_row()

    def _phrase(self, cube: int) -> str:
        label = self._cube_label(cube)
        return f"Cube {cube}" if label == str(cube) else f"Cube {cube} ({label})"

    def refresh_cube_row(self) -> None:
        if not self._shown:
            return
        strip = self.cube_strip
        cubes = self._dataset.spectral_cubes()
        strip.setEnabled(bool(cubes))
        strip.set_axis_count(len(cubes))
        position = {cube: index for index, cube in enumerate(cubes)}
        persistent = self._mask.persistent_points()
        individual = self._mask.individual_cubes()
        marks: list[StripMark] = []
        for cube in sorted(set(persistent) | set(individual)):
            index = position.get(cube)
            if index is None:
                continue  # a mask of a cube this dataset does not have
            drawn_at = persistent.get(cube)
            wavelengths = individual.get(cube, ())
            singles = f"{_plural(len(wavelengths), 'wavelength')} ({_wavelength_list(wavelengths)} nm)"
            if drawn_at is not None and wavelengths:
                kind = KIND_MIXED
                tip = f"{self._phrase(cube)} - persistent mask (drawn at {drawn_at:g} nm) and individual masks on {singles}"
            elif drawn_at is not None:
                kind = SCOPE_PERSISTENT
                tip = f"{self._phrase(cube)} - persistent mask, holds from here on (drawn at {drawn_at:g} nm)"
            else:
                kind = SCOPE_INDIVIDUAL
                tip = f"{self._phrase(cube)} - individual mask on {singles}"
            marks.append(StripMark(index, kind, tip))
        strip.set_marks(marks)
        strip.set_event_marks(self._cube_event_marks(position))
        strip.set_cursor(position.get(int(self._selection.current_cube())))
        # A flag, not a callable into this object: a cube with no mark of its own still has other cubes to go to, so the wavelength
        # row takes the wheel whenever some cube has a marked wavelength. (A bound method on the strip would make a reference cycle
        # with the strip's owner; the flag is as fresh as the marks, which are rebuilt on every mask / pointer change.)
        self.wavelength_strip.hop_possible = bool(self._cubes_with_marked_wavelengths())

    def _cube_event_marks(self, position: dict[int, int]) -> list[StripMark]:
        if self._events is None:
            return []
        marks: list[StripMark] = []
        for cube, (standing, dismissed) in self._events.pointer_cubes().items():
            index = position.get(cube)
            if index is None:
                continue  # an event of a cube this dataset does not have
            marks.append(
                StripMark(index, KIND_EVENT if standing else KIND_EVENT_DISMISSED, self._cube_event_tip(cube, standing, dismissed))
            )
        return marks

    def _cube_event_tip(self, cube: int, standing: int, dismissed: int) -> str:
        records = [r for r in self._events.records_in_cube(cube) if not r.dismissed]
        kinds = [(KIND_PARTICLE, "particle"), (KIND_GLOBAL, "global event"), (KIND_ARTEFACT, "artefact")]
        parts = [_plural(n, label) for kind, label in kinds if (n := sum(1 for r in records if r.event.kind == kind))]
        head = f"{self._phrase(cube)} - scan: {', '.join(parts) if parts else 'nothing left standing'}"
        if dismissed:
            head += f" ({dismissed} dismissed)"
        passages: dict[int, list[MaskPointer]] = {}
        for record in records:
            if record.event.kind == KIND_PARTICLE and record.event.passage_id is not None:
                passages.setdefault(record.event.passage_id, []).append(record)
        lines = [head]
        for members in list(passages.values())[:_LISTED]:
            wavelengths = sorted({r.event.frame[1] for r in members})
            span = f"{wavelengths[0]:g}-{wavelengths[-1]:g} nm ({len(wavelengths)} frames"
            if all(is_resting(r.event) for r in members):  # arrived and stayed: masked like any other particle
                lines.append(f"  particle at rest {span})")
                continue
            steps = [r.event.step_px for r in members if r.event.step_px]
            speed = f", ~{sum(steps) / len(steps):.0f} px per frame" if steps else ""
            lines.append(f"  particle passage {span}{speed})")
        if len(passages) > _LISTED:
            lines.append("  ...")
        return "\n".join(lines)

    def set_cube_cursor(self, index: int | None) -> None:
        """The Cube slider moved to tick `index` (every cube step: only the hairline moves)."""
        self.cube_strip.set_cursor(index)

    def update_wavelength_row(self, wavelengths: tuple[float, ...], wavelength: float) -> None:
        """The cube or the wavelength shown changed: the wavelengths of the cube now shown, and the one shown."""
        self._wavelengths = tuple(wavelengths)
        self._wavelength = float(wavelength)
        self.refresh_wavelength_row()

    def set_wavelength_cursor(self, wavelength: float) -> None:
        """The wavelength shown changed within the same cube: only the hairline moves."""
        self._wavelength = float(wavelength)
        self.wavelength_strip.set_cursor(self._wavelengths.index(self._wavelength) if self._wavelength in self._wavelengths else None)

    def refresh_wavelength_row(self) -> None:
        if not self._shown:
            return
        strip = self.wavelength_strip
        wavelengths = self._wavelengths
        strip.setEnabled(bool(wavelengths))
        strip.set_axis_count(len(wavelengths))
        cube = int(self._selection.current_cube())
        with_mask = set(self._mask.individual_wavelengths(cube))
        strip.set_marks(
            [
                StripMark(index, SCOPE_INDIVIDUAL, f"{wavelength:g} nm - individual mask (cube {cube})")
                for index, wavelength in enumerate(wavelengths)
                if wavelength in with_mask
            ]
        )
        strip.set_event_marks(self._wavelength_event_marks(cube, wavelengths))
        strip.set_cursor(wavelengths.index(self._wavelength) if self._wavelength in wavelengths else None)

    def _wavelength_event_marks(self, cube: int, wavelengths: tuple[float, ...]) -> list[StripMark]:
        if self._events is None:
            return []
        by_wavelength: dict[float, list[MaskPointer]] = {}
        for record in self._events.records_in_cube(cube):
            by_wavelength.setdefault(record.event.frame[1], []).append(record)
        marks: list[StripMark] = []
        for index, wavelength in enumerate(wavelengths):
            records = by_wavelength.get(wavelength)
            if not records:
                continue
            kind = KIND_EVENT if any(not r.dismissed for r in records) else KIND_EVENT_DISMISSED
            marks.append(StripMark(index, kind, self._wavelength_event_tip(wavelength, cube, records)))
        return marks

    @staticmethod
    def _wavelength_event_tip(wavelength: float, cube: int, records: list[MaskPointer]) -> str:
        lines = [f"{wavelength:g} nm, cube {cube} - scan:"]
        for record in records:
            event = record.event
            bits = [f"{event.kind}, {event.area_px} px"]
            if event.passage_id is not None:
                if is_resting(event):
                    bits.append(f"at rest (passage {event.passage_id})")
                else:
                    bits.append(f"passage {event.passage_id}" + (f", ~{event.step_px:.0f} px per frame" if event.step_px else ""))
            if event.inferred:
                bits.append("found by gap filling")
            if record.dismissed:
                bits.append("dismissed")
            lines.append("  " + "; ".join(bits))
        return "\n".join(lines)

    def event_state_cubes(self) -> tuple[int, ...]:
        """The cubes with a pointer that is not dismissed, ascending: what the slideshow's "Events" mode steps through."""
        if self._events is None:
            return ()
        known = set(self._dataset.spectral_cubes())
        return tuple(cube for cube in self._events.active_pointer_cubes() if cube in known)

    def state_cubes(self) -> tuple[int, ...]:
        """The cubes with a mark on the Cube row, ascending: what the slideshow's "Mask states" mode steps through."""
        known = set(self._dataset.spectral_cubes())
        return tuple(sorted((set(self._mask.persistent_points()) | set(self._mask.individual_cubes())) & known))

    def marked_cubes(self) -> tuple[int, ...]:
        """Every cube the Cube row marks, ascending: a mask mark (persistent or individual) or a pink pointer that is not dismissed.
        What the wheel over the Cube row hops between (2026-10-18, maintainer: "hop between marks, masks and pointers")."""
        known = set(self._dataset.spectral_cubes())
        marked = set(self._mask.persistent_points()) | set(self._mask.individual_cubes())
        if self._events is not None:
            marked |= set(self._events.active_pointer_cubes())
        return tuple(sorted(marked & known))

    # -- the wheel over the wavelength row ---------------------------------------------------------------

    def _marked_wavelengths(self, cube: int) -> list[float]:
        """The wavelengths of `cube`, ascending, that carry an individual mask or a standing scan pointer (not a dismissed one)."""
        found = set(self._mask.individual_wavelengths(cube))
        if self._events is not None:
            found |= {record.event.frame[1] for record in self._events.records_in_cube(cube) if not record.dismissed}
        known = self._dataset.wavelengths_for_cube(cube)
        return sorted(wavelength for wavelength in found if wavelength in known)

    def _cubes_with_marked_wavelengths(self) -> set[int]:
        candidates = set(self._mask.individual_cubes())
        if self._events is not None:
            candidates |= set(self._events.active_pointer_cubes())
        return candidates & set(self._dataset.spectral_cubes())

    def _hop_wavelength(self, direction: int) -> None:
        """The wheel over the wavelength row, by the one rule every set in this app follows (2026-10-18, maintainer): scroll inside the
        set; when the scroll leaves it, go to the next (+1) / previous (-1) available set and land on its lowest (+1) / highest (-1)
        element. Here a set is a cube and its elements are its marked wavelengths, a mask or a scan pointer that is not dismissed.
        So: the next marked wavelength of the cube shown; past the last (first) one, the first (last) marked wavelength of the nearest
        later (earlier) cube that has one; nothing beyond the last such cube. The wheel over the wavelength slider does the same with
        every wavelength (`edge_step_requested`), the one over the Cube row with the marked cubes."""
        cube = int(self._selection.current_cube())
        current = float(self._selection.current_wavelength())
        here = self._marked_wavelengths(cube)
        ahead = [wavelength for wavelength in here if (wavelength > current if direction > 0 else wavelength < current)]
        if ahead:
            self._selection.set_wavelength(min(ahead) if direction > 0 else max(ahead))
            return
        others = sorted(c for c in self._cubes_with_marked_wavelengths() if (c > cube if direction > 0 else c < cube))
        for other in others if direction > 0 else reversed(others):
            wavelengths = self._marked_wavelengths(other)
            if wavelengths:
                self._selection.set_cube(other)
                self._selection.set_wavelength(wavelengths[0] if direction > 0 else wavelengths[-1])
                return

    # -- right click on a scan pointer ---------------------------------------------------------------------

    def _mask_entry(self, to_mask: list[MaskPointer], where: str) -> list[MenuEntry]:
        """The "Mask ..." menu entry (none without an Auto mask step to call): enabled when a particle pointer is standing there."""
        if self._auto_mask is None:
            return []
        frames = len(group_by_frame(to_mask))
        return [
            MenuEntry(
                MASK_EVENTS, f"Mask the particle events {where} ({_plural(frames, 'frame')})", enabled=bool(to_mask),
                tooltip=(
                    "Make an individual mask at each of these frames: the contour grown by the Auto mask px box, merged with the mask "
                    "already in effect. Ctrl+Z undoes it."
                    if to_mask
                    else "No particle pointer is standing here (global and artefact pointers are never masked)."
                ),
            )
        ]

    def _on_cube_event_menu(self, mark: StripMark, pos: object) -> None:
        cubes = self._dataset.spectral_cubes()
        if self._events is None or not 0 <= mark.index < len(cubes):
            return
        cube = cubes[mark.index]
        records = self._events.records_in_cube(cube)
        if not records:
            return  # a stale pointer
        standing = [r.id for r in records if not r.dismissed]
        dismissed = [r.id for r in records if r.dismissed]
        phrase = self._phrase(cube)
        to_mask = maskable(records)
        entries = [
            MenuEntry(GO_TO, "Go to this cube"),
            *self._mask_entry(to_mask, "of this cube"),
            MenuEntry(
                DISMISS, f"Dismiss the {_plural(len(standing), 'event')} of this cube", enabled=bool(standing), separator_before=True,
                tooltip="A false alarm: the pointer stays, faint, and the masking step skips it. Restore brings it back.",
            ),
            MenuEntry(RESTORE, f"Restore the {_plural(len(dismissed), 'dismissed event')}", enabled=bool(dismissed)),
        ]
        choice = show_entries_menu(self._parent, header=f"{phrase} - scan pointers", entries=entries, pos=pos)
        if choice == GO_TO:
            self._selection.set_cube(cube)
        elif choice == MASK_EVENTS and self._auto_mask is not None:
            self._auto_mask(to_mask)
        elif choice == DISMISS:
            self._events.dismiss(standing)
            self._status(f"{phrase}: {_plural(len(standing), 'event')} dismissed (right-click the pointer to restore).")
        elif choice == RESTORE:
            self._events.restore(dismissed)
            self._status(f"{phrase}: {_plural(len(dismissed), 'event')} restored.")

    def _on_wavelength_event_menu(self, mark: StripMark, pos: object) -> None:
        if self._events is None or not 0 <= mark.index < len(self._wavelengths):
            return
        wavelength = self._wavelengths[mark.index]
        cube = int(self._selection.current_cube())
        records = self._events.records_in_frame((cube, wavelength))
        if not records:
            return  # a stale pointer
        standing = [r.id for r in records if not r.dismissed]
        dismissed = [r.id for r in records if r.dismissed]
        phrase = self._phrase(cube)
        to_mask = maskable(records)
        entries = [
            MenuEntry(GO_TO, f"Go to {wavelength:g} nm"),
            *self._mask_entry(to_mask, "of this frame"),
            MenuEntry(
                DISMISS, f"Dismiss the {_plural(len(standing), 'event')} of this frame", enabled=bool(standing), separator_before=True,
                tooltip="A false alarm: the pointer stays, faint, and the masking step skips it. Restore brings it back.",
            ),
            MenuEntry(RESTORE, f"Restore the {_plural(len(dismissed), 'dismissed event')}", enabled=bool(dismissed)),
        ]
        choice = show_entries_menu(self._parent, header=f"{wavelength:g} nm, {phrase} - scan pointers", entries=entries, pos=pos)
        if choice == GO_TO:
            self._selection.set_wavelength(wavelength)
        elif choice == MASK_EVENTS and self._auto_mask is not None:
            self._auto_mask(to_mask)
        elif choice == DISMISS:
            self._events.dismiss(standing)
            self._status(f"{phrase}, {wavelength:g} nm: {_plural(len(standing), 'event')} dismissed.")
        elif choice == RESTORE:
            self._events.restore(dismissed)
            self._status(f"{phrase}, {wavelength:g} nm: {_plural(len(dismissed), 'event')} restored.")

    # -- right click on a mark -------------------------------------------------------------------------

    def _on_cube_menu(self, mark: StripMark, pos: object) -> None:
        cubes = self._dataset.spectral_cubes()
        if not 0 <= mark.index < len(cubes):
            return
        cube = cubes[mark.index]
        drawn_at = self._mask.persistent_points().get(cube)
        wavelengths = self._mask.individual_wavelengths(cube)
        if drawn_at is None and not wavelengths:
            return  # a stale mark (its masks are gone already)
        phrase = self._phrase(cube)
        entries = [MenuEntry(GO_TO, "Go to this cube")]
        if drawn_at is not None:
            clash = drawn_at in wavelengths
            entries.append(
                MenuEntry(
                    DELETE_PERSISTENT, f"Delete the persistent mask (drawn at {drawn_at:g} nm)",
                    tooltip="The cube follows the persistent mask before it again, if there is one. Ctrl+Z brings it back.",
                    separator_before=True,
                )
            )
            entries.append(
                MenuEntry(
                    MAKE_INDIVIDUAL, f"Make it individual ({drawn_at:g} nm only)", enabled=not clash,
                    tooltip=(
                        f"{drawn_at:g} nm of this cube already has an individual mask; delete that one first."
                        if clash
                        else "The mask then applies to that wavelength of this cube only; the cubes after it go back to the "
                        "persistent mask before this one (or to no mask)."
                    ),
                )
            )
        if wavelengths:
            entries.append(
                MenuEntry(
                    DELETE_INDIVIDUAL,
                    f"Delete the individual {'mask' if len(wavelengths) == 1 else 'masks'} of this cube "
                    f"({_plural(len(wavelengths), 'wavelength')}: {_wavelength_list(wavelengths)} nm)",
                    tooltip="Ctrl+Z brings them back.",
                    separator_before=drawn_at is None,
                )
            )
        kind = "persistent and individual" if drawn_at is not None and wavelengths else ("persistent" if drawn_at is not None else "individual")
        choice = show_entries_menu(self._parent, header=f"{phrase} - {kind} mask", entries=entries, pos=pos)
        try:
            if choice == GO_TO:
                self._selection.set_cube(cube)
            elif choice == DELETE_PERSISTENT:
                self._mask.delete_persistent_mask(cube)
                self._status(f"{phrase}: the persistent mask was deleted (Ctrl+Z brings it back).")
            elif choice == MAKE_INDIVIDUAL and drawn_at is not None:
                self._mask.make_mask_individual(cube)
                self._status(
                    f"{phrase}: the mask is now individual ({drawn_at:g} nm only); the cubes after it go back to the earlier "
                    "persistent mask (Ctrl+Z undoes)."
                )
            elif choice == DELETE_INDIVIDUAL:
                self._mask.delete_individual_masks([(cube, wl) for wl in wavelengths])
                self._status(f"{phrase}: {_plural(len(wavelengths), 'individual mask')} deleted (Ctrl+Z brings them back).")
        except (ValueError, KeyError) as exc:
            self._status(f"Mask row: {exc}")

    def _on_wavelength_menu(self, mark: StripMark, pos: object) -> None:
        if not 0 <= mark.index < len(self._wavelengths):
            return
        wavelength = self._wavelengths[mark.index]
        cube = int(self._selection.current_cube())
        if wavelength not in self._mask.individual_wavelengths(cube):
            return  # a stale mark
        has_persistent = cube in self._mask.persistent_points()
        phrase = self._phrase(cube)
        entries = [
            MenuEntry(GO_TO, f"Go to {wavelength:g} nm"),
            MenuEntry(
                MAKE_PERSISTENT, "Make it the persistent mask of this cube", enabled=not has_persistent, separator_before=True,
                tooltip=(
                    f"Cube {cube} already has its own persistent mask; delete it first."
                    if has_persistent
                    else "The mask then applies to every wavelength of this cube and to every cube after it, until the next "
                    "persistent mask."
                ),
            ),
            MenuEntry(DELETE_ONE, f"Delete this mask ({wavelength:g} nm)", tooltip="Ctrl+Z brings it back."),
        ]
        choice = show_entries_menu(
            self._parent, header=f"{wavelength:g} nm, {phrase} - individual mask", entries=entries, pos=pos
        )
        try:
            if choice == GO_TO:
                self._selection.set_wavelength(wavelength)
            elif choice == MAKE_PERSISTENT:
                self._mask.make_mask_persistent((cube, wavelength))
                self._status(f"{phrase}: the {wavelength:g} nm mask is now the persistent mask; it holds from here on (Ctrl+Z undoes).")
            elif choice == DELETE_ONE:
                self._mask.delete_individual_masks([(cube, wavelength)])
                self._status(f"{phrase}: the {wavelength:g} nm mask was deleted (Ctrl+Z brings it back).")
        except (ValueError, KeyError) as exc:
            self._status(f"Mask row: {exc}")
