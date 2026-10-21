"""What the Image panel needs to put a frame on screen, worked out on the render thread (2026-10-14).

The frame itself (float32, the measured values) is never changed here; this only prepares how it is
*shown* and one number the panel keeps (the frame's value range). Doing it on the render thread takes
~12 ms per frame (3-4 ms of it two passes over the frame for min / max) off the GUI thread, which is the
busy one while Play runs.

Two results, both from a single look at the frame:

* ``data_range``: the exact (min, max) of the frame's finite pixels, for every frame. The panel used to
  copy the finite pixels (`image[np.isfinite(image)]`) on the GUI thread just to take min and max.
* ``picture``: the (h, w, 4) uint8 RGBA picture of the frame, only for a frame that has NaN pixels (rotation
  corners, anything the geometry left without a value; they come out transparent so the "no data"
  checker shows through). It is what pyqtgraph's `ImageItem` would draw with its automatic levels, built the
  same way so the display does not change:

  - brightness range ("levels") from the same coarse sample (about 65 536 pixels) pyqtgraph uses, not from
    every pixel; ``levels`` is returned so the item can carry them;
  - ``(value - low) * scale`` in float32 (OpenCV does the passes), clipped to 0..255 and *truncated* to 8 bit. ``scale`` is 256/range
    for a frame with NaN pixels (pyqtgraph sends that case through a 256-entry lookup table) and 255/range
    without, a quirk of pyqtgraph that is copied on purpose so the picture is the one on screen today.

  With a **fixed Display range** (View tab, `display_range_controls.py`) the levels are the given ones and the
  scale is 255/range for every frame, with or without NaN pixels, so equal values get equal grey in every frame
  (the automatic NaN path's 256/range is a pyqtgraph quirk copied only to keep the automatic picture identical).
  `FrameDisplay.auto_levels` always carries the frame's own automatic range for the readout and the fit button.

  A frame without NaN pixels gets no picture: pyqtgraph has a direct 8-bit path for it that costs ~3 ms,
  cheaper than doing it here (6 ms) and then handing it over.

Checked against pyqtgraph 0.14 (the pinned version): byte-identical (same levels, alpha and grey values)
on 1.2 MP frames and in the tests. That needed pyqtgraph's own numeric types (float32 levels, so a float32
range and scale): with Python doubles a few pixels per million came out one grey level away.
`tests/integration/test_lspri_display_picture.py` fails if a pyqtgraph upgrade changes any of it. A frame this module cannot reproduce exactly (infinite pixels, non-finite levels, not a
2-D float image) gets no picture and the panel falls back to letting pyqtgraph do it.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# `pyqtgraph.ImageItem.setImage(autoLevels=True)` looks at (about) this many pixels to find the levels.
LEVEL_SAMPLES = 65536
_FLT_MAX = float(np.finfo(np.float32).max)
MAX_FAST_GREY_DIFFERENCE = 1
"""The most a grey value of the `fast` picture may differ from the exact one (rounded instead of truncated)."""


@dataclass(frozen=True)
class FrameDisplay:
    data_range: tuple[float, float] | None
    """Exact (min, max) of the finite pixels; None when the frame has none."""
    levels: np.ndarray | None = None
    """The brightness range pyqtgraph's automatic levels would choose, as the array pyqtgraph itself would
    hold (float32; ints 0, 255 when the frame has no range). Set whenever ``picture`` is."""
    picture: np.ndarray | None = None
    """(h, w, 4) uint8 RGBA, C-contiguous; None = let pyqtgraph render the frame itself."""
    auto_levels: tuple[float, float] | None = None
    """The brightness range pyqtgraph's *automatic* levels give this frame (the "Display range" readout and its
    "from this frame" button), whether or not a fixed range is in use; None when it has no finite range."""


def prepare_frame_display(
    image: np.ndarray, fixed_levels: tuple[float, float] | None = None, *, fast: bool = False
) -> FrameDisplay | None:
    """The `FrameDisplay` of one frame, or None when it is not a plain 2-D float32 image
    (the panel then does everything itself, as before).

    `fast` (display while the slideshow plays, nothing else; 2026-10-21): build the grey picture in one saturating
    OpenCV pass that *rounds* to the nearest grey level instead of truncating, so it costs 3 native calls instead of
    7. Reason: each native call hands the GIL back and waits for it again, up to one switch interval (5 ms) while
    the GUI thread is busy, so during Play the picture cost ~40 ms instead of ~9. Grey values differ from the exact
    picture by at most one level, on about 0.002 % of the pixels (`MAX_FAST_GREY_DIFFERENCE`, pinned by a test); the
    value range, levels and alpha are identical. Play re-renders the frame it stopped on with `fast=False`.

    `fixed_levels` = (low, high): the maintainer's fixed display range, the same black and white point for every
    frame. The picture (frames with NaN pixels) is then built with it; without it, with pyqtgraph's automatic ones.
    A fixed range scales by 255/range, like pyqtgraph does for any frame without NaN pixels, so frames with and
    without NaN pixels get the same grey for the same value (the automatic NaN path's 256/range is copied only
    for the automatic levels, to keep that picture identical to what pyqtgraph draws)."""
    image = np.asarray(image)
    if image.ndim != 2 or image.dtype != np.float32 or image.size == 0:
        return None
    image = np.ascontiguousarray(image)
    automatic = _automatic_levels(image)
    auto_levels = None if automatic is None else (float(automatic[0]), float(automatic[1]))

    has_value = cv2.compare(image, image, cv2.CMP_EQ)  # 255 where the pixel has a value, 0 where it is NaN
    count = cv2.countNonZero(has_value)
    if count == 0:
        return FrameDisplay(data_range=None, auto_levels=auto_levels)
    low, high, _, _ = cv2.minMaxLoc(image, mask=has_value)
    if not (np.isfinite(low) and np.isfinite(high)):
        # Infinite pixels: min / max over the finite ones only, and no picture (pyqtgraph's own handling of
        # infinities is not something to copy).
        finite = image[np.isfinite(image)]
        data_range = (float(finite.min()), float(finite.max())) if finite.size else None
        return FrameDisplay(data_range=data_range, auto_levels=auto_levels)
    data_range = (float(low), float(high))
    if count == image.size:
        return FrameDisplay(data_range=data_range, auto_levels=auto_levels)  # no NaN: pyqtgraph's direct 8-bit path is cheaper

    if fixed_levels is not None:
        levels = np.asarray(fixed_levels, dtype=np.float32)  # float32, like the automatic levels: same arithmetic
        if levels.shape != (2,) or not np.all(np.isfinite(levels)) or not levels[1] > levels[0]:
            return FrameDisplay(data_range=data_range, auto_levels=auto_levels)
        numerator = 255.0
    else:
        levels = automatic
        numerator = 256.0
    if levels is None:
        return FrameDisplay(data_range=data_range, auto_levels=auto_levels)
    # The same numeric types pyqtgraph uses (float32 levels, so a float32 range and scale): in Python doubles
    # a few pixels per million land one grey level away.
    minimum, maximum = levels
    span = maximum - minimum
    span = 1 if span == 0 else span
    if fast:
        # One pass: grey = saturate_u8(value * scale + (-minimum * scale - 0.5)). The -0.5 turns OpenCV's
        # round-to-nearest into (almost always) the exact path's truncation. NaN pixels saturate to some value
        # (not guaranteed to be 0), so the mask below still zeroes them.
        scale = float(numerator / span)
        grey = cv2.addWeighted(image, scale, image, 0.0, -float(minimum) * scale - 0.5, dtype=cv2.CV_8U)
    else:
        work = cv2.subtract(image, float(minimum))
        cv2.multiply(work, float(numerator / span), dst=work)
        cv2.min(work, 255.0, dst=work)
        cv2.max(work, 0.0, dst=work)
        with np.errstate(invalid="ignore"):  # NaN pixels cast to something harmless; zeroed just below
            grey = work.astype(np.uint8)  # truncates, like pyqtgraph
    cv2.bitwise_and(grey, has_value, dst=grey)  # NaN pixels: black and (below) transparent, like pyqtgraph's
    picture = cv2.merge([grey, grey, grey, has_value])
    return FrameDisplay(data_range=data_range, levels=levels, picture=picture, auto_levels=auto_levels)


def _automatic_levels(image: np.ndarray) -> np.ndarray | None:
    """pyqtgraph's automatic levels: min / max of a strided sample of at most `LEVEL_SAMPLES` pixels
    (`ImageItem.quickMinMax`), or (0, 255) when the sample has no range. None if they are not finite."""
    sample = image
    while sample.shape[0] * sample.shape[1] > LEVEL_SAMPLES:
        rows, columns = sample.shape
        sample = sample[::2, :] if rows > columns else sample[:, ::2]
    sample = np.ascontiguousarray(sample)
    has_value = cv2.compare(sample, sample, cv2.CMP_EQ)
    if cv2.countNonZero(has_value) == 0:
        return np.asarray((0, 255))
    low, high, _, _ = cv2.minMaxLoc(sample, mask=has_value)
    low, high = np.float32(low), np.float32(high)  # exact: the sample is float32
    if low == high:
        return np.asarray((0, 255))
    if not (np.isfinite(low) and np.isfinite(high)):
        return None
    return np.asarray((low, high))
