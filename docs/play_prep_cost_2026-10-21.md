# Play at 12-15 frames/s instead of 20-25 (2026-10-21)

**Report:** slideshow Play slowed to 12-15 fps. Session log `logs/lspri-20261021-165814-34660.jsonl`
(dataset `04_Bulk_sensitivity_..._LED_V2`, 160 ROIs, speed 10 = target 29.6 fps).

## What the log said

`playback.window` lines at speed 10: `cycle_ms` ~ `lag_ms` ~ 60-65 ms, `load_ms` < 1 (read-ahead works),
`gui_busy_pct` 30-40 %. So the **render thread** set the pace, and inside it `prep_ms` (rotate/flip/crop + background
flatten, `ImageRenderer._render_inner`) was **46-80 ms** (median ~52). The older runs (`testing7`/`9.jsonl`) had
`prep_ms` ~2 ms: background flattening was off there. Today: `c_background=True`, sigma 40, bin 4, "exclude ROIs" on.
I could not see the settings of the 20-25 fps run; the log flags only exist from this session on.

## Where the 47-53 ms went (standalone, real frame + real session, `fast_rotation=True`)

| stage | ms |
|---|---|
| rotate + crop (bilinear, Play) | 6-10 |
| ROI exclusion mask (`roi_exclusion_mask`: Python loop over 160 ROIs) | **~11.5** (in place, p25) |
| rest of the exclusion mask (copy, isfinite, ignore mask) | ~5 |
| valid-mask weights (`_weights_part`: downsample + blur) | **~5.6** (in place, p25) |
| background estimate proper (masked image, bin, blur, upsample, median) | ~8 |
| divide / scale / clip (`apply_background`) | ~8 |

The two bold stages depend only on the ROIs / the valid mask, not on the frame's pixels, yet were redone every frame.

## What was changed (`panels/image/render.py`, `ImageRenderer._frame_caches`)

The renderer keeps the ROI exclusion mask (key: image shape + each ROI's centre and diameter) and a
`BackgroundWeightsCache`, and passes them to `apply_preprocessing` via its existing `roi_exclusion=` / `weights_cache=`
arguments (the analysis runs already use them; documented and tested as giving the same result).

**Measured** (interleaved before/after, renderer's own `prep_ms`, two runs): 46.6 -> 30.0 and 55.0 -> 36.0 ms
(about -17 ms per frame). Worst case (a different ignore mask on every frame, so the weights never hit): -14 ms.
**Image identical** to the uncached pipeline, NaN positions included (checked on the real frame at Play and at rest, and
pinned by `TestRendererFlattenCaches` in `tests/unit/test_lspri_rewrite_image_refresh_speedups.py`).

Not driven in the real app (GUI rule): the maintainer should confirm the fps in a Play.

## Left, not done (each needs a decision)

* ~30-36 ms of `prep_ms` remains. The biggest parts are the divide (8 ms), the estimate (8 ms) and the rotation (7 ms).
  `apply_background` could clip in place (saves ~1-2 ms, identical values) - small, left out.
* Two render threads would roughly double the ceiling (numpy/cv2 release the GIL) but make "newest request wins" and
  result ordering harder; the testing7 analysis found GIL contention already inflates reads. Architecture decision.
* A cheaper background during Play only (larger bin, re-rendered exact at rest, like the bilinear rotation) changes the
  displayed values while playing; science-adjacent, so it is the maintainer's call.
* `resolve_external_mask` runs when "exclude mask" is on even if `detection.ignore_marked_pixels` is False (the mask is
  then never read); ~0.7 ms warm, harmless.
