"""What the Sensorgram's plan bar shows and where (pure, no Qt): the display settings, the steps converted to the plot's x axis, the colours, where the text goes and
how much room the bar needs. ``panels/sensorgram/plan_bar.py`` draws what this computes; ``plan_group.py`` holds the controls that make the settings.

Design: ``docs/experimental_plan_bar_design_2026-10-26.md``.

- **Steps on the axis** (`build_bar_steps`): each `EffectiveStep` gets its x range through the `AxisMap`, the text the label rule picks, a colour and a tooltip.
- **Colours**: the user's colour wins; otherwise one from a fixed palette, taken in the order in which each distinct *solution* (the text before the separator) first
  appears, so a solution that comes back (water between samples) always has the same colour and switching the text mode never recolours the bar.
- **Text position** (`text_top_y`): five stops, high to low, in two modes. *Bar*: inside the bar (top, upper middle, middle, lower middle, bottom). *Data*: outside the
  bar, relative to the traces inside the step (far above, just above, on the data, just below, far below). "Top" and "bottom" of the data are the 98th and 2nd percentile of
  every value drawn in the step (`section_levels`): a percentile ignores a stray outlier like a median does, but stays above (below) nearly all points, where a median of the
  per-cube maxima would run through a drifting trace. The middle stop is the median.
- **Room** (`reserved_range`): the bar must not hide a trace, so the y range is widened by the bar's height on its side (and, in Data mode, by the room the labels need).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .axis_map import AxisMap
from .edits import EffectiveStep
from .label_rule import DEFAULT_SEPARATORS, MODE_SOLUTION, MODE_WHOLE, shown_label

POSITION_TOP = "top"
POSITION_BOTTOM = "bottom"
POSITIONS = (POSITION_TOP, POSITION_BOTTOM)
ALIGN_BAR = "bar"
ALIGN_DATA = "data"
ALIGNS = (ALIGN_BAR, ALIGN_DATA)

HEIGHT_MAX_PERCENT = 10
HEIGHT_FULL = HEIGHT_MAX_PERCENT + 1  # the stop after 10 %: the bar fills the whole plot, behind the traces
STOP_MIN, STOP_MAX = 1, 5
FONT_RANGE = (6, 24)  # points

BAR_TEXT_PADDING_PX = 2  # between the text and the edge of the bar
NEAR_GAP = 0.5  # text heights between the data and the label, "just above / below"
FAR_GAP = 3.0  # "far above / below"
MIN_TEXT_WIDTH_PX = 40.0  # a bar narrower than this shows no text (the tooltip still has it)

# Tableau 10: ten categorical colours that stay apart on a dark and a light plot.
STEP_COLORS: tuple[str, ...] = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
)

BAR_STOP_NAMES = ("Bottom of the bar", "Lower middle of the bar", "Middle of the bar", "Upper middle of the bar", "Top of the bar")
DATA_STOP_NAMES = (
    "Far below the data", "Just below the data", "On the data (median)", "Just above the data", "Far above the data",
)


@dataclass(frozen=True)
class PlanBarSettings:
    """Everything the ribbon group sets. Display only: changes no number."""

    visible: bool = True
    position: str = POSITION_TOP
    height_stop: int = 5  # 1 - 10 (percent of the plot height), or HEIGHT_FULL
    alpha: float = 0.55  # opacity of the fill, 0 - 1
    text_mode: str = MODE_WHOLE  # label_rule.MODE_WHOLE / MODE_SOLUTION
    separators: tuple[str, ...] = DEFAULT_SEPARATORS
    text_align: str = ALIGN_BAR
    text_stop: int = 3  # 1 (low) - 5 (high), meaning depends on text_align
    text_pt: int = 9

    @property
    def is_full(self) -> bool:
        return self.height_stop >= HEIGHT_FULL

    @property
    def height_fraction(self) -> float:
        """The bar's height as a fraction of the plot height (1.0 for Full)."""
        return 1.0 if self.is_full else min(max(self.height_stop, 1), HEIGHT_MAX_PERCENT) / 100.0

    def stop_name(self) -> str:
        names = DATA_STOP_NAMES if self.text_align == ALIGN_DATA else BAR_STOP_NAMES
        return names[min(max(self.text_stop, STOP_MIN), STOP_MAX) - 1]


@dataclass(frozen=True)
class SectionLevels:
    """The traces' values inside one step (the same unit as the plot's y)."""

    low: float  # 2nd percentile
    median: float
    high: float  # 98th percentile


@dataclass(frozen=True)
class PlanBarStep:
    index: int  # position in ExperimentPlanModule.steps(), the handle for its commands
    x_start: float
    x_end: float
    text: str  # as shown: the label rule already applied
    color: str  # '#rrggbb'
    tooltip: str
    levels: SectionLevels | None
    edited: bool = False
    roles: tuple[str, ...] = ()  # "B1", "S1" ...: the evaluation roles this step plays, drawn as small chips on the bar
    added: bool = False  # a step the user made


# -- the data inside a step -----------------------------------------------------------------------------------------------


def section_levels(
    lines: Iterable[tuple[np.ndarray, np.ndarray]], x_start: float, x_end: float, *, low_pct: float = 2.0, high_pct: float = 98.0
) -> SectionLevels | None:
    """Percentiles of every finite y whose x lies in [x_start, x_end) over all `lines` ((x, y) arrays: each drawn line and the edges of its band).
    None when no point lies inside."""
    parts: list[np.ndarray] = []
    for x, y in lines:
        x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        inside = np.isfinite(x) & np.isfinite(y) & (x >= x_start) & (x < x_end)
        if inside.any():
            parts.append(y[inside])
    if not parts:
        return None
    values = np.concatenate(parts)
    low, median, high = np.percentile(values, [low_pct, 50.0, high_pct])
    return SectionLevels(low=float(low), median=float(median), high=float(high))


# -- colours --------------------------------------------------------------------------------------------------------------


def automatic_colors(notes: Sequence[str], separators: Sequence[str] = DEFAULT_SEPARATORS) -> list[str]:
    """One palette colour per note: by the order in which each distinct solution first appears (case ignored), cycling after ten."""
    order: dict[str, int] = {}
    colors: list[str] = []
    for note in notes:
        key = shown_label(note, MODE_SOLUTION, separators).casefold()
        index = order.setdefault(key, len(order))
        colors.append(STEP_COLORS[index % len(STEP_COLORS)])
    return colors


def _rgb(color: str) -> tuple[float, float, float]:
    text = color.lstrip("#")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def contrast_text_color(fill: str, alpha: float, background: str) -> str:
    """Dark or light text for a fill drawn with `alpha` over `background`: judged on the blended colour, so it stays readable at any transparency."""
    a = min(max(float(alpha), 0.0), 1.0)
    blended = [a * f + (1.0 - a) * b for f, b in zip(_rgb(fill), _rgb(background))]
    luminance = 0.2126 * blended[0] + 0.7152 * blended[1] + 0.0722 * blended[2]
    return "#0f1720" if luminance > 0.58 else "#f8fbff"


# -- text, tooltip ----------------------------------------------------------------------------------------------------------


def describe_devices(devices: Mapping[str, object] | None) -> list[str]:
    """Pump channels and valves of a step as short lines, from the plan file's ``devices`` layout. Anything unexpected is left out, never guessed."""
    lines: list[str] = []
    if not isinstance(devices, Mapping):
        return lines
    channels = devices.get("channels")
    if isinstance(channels, Sequence) and not isinstance(channels, (str, bytes)):
        parts = []
        for entry in channels:
            if not isinstance(entry, Mapping):
                continue
            try:
                flow = float(entry["flow_ul_min"])
            except (KeyError, TypeError, ValueError):
                continue
            direction = str(entry.get("direction") or "").strip()
            parts.append(f"ch{entry.get('channel', '?')} {flow:g} µL/min{' ' + direction if direction else ''}")
        if parts:
            lines.append("Pump: " + ", ".join(parts))
    valves = devices.get("valves")
    if isinstance(valves, Mapping) and valves:
        lines.append("Valves: " + ", ".join(f"{name} {state}" for name, state in valves.items()))
    return lines


def _tooltip(step: EffectiveStep, x_start: float, x_end: float, time_mode: bool) -> str:
    lines = [f"Step {step.order}: {step.label}" if step.label else f"Step {step.order}"]
    if time_mode:
        lines.append(f"{x_start:.1f} s to {x_end:.1f} s ({step.duration_s:.1f} s)")
    else:
        lines.append(f"cube {x_start:.1f} to cube {x_end:.1f} ({step.duration_s:.1f} s)")
    lines.extend(describe_devices(step.devices))
    for role in step.roles:
        lines.append(f"{'Baseline' if role.startswith('B') else 'Signal'} step of evaluation pair {role[1:]} ({role}).")
    if step.is_added:
        lines.append("Added by you (not in the dataset).")
    elif step.edited:
        lines.append("Edited (the measured step is kept in the dataset).")
    return "\n".join(lines)


def build_bar_steps(
    steps: Sequence[EffectiveStep],
    axis: AxisMap,
    lines: Sequence[tuple[np.ndarray, np.ndarray]],
    *,
    text_mode: str = MODE_WHOLE,
    separators: Sequence[str] = DEFAULT_SEPARATORS,
    time_mode: bool = True,
) -> list[PlanBarStep]:
    """The steps on the plot's x axis. `lines`: (x, y) of everything drawn, for the levels of the Data text mode."""
    automatic = automatic_colors([step.label for step in steps], separators)
    out: list[PlanBarStep] = []
    for index, step in enumerate(steps):
        x_start, x_end = axis.x_at(step.start_unix_ms), axis.x_at(step.end_unix_ms)
        out.append(
            PlanBarStep(
                index=index, x_start=x_start, x_end=x_end, text=shown_label(step.label, text_mode, separators),
                color=step.color or automatic[index], tooltip=_tooltip(step, x_start, x_end, time_mode),
                levels=section_levels(lines, x_start, x_end), edited=step.edited, roles=step.roles, added=step.is_added,
            )
        )
    return out


# -- editing with the pointer ---------------------------------------------------------------------------------------------


def click_selection(
    current: Sequence[int], anchor: int | None, clicked: int, available: Sequence[int], *, ctrl: bool = False, shift: bool = False
) -> tuple[tuple[int, ...], int | None]:
    """The selection after a left click on step `clicked`, and the new anchor (the step a Shift-click measures its range from). `available`: the indices of the steps on the bar.

    A plain click selects that step only. Ctrl adds it, or takes it out when it is already selected. Shift adds it and every step between the anchor and it (the anchor stays
    where it was); with no anchor to measure from it is a plain click. Ctrl with Shift is Shift."""
    present = sorted(set(available))
    if clicked not in present:
        return tuple(sorted(i for i in current if i in present)), anchor
    if shift and anchor is not None and anchor in present:
        low, high = sorted((anchor, clicked))
        return tuple(sorted({i for i in current if i in present} | {i for i in present if low <= i <= high})), anchor
    if ctrl:
        chosen = {i for i in current if i in present}
        chosen.symmetric_difference_update({clicked})
        return tuple(sorted(chosen)), clicked
    return (clicked,), clicked


def grab_edge(steps: Sequence[PlanBarStep], selected: Collection[int], x: float, tolerance: float) -> tuple[int, str, bool] | None:
    """Which edge of a *selected* step the pointer at data x takes: (step index, "start" | "end", together), or None. The nearest edge within `tolerance` (data units) wins; at a
    tie the earlier step's end. Only selected steps are looked at, so two steps that meet at one place never compete: the selection says whose edge it is.

    `together` is True when the edge is a boundary shared with a neighbouring step that is selected too: then both steps' edges move (it is reported as the end of the earlier step)."""
    best: tuple[tuple[float, int, int], int, str] | None = None
    for position, step in enumerate(steps):
        if step.index not in selected:
            continue
        for edge, at in (("end", step.x_end), ("start", step.x_start)):
            distance = abs(float(x) - at)
            if distance > tolerance:
                continue
            key = (distance, position, 0 if edge == "end" else 1)
            if best is None or key < best[0]:
                best = (key, position, edge)
    if best is None:
        return None
    _key, position, edge = best
    step = steps[position]
    if edge == "start" and position > 0 and steps[position - 1].index in selected and abs(steps[position - 1].x_end - step.x_start) <= 1e-9:
        return steps[position - 1].index, "end", True
    if edge == "end" and position + 1 < len(steps) and steps[position + 1].index in selected and abs(step.x_end - steps[position + 1].x_start) <= 1e-9:
        return step.index, "end", True
    return step.index, edge, False


def boundary_readout(x: float, time_mode: bool) -> str:
    """What the small label shows while a boundary is dragged: seconds with one decimal, or the cube number (a whole number when it is on a cube)."""
    if time_mode:
        return f"{x:.1f} s"
    return "cube " + f"{x:.2f}".rstrip("0").rstrip(".")


# -- where the text goes (device pixels, y down) ----------------------------------------------------------------------------


def text_top_y(
    settings: PlanBarSettings,
    *,
    text_height: float,
    area_top: float,
    area_bottom: float,
    bar_top: float,
    bar_bottom: float,
    levels_y: tuple[float, float, float] | None = None,
) -> float | None:
    """The top edge of the text box in device pixels (y grows downwards), or None when the text does not fit and is not drawn.

    `area_*`: the plot area. `bar_*`: the bar's strip (the whole area for Full). `levels_y`: the device y of the step's (high, median, low) levels, for the Data mode;
    without them (no point in the step) the text falls back to the middle of the bar."""
    pad = BAR_TEXT_PADDING_PX
    stop = min(max(int(settings.text_stop), STOP_MIN), STOP_MAX)
    th = float(text_height)

    def in_bar(at: int) -> float | None:
        if (bar_bottom - bar_top) - 2 * pad < th:  # the bar is lower than the text
            return None
        if at == 5:
            top = bar_top + pad
        elif at == 1:
            top = bar_bottom - pad - th
        else:  # the text's centre a quarter, a half or three quarters of the way down the bar
            top = bar_top + {4: 0.25, 3: 0.5, 2: 0.75}[at] * (bar_bottom - bar_top) - th / 2.0
        return min(max(top, bar_top + pad), bar_bottom - pad - th)

    if settings.text_align != ALIGN_DATA or levels_y is None:
        return in_bar(stop if settings.text_align != ALIGN_DATA else 3)

    high_y, median_y, low_y = levels_y
    near, far = NEAR_GAP * th, FAR_GAP * th
    top = {5: high_y - far - th, 4: high_y - near - th, 3: median_y - th / 2.0, 2: low_y + near, 1: low_y + far}[stop]
    free_top, free_bottom = area_top + pad, area_bottom - pad
    if not settings.is_full:  # the label stays clear of the bar
        if settings.position == POSITION_TOP:
            free_top = max(free_top, bar_bottom + pad)
        else:
            free_bottom = min(free_bottom, bar_top - pad)
    if free_bottom - free_top < th:
        return None
    return min(max(top, free_top), free_bottom - th)


# -- room for the bar -------------------------------------------------------------------------------------------------------


def reserved_range(low: float, high: float, top_fraction: float, bottom_fraction: float) -> tuple[float, float]:
    """The y range [low, high] widened so that the data takes the plot's middle part: `top_fraction` / `bottom_fraction` of the final plot height stay free above / below.
    (final span = span / (1 - top - bottom).) Fractions that leave less than a fifth of the plot to the data are cut back."""
    top = min(max(float(top_fraction), 0.0), 0.8)
    bottom = min(max(float(bottom_fraction), 0.0), 0.8)
    if top + bottom > 0.8:
        scale = 0.8 / (top + bottom)
        top, bottom = top * scale, bottom * scale
    span = float(high) - float(low)
    if span <= 0.0:
        return float(low), float(high)
    final = span / (1.0 - top - bottom)
    return float(low) - final * bottom, float(high) + final * top


def label_headroom_px(settings: PlanBarSettings, text_height: float) -> tuple[float, float]:
    """(above, below): pixels the Data text mode needs beyond the traces' extremes, so a label placed far above / below stays inside the plot. (0, 0) in the Bar mode."""
    if settings.text_align != ALIGN_DATA:
        return 0.0, 0.0
    th = float(text_height)
    pad = 2.0 * BAR_TEXT_PADDING_PX
    stop = min(max(int(settings.text_stop), STOP_MIN), STOP_MAX)
    return {
        5: (FAR_GAP * th + th + pad, 0.0),
        4: (NEAR_GAP * th + th + pad, 0.0),
        3: (0.0, 0.0),
        2: (0.0, NEAR_GAP * th + th + pad),
        1: (0.0, FAR_GAP * th + th + pad),
    }[stop]
