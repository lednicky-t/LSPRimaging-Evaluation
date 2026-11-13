"""How the Sensorgram plot is drawn: the display options of the View tab (2026-11-13). No Qt, no numbers of the analysis.

Everything here changes how already-computed lines are drawn, never a value: the shaded SD / SEM band, the anchor squares and baseline curve, whether the dots and the lines are
drawn, the size of the dots, the width of the lines, how the points are joined (straight, or a smooth spline) and the legend (when it is shown, its text size, the space between
its rows, whether a group's label carries its number of ROIs). The controls (`view_controls.py`) hold the values and remember them (`restore_ui_state`); the plot
(`SensorgramPlot.set_view`) draws with them.

The defaults are what the plot always did, so a session that never touched the View tab looks as before: the band and the anchors shown, dots (4 px, on up to
`plot.DOTS_MAX_LINES` lines) and lines (1 px) drawn, joined straight, the legend automatic with 9 pt text and its rows as close as they were, the counts in the labels.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

LEGEND_AUTO, LEGEND_SHOW, LEGEND_HIDE = "auto", "show", "hide"
LEGEND_MODES = (LEGEND_AUTO, LEGEND_SHOW, LEGEND_HIDE)

LINE_STRAIGHT, LINE_SPLINE = "straight", "spline"  # how the points are joined: a straight segment between two points, or a smooth curve through them (`curve_smoothing.py`)
LINE_STYLES = (LINE_STRAIGHT, LINE_SPLINE)

DEFAULT_POINT_SIZE = 4  # px
POINT_SIZE_RANGE = (1, 14)  # no dots at all is the Points toggle, not a size
DEFAULT_LINE_WIDTH = 1.0  # px. Qt paints only 1 px lines fast: 160 lines x 400 cubes took 140 ms at 1.5 px and 30 ms at 1 px (docs/spectra_sensorgram_draw_cost_2026-11-02.md)
LINE_WIDTH_RANGE = (0.5, 4.0)
LINE_WIDTH_STEP = 0.5
DEFAULT_LEGEND_TEXT_PT = 9  # pyqtgraph's own default for a legend label
LEGEND_TEXT_RANGE = (6, 24)
DEFAULT_LEGEND_SPACING_PX = 0  # added between the legend's rows: 0 is the plot's look so far, a negative value is tighter. The step table's rows follow the legend's.
LEGEND_SPACING_RANGE = (-6, 24)


@dataclass(frozen=True)
class SensorgramViewSettings:
    show_band: bool = True  # the shaded SD / SEM band around an average
    show_anchors: bool = True  # the anchors and the baseline made from them: the hollow squares of the Anchor points method and the dashed baseline curve
    show_points: bool = True  # the dots on the measured points (drawn only up to `plot.DOTS_MAX_LINES` lines)
    show_lines: bool = True  # the lines that join them; hidden, the line items stay (invisible) so the view still fits the data, and the legend keeps its colour marks
    point_size: int = DEFAULT_POINT_SIZE
    line_width: float = DEFAULT_LINE_WIDTH
    line_style: str = LINE_STRAIGHT
    legend_mode: str = LEGEND_AUTO  # auto: as before (named up to 24 lines, kept while it takes at most half the plot); show: always; hide: never drawn
    legend_text_pt: int = DEFAULT_LEGEND_TEXT_PT
    legend_spacing_px: int = DEFAULT_LEGEND_SPACING_PX
    legend_counts: bool = True  # "Group A (n=12)" (True) or "Group A"
    # While the step table is on the legend sits just inside the plan bar (so does the table: its rows line up with the legend's). True: it ignores the bar and what hangs from
    # it and goes to the very edge of the plot, over the bar; the table's rows follow it, and the room made for the bar and the rows is the larger of the two, not their sum.
    legend_over_bar: bool = False

    def sanitized(self) -> SensorgramViewSettings:
        """The same settings with every value inside its range (a remembered value from another version, a typed number): the control and the plot never disagree."""
        return replace(
            self,
            show_band=bool(self.show_band), show_anchors=bool(self.show_anchors), legend_counts=bool(self.legend_counts), legend_over_bar=bool(self.legend_over_bar),
            show_points=bool(self.show_points), show_lines=bool(self.show_lines),
            point_size=_clamp(int(self.point_size), *POINT_SIZE_RANGE),
            line_width=round(_clamp(float(self.line_width), *LINE_WIDTH_RANGE) / LINE_WIDTH_STEP) * LINE_WIDTH_STEP,
            line_style=self.line_style if self.line_style in LINE_STYLES else LINE_STRAIGHT,
            legend_mode=self.legend_mode if self.legend_mode in LEGEND_MODES else LEGEND_AUTO,
            legend_text_pt=_clamp(int(self.legend_text_pt), *LEGEND_TEXT_RANGE),
            legend_spacing_px=_clamp(int(self.legend_spacing_px), *LEGEND_SPACING_RANGE),
        )


def _clamp(value, low, high):
    return min(max(value, low), high)


def legend_text(label: str, short_label: str, show_counts: bool) -> str:
    """The legend's text of one line: its label, or `short_label` (the label without the number of ROIs) when the counts are left out. A line without a short label
    (one ROI: "ROI 5") always reads as its label."""
    return label if show_counts or not short_label else short_label
