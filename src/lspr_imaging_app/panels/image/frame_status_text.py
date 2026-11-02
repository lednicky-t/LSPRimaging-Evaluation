"""The Image panel's frame status: what the dock title bar's subtitle says about the frame on screen
("Cube 0, 550 nm | 1:23.45 | comment"), as one line and as fixed-place fields.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 2). The code is the code that was in `ImagePanel`; the state
(`status`, the dock fields, the viewed frame, the widest texts) is owned here. What it needs from the panel comes in as small functions:
the slider colour of a role, and the two sinks for the line and the fields (the panel's `frame_status_changed` / `frame_columns_changed`).
"""

from __future__ import annotations

import html
from collections.abc import Callable

from ...dataset import DatasetModule
from ...selection import CubeDisplayModule
from ...selection.cube_display_module import format_elapsed_seconds
from ...selection.frame_roles_module import ROLE_DARK, ROLE_IGNORED, ROLE_REFERENCE, FrameRolesModule
from ..subtitle_column import SubtitleColumn

# The frame-role word of the dock subtitle: role -> (slider colour role, word), in the colour of its slider tick.
_ROLE_WORDS = {ROLE_DARK: ("dark", "Dark background"), ROLE_REFERENCE: ("reference", "Reference"), ROLE_IGNORED: ("ignored", "Ignored")}
_WIDEST_ROLE_WORD = max((word for _color, word in _ROLE_WORDS.values()), key=len)
_WIDEST_FRAME_TIME = "00:00:00.00"  # H:MM:SS.ss with a two-digit hour (the dock measures every digit as the widest)


class FrameStatusText:
    def __init__(
        self,
        dataset: DatasetModule,
        cube_display: CubeDisplayModule,
        frame_roles: FrameRolesModule | None,
        *,
        slider_color: Callable[[str], str],
        status_changed: Callable[[str, str], None],
        columns_changed: Callable[[tuple[SubtitleColumn, ...]], None],
    ) -> None:
        """``slider_color(role)``: the colour the slider ribbon shows for "dark" / "reference" / "ignored". ``status_changed(text,
        tooltip)`` and ``columns_changed(columns)``: the one-line status and the dock's fixed-place fields changed."""
        self._dataset = dataset
        self._cube_display = cube_display
        self._frame_roles = frame_roles
        self._slider_color = slider_color
        self._status_changed = status_changed
        self._columns_changed = columns_changed
        self._frame_key: tuple[int, float] | None = None  # the frame the dock subtitle describes
        # Mirrors the last `frame_status_changed` emission (same "cached
        # alongside the signal" shape as the panel's `_tool_status`) - lets a test
        # (or any other direct caller) read the current frame-status text
        # without needing a dock title bar in the loop to observe it.
        self.status = ""
        self._columns: tuple[SubtitleColumn, ...] = ()  # the same, as the dock's fixed-place fields
        # The widest cube and wavelength texts of the loaded dataset (sizes of two dock fields);
        # None = look them up on next use (dataset loaded / cleared, cube unit changed).
        self._widest: tuple[str, str] | None = None

    def forget_widest(self) -> None:
        """The dataset or the cube unit changed: look the widest texts up again on next use."""
        self._widest = None

    def forget_frame(self) -> None:
        """What is on screen is not a real frame (a background estimate): do not describe it again on a unit change."""
        self._frame_key = None

    def set_status(
        self, text: str, tooltip: str = "", columns: tuple[SubtitleColumn, ...] | None = None
    ) -> None:
        """Publish the frame status as one line (`frame_status_changed`) and as the dock's fixed-place
        fields (`frame_columns_changed`). A message with no frame in it ("No dataset loaded.") has no
        *columns*: it is shown in the comment field, the one that has room."""
        self.status = text
        self._columns = columns if columns is not None else self.fields(comment=text, tooltip=tooltip)
        self._status_changed(text, tooltip)
        self._columns_changed(self._columns)

    def columns(self) -> tuple[SubtitleColumn, ...]:
        """The dock fields of the last frame status (to seed a dock that connected after the panel was built)."""
        return self._columns

    def fields(
        self,
        *,
        role: tuple[str, str] | None = None,
        cube: str = "",
        wavelength: str = "",
        time: str = "",
        comment: str = "",
        tooltip: str = "",
    ) -> tuple[SubtitleColumn, ...]:
        """The dock fields, left to right: cube, wavelength, the frame's own time, comment, frame role
        (word, colour). The role word is empty on most frames and has a fixed width (the widest role word), so it
        comes last, at the right end: anywhere before the comment it would be a blank gap (2026-10-24). Every field but the comment has the width of the widest thing it can
        show, so an empty one keeps its place and the ones after it never move."""
        widest_cube, widest_wavelength = self._widest_texts()
        return (
            SubtitleColumn(cube, widest_cube),
            SubtitleColumn(wavelength, widest_wavelength),
            SubtitleColumn(time, _WIDEST_FRAME_TIME),
            SubtitleColumn(comment, tooltip=tooltip),
            SubtitleColumn(role[0] if role else "", _WIDEST_ROLE_WORD, role[1] if role else None),
        )

    def _widest_texts(self) -> tuple[str, str]:
        """The longest "Cube X" and "NNN nm" of the loaded dataset (the highest cube and wavelength: the
        most digits; the last cube has the longest start time). Looked up once per dataset / cube unit,
        not per frame, since the status is rebuilt on every Play step."""
        if self._widest is None:
            cubes = self._dataset.spectral_cubes()
            wavelengths = self._dataset.wavelengths()
            self._widest = (
                f"Cube {self._cube_display.label_for(cubes[-1])}" if cubes else "Cube 0",
                f"{max(wavelengths):.0f} nm" if wavelengths else "0 nm",
            )
        return self._widest

    def show_frame(self, cube_index: int, wavelength_nm: float) -> None:
        """The dock subtitle for a real frame: "Cube X, 550 nm | 1:23.45 | <comment>".
        The middle part is this frame's own experimental time (H:MM:SS.ss, or M:SS.ss
        under an hour, since the acquisition start - the same t = 0 as the
        cube's Time label, which is only the cube's *first* frame); it is left
        out when the frame has no timing. The comment is the experiment-record
        entry active when the frame was taken. "N/A" (with the reason in the
        tooltip) when there is none: either the frame is not linked to
        experiment data (no acquisition metadata or no timing for it), or no
        comment was recorded yet."""
        self._frame_key = (int(cube_index), float(wavelength_nm))
        linked, comment = self._dataset.frame_comment(cube_index, wavelength_nm)
        cube_text = f"Cube {self._cube_display.label_for(cube_index)}"
        wavelength_text = f"{wavelength_nm:.0f} nm"
        head = f"{cube_text}, {wavelength_text}"
        frame_seconds = self._dataset.frame_start_seconds(cube_index, wavelength_nm)
        time_text = "" if frame_seconds is None else format_elapsed_seconds(frame_seconds, decimals=2)  # tens of ms: frames of a cube are close
        if time_text:
            head = f"{head} | {time_text}"
        if comment:
            text, tooltip = f"{head} | {comment}", comment
        elif linked:
            text, tooltip = f"{head} | N/A", "No comment was recorded at the time this image was acquired."
        else:
            text, tooltip = (
                f"{head} | N/A",
                "This image is not linked to experiment data (no acquisition metadata, or no timing for it).",
            )
        role = self._role_word(cube_index, wavelength_nm)
        self.set_status(
            self._with_role_prefix(text, cube_index, wavelength_nm),
            tooltip,
            self.fields(
                role=role, cube=cube_text, wavelength=wavelength_text, time=time_text,
                comment=comment or "N/A", tooltip=tooltip,
            ),
        )

    def _role_word(self, cube_index: int, wavelength_nm: float) -> tuple[str, str] | None:
        """("Dark background" | "Reference" | "Ignored", its slider colour) for such a frame
        (`slider_colors.py`: the colour of its tick); None for any other frame."""
        if self._frame_roles is None:
            return None
        shown = _ROLE_WORDS.get(self._frame_roles.role_of(cube_index, wavelength_nm))
        if shown is None:
            return None
        color_role, word = shown
        return word, self._slider_color(color_role)

    def _with_role_prefix(self, text: str, cube_index: int, wavelength_nm: float) -> str:
        """The one-line status: the role word, in its colour, before the Cube/wavelength text; other
        frames: the text unchanged (plain). A rich-text line, so the whole text is escaped."""
        role = self._role_word(cube_index, wavelength_nm)
        if role is None:
            return text
        return f'<span style="color:{role[1]}">{role[0]}</span> {html.escape(text)}'

    def refresh(self) -> None:
        """Show the viewed frame's dock subtitle again (a role, a slider colour or the cube unit changed)."""
        if self._frame_key is not None:
            self.show_frame(*self._frame_key)
