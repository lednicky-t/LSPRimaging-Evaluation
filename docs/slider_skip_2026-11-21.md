# The sliders obey the slideshow's Skip settings (2026-11-21)

Maintainer request: the slideshow can skip some frames (ignored, dark); apply that to scrolling the Cube and wavelength sliders by
hand too. **Same two settings** (`SlideshowSettings.skip_ignored`, default on, and `skip_dark`, default off; the eye-off and moon
buttons of the slideshow's Skip group); no new setting. Switch both off and the sliders visit every frame again.

## What is filtered, what is not

Filtered: the slider's own gestures: a press or drag on the track, the wheel, the arrow / Page Up / Page Down keys, Home / End.
Not filtered: every `setValue` from code. A click on a mark of the ROI or Mask row (`index_requested`), "Go to reference" and the
sync after a selection change land exactly where they are told; the user asked for that frame by name. Previous / Next and the wheel
past the end of the wavelength slider already went through the slideshow's own stepping.

## The rule (`panels/image/slider_skip.py`, `snap_index`, pure)

- A frame that is not skipped is taken as asked.
- **A step** (wheel, arrows, pages) goes on in its direction to the first allowed frame; with none that way it is refused, like a step at
  any slider's end. Home / End land on the nearest allowed frame between the end and the handle.
- **A press or drag** lands on the nearest allowed frame (a tie goes the way the handle is travelling). It looks only at where the
  pointer is, never at where the handle was: a rule that depended on the last position jittered back and forth when dragging across a
  block of skipped frames (worked out on paper before building).
- If **every** position is skipped (the dark frame is shown with Skip dark on, or a cube that is wholly ignored) nothing is filtered: the
  user is standing there on purpose, and a slider that would not move at all is worse.
- The wheel turned on past the last **allowed** frame goes into the next / previous cube (`edge_step_requested`), not only past the last
  tick. Without this, with Skip dark on, the first real wavelength would no longer be an end (0 nm lies below it, skipped) and the wheel
  could not leave the cube.

What counts as skipped: the Cube slider asks `SlideshowController.is_skipped(cube, wavelength shown)`, the wavelength slider
`is_skipped(cube shown, wavelength)` (the controller's own question, now public). A cube that lacks the wavelength shown is judged by
the same question, an approximation for datasets whose cubes differ in wavelengths.

## How it is wired

`DataAxisSlider.set_index_filter(filter)` (`filter(requested, current, first, last, absolute) -> index`; the widget still knows nothing
about cubes). Mouse press / move call it before their `setValue`; the wheel, keys and Home / End reach it through Qt's
`actionTriggered` (emitted with the new position already asked for and the value not yet changed, so the slot replaces the position
with `setSliderPosition`; checked in Qt's `triggerAction`). Built in `navigation_build.py` from `cube_index_filter` /
`wavelength_index_filter`. The panel (`panel.py`) is untouched (its size budget has room for 1 method only).

## Tests

`tests/unit/test_lspri_slider_skip.py` (22: the rule, drag path, ties, ends, everything skipped, the two filters with fakes),
`tests/integration/test_lspri_slider_skip_panel.py` (12: a real Image panel, hand-made wheel / key / mouse events on the real sliders,
frames ignored through the real `FrameRolesModule`, marks, both settings off, the dark frame). Existing wheel-edge, slider, slider-ribbon,
slideshow and panel-budget tests unchanged and green. **Unseen by eye.**

## Not done / to look at

- The dark frame is set by the session restore in the app (`FrameRolesModule` does not pick 0 nm itself on a load); the tests set it by hand.
- A drag that starts on a skipped tick snaps at once on the press.
- Skipped ticks are still drawn on the sliders (ignored ones are already marked; the dark one has its own tick). Nothing greys them out.
