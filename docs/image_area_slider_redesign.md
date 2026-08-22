# Image Area: Cube/Wavelength Slider Redesign (2026-08)

What the Cube and λ sliders in the Image area's bottom toolbar are built from now,
and why, after the tick/axis-style redesign. Written so a future session doesn't
need to re-trace this from scratch.

## Widget: `DataAxisSlider` (`gui/widgets.py`)

A `QSlider` subclass, not a bare `QWidget`. Deliberately, so that every existing
integration point keeps working unchanged: `_configure_slider` (`main_window.py`),
`value()`/`setValue()`/`blockSignals()`, the wheel-scroll `installEventFilter`
handling in `image_interaction_controller.py`, and default keyboard arrow
stepping — none of that is touched. Only `paintEvent` (thin rail, ticks, no fill,
handle) and click/drag hit-testing (`mousePressEvent`/`mouseMoveEvent` — clicking
anywhere on the track jumps straight there, unlike stock QSlider's page-step
click) are overridden.

The widget is intentionally "dumb": it has no notion of wavelengths, cubes, or
time. `set_ticks(values, major_labels)` takes the full real-value array (one
minor tick per entry) plus a `{index: label_text}` dict for the labeled subset —
all deciding-what's-major logic lives in the caller (`main_window.py`), not here.
**Label-overlap avoidance also lives in `paintEvent`**, not in the major-index
picker: two majors can land pixel-close even when far apart in value, because
tick *position* is spaced by index, not by value (see below) — a sparse stretch
of the dataset compresses whatever majors fall in it. `paintEvent` measures each
label's rendered width and silently drops (not truncates) any label that would
overlap the previous one, keeping the tick mark but not its text. This
self-adjusts to actual widget width, so it holds up across panel resizes without
needing to recompute anything upstream.

## Tick/major-label computation (`main_window.py`)

- `_wavelength_slider_major_ticks()`: labels the dataset point nearest each
  ~100 nm boundary in the data's own range.
- `_cube_slider_major_ticks()`: raw index at a "nice" interval normally: in Time
  mode (Cube/Time toggle switched, real acquisition timing loaded) it instead
  reuses `AnalysisController._sensorgram_x_values()` (the same elapsed-seconds
  mapping the sensorgram plot's time axis already uses) and
  `_format_elapsed_seconds` (same M:SS/H:MM:SS formatter the spin box's Time-mode
  display already uses) — so the slider, the spin box, and the sensorgram plot
  can never disagree about a given cube's elapsed time. Called from
  `_configure_navigation_inputs()` (dataset load/clear) and from
  `_refresh_cube_time_display()` (Cube/Time toggle flips).

**Known, deliberate limitation:** tick *position* is spaced by array index for
both sliders, not by real value — same as before this redesign
(`_configure_slider` sets index-only min/max/step). A non-uniform wavelength grid
would make position drift from what the labels say. Not changed here; flagged to
the maintainer as a bigger follow-up if it ever matters for real data.

## Reference-image highlight

`set_reference_highlight(color | None)` replaces the old raw
`setStyleSheet("QSlider::handle:horizontal {...}")` hack in
`_update_reference_navigation_styles` — needed because a fully custom
`paintEvent` doesn't respond to `QSlider::handle` QSS selectors anymore.

## Wavelength jump field: `GuidedValueSpinBox` + `QCompleter`

`gui/widgets.py`. A `QCompleter` attached to `wavelength_spin.lineEdit()`
prefix-filters against the dataset's actual wavelength values as you type,
flagging (red border) prefixes that can't match anything. The suffix (now just
the title says "λ (nm)"; the box itself has no suffix) would otherwise defeat
prefix matching if one were ever reintroduced — `GuidedValueSpinBox` hides its
suffix while focused for exactly that reason, see its docstring.

## Layout (`gui/layout_builder.py`)

Cube and λ are two stacked full-width rows (`QVBoxLayout` of two `QHBoxLayout`s),
each `title -> slider -> control -> one icon button` (`reference_jump_button` on
the Cube row, `image_exclusion_button` on the λ row). Both titles share one style
helper, `_slider_axis_title_style()`, instead of the Cube toggle hand-copying the
λ label's font (that drift was the original size-mismatch bug this whole
redesign started from).
