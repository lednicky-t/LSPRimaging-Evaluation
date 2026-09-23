"""Pure per-cell compute (sketch §10: "ports analysis_tasks.py largely
as-is").

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariants: never pool pixels across ROIs before computing
sample/reference ratios; always average already-fitted per-ROI values,
never average raw spectra and fit once.

**"Largely as-is" did not survive contact with the real code (2026-09-22,
same family as the `dataset/io.py` §10 correction)**: the old app's
closest equivalent, `gui/analysis_tasks.py`'s `_sensorgram_metric_task`
(~400 lines), is deeply entangled with *bulk multi-ROI* concerns this
file's job (per-cell compute) doesn't own - GC toggling, a
`ThreadPoolExecutor` cube-payload prefetch stage, a `roi_mask_cache`
shared across every selected ROI, worker-count calibration, and a
cancellation story spread across several paragraphs of comment explaining
edge cases. None of that is "one cell's compute," it's a *batch dispatch
and caching layer around* per-cell compute. `compute_cell` below is a
genuine, correct rewrite of the per-cell arithmetic that function performs
internally - not a port of the function itself, and deliberately without
its batch-level optimizations (shared ROI-mask caching across a whole
multi-ROI run, chunk-aware zarr reads, prefetch concurrency). Those
optimizations belong in a *different* layer - batching/caching around
repeated `compute_cell` calls, in `worker.py`/`engine.py` or a future
optimization pass - once there's a real dataset to measure against
(AGENTS.md's Performance Work rules: verify with real data, don't guess).

**Stores raw reduced (sample, reference) pairs, not a formula-applied
value** - a real correction to this file's own stub, which returned a bare
`float`. Sketch §6 is explicit: "Formula, Fit method, Metric choice - never
touch the stored cells at all; computed live from whatever's already in
the store." Baking `formula_value` (`processing/analysis.py` on
`develop`/`main`) into what gets stored here would mean a Formula change
(e.g. absorbance -> ratio) silently requiring a full recompute purely
because a display-math choice changed the SHAPE of what's on disk - not
what the sketch designed. `compute_cell` stops at the reduced
sample/reference pair; formula math is a query-time concern for whatever
reads `get_spectrum`/`get_metric` later (not built yet - `engine.py`).

**Pixel access is the expensive part, but the actual formula
`sample_pixels = processed[sample_mask]` etc. below is not "just NumPy
indexing" - `apply_preprocessing`/`rasterize_sample`/`rasterize_reference`
do the real work; this file composes already-pure functions, no new pixel
math.**

**Assumption 1 (mask coordinate space) resolved 2026-09-23 - and it was
half wrong; see `_mask_for_compute` below.** Traced the old app's real
analysis path (`gui/mask_controller.py`'s `external_mask_for_record`, and
`gui/analysis_tasks.py`/`gui/analysis_worker_mixin.py`'s callers):

- The *authored* mask genuinely is in **raw image space** - it is read
  from a file sized to `load_image_shape(record.path)`, so the original
  "needs this call's own crop/rotate/flip" half of the assumption was
  right.
- But the **chromatic affine is defined in processed space**, not raw
  (the same space `rasterize_sample`/`rasterize_reference` rasterize ROIs
  into, off `processed.shape`). So a mask authored at a *different* frame
  cannot be warped while still in raw space, which is what the original
  `resolve_mask`/`external_mask_processed=False` wiring would have done.
  It has to be transformed into processed space first and warped there -
  exactly the order the old app uses
  (`apply_spatial_mask(...)` → `warp_boolean_mask_affine(...)` →
  `external_mask_processed=True`).

Getting that order wrong wouldn't crash - it would silently misalign the
ignore mask against the image it is meant to exclude from, by whatever
the crop/rotation happens to be. `_mask_for_compute` now does it in the
old app's order unconditionally, and the mask *persisted into provenance*
stays the as-authored one (see `compute_cell`).

**Background-exclusion port gap closed 2026-09-23.** This file used to call
`apply_preprocessing` with neither `rois` nor `mask_settings`, which left
*both* of `BackgroundSettings`' exclusion toggles inert on the analysis path
- `flatten_background_exclude_area_rois` (which defaults **on**) and
`flatten_background_exclude_mask`. The old app's equivalent bulk path wires
both explicitly (`gui/analysis_tasks.py`), so this was a port gap rather than
inherited behaviour, and the mask half compounded: `preprocess.py` zeroes
masked pixels into the processed image, and inside
`background/estimate.py`'s `_combined_exclusion_mask` the ignore mask only
reaches the exclusion set *through* `mask_settings` - so those zeros were
left to drag the local background average down, the exact failure the fix
above that line was written to prevent. Both are now passed; the ROI set is
**all** ROIs, never a selected subset (maintainer's instruction, same
reasoning as `REFERENCE_EXCLUSION_MODES`). Measured impact, before it was
fixed: ~0.13% median shift in the sample/reference ratio, but only 2 of 160
ROIs drifting by more than one shot-noise floor across a 300-cube run - see
the 2026-09-23 build log entry for the full numbers.

**Assumption 2 (rasterization) unchanged and still deliberate**:
`roi/rasterize.py`'s binary `rasterize_sample`/`rasterize_reference` are
used here, not `rasterize_fractional` (§6a). `analysis/reduction.py`'s
`weighted_*` functions that would consume fractional weights now exist
(built 2026-09-22), so this is no longer blocked - but wiring them is a
real change to computed values behind its own toggle, not something this
file switches to on its own.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import apply_preprocessing, resolve_external_mask
from ..roi.model import AreaRoi, AreaRoiDetectionSettings
from ..roi.rasterize import rasterize_reference, rasterize_sample
from .provenance import (
    DEFAULT_REFERENCE_EXCLUSION_MODE,
    FrameNamingScheme,
    ProvenanceRecord,
    SettingsSnapshot,
    background_exclusion_digest,
    compute_fingerprint,
    mask_scope_tag,
    persist_chromatic_snapshot,
    persist_mask_snapshot,
    roi_geometry_fingerprint_fields,
    sample_exclusion_digest,
)
from .reduction import reduce_sample_and_reference

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WavelengthComputeInput:
    """Everything `compute_cell` needs for one wavelength within a cell's
    cube, already resolved from the real modules by the caller (engine.py/
    worker.py) - this file stays Qt/module-free (see module docstring)."""

    wavelength_nm: float
    raw_image: np.ndarray
    geometry_settings: GeometrySettings
    background_settings: BackgroundSettings
    chromatic_affine: np.ndarray
    resolved_mask: np.ndarray | None
    """The mask that applies at this wavelength **exactly as authored** -
    raw image space, not warped - i.e. `MaskModule.resolve_mask_source`'s
    mask handed straight through.

    Deliberately *not* pre-warped by the caller (corrected 2026-09-23, see
    module docstring): the warp has to happen in processed space, which
    only `compute_cell` is in a position to do, and provenance needs the
    as-authored mask anyway - a per-wavelength warped variant persisted
    under the authored frame's filename would churn version numbers for
    what is really one mask."""
    mask_authored_frame: tuple[int, float] | None
    """The `(cube_index, wavelength_nm)` the mask was actually authored at
    (for provenance file naming) - `None` if `resolved_mask` is `None`."""
    mask_scope: str | None
    """`MaskModule`'s own scope vocabulary, `"persistent"` or
    `"individual"` - `None` if `resolved_mask` is `None`. Translated to the
    `persi`/`indiv` filename tag by `provenance.mask_scope_tag`, not by the
    caller."""
    mask_warp_affine: np.ndarray | None = None
    """The 2x3 affine mapping the authored frame's **processed-space**
    geometry into this wavelength's, i.e.
    `ChromaticModule.affine_between(mask_authored_frame, (cube, wavelength))`.

    `None` (or identity) means no warp is needed - the overwhelmingly
    common case, since a mask authored at this exact frame, or any mask at
    all when no chromatic model has been fitted, needs no re-registration.
    Passed as a plain matrix rather than this file reaching into
    `ChromaticModule`, the same one-directional convention
    `roi/rasterize.py` already follows."""


@dataclass(frozen=True)
class CellResult:
    """One (ROI, cube) cell's computed result - the raw reduced
    sample/reference pair per wavelength (see module docstring for why not
    a formula-applied value), plus its provenance."""

    wavelengths_nm: tuple[float, ...]
    sample_values: tuple[float, ...]
    reference_values: tuple[float, ...]
    provenance: ProvenanceRecord


def _mask_for_compute(wl_input: WavelengthComputeInput) -> np.ndarray | None:
    """This cell's ignore mask in processed space - a thin adapter over
    `image_tools.preprocess.resolve_external_mask`, which owns the actual
    transform order and the reasoning behind it.

    Shared with the Image panel deliberately: the displayed image and the
    analyzed image resolve their mask through the same function, so they
    cannot silently diverge. Applied unconditionally (always
    `external_mask_processed=True`), matching the old app's analysis path -
    masking in raw space first is only *nearly* equivalent even with no
    warp, since the image transform interpolates (`order=1`) and would
    bleed zeroed pixels into their neighbours at mask edges.
    """
    return resolve_external_mask(
        wl_input.resolved_mask, wl_input.geometry_settings, wl_input.mask_warp_affine
    )


def _sample_exclusion_union(
    all_rois: tuple[AreaRoi, ...],
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Union of every ROI's sample-aperture mask at this wavelength's
    geometry - the thing a reference ring subtracts in
    `"exclude_all_sample_rois"` mode.

    **Includes the ROI's own sample aperture, not just its neighbours'** -
    a deliberate, small difference from the old app, which skipped the
    union entirely when only one ROI was selected and otherwise included
    self. That made single-ROI and multi-ROI runs behave differently at
    the same geometry (a ROI whose sample circle pokes inside its own
    reference ring got those pixels counted with one ROI selected and
    dropped with two). Excluding every sample aperture unconditionally is
    both simpler and consistent: a reference ring never counts sample
    pixels, full stop.
    """
    union = np.zeros(image_shape, dtype=bool)
    for other in all_rois:
        union |= rasterize_sample(other, image_shape, affine_matrix)
    return union


def compute_cell(
    roi: AreaRoi,
    cube_index: int,
    wavelength_inputs: dict[float, WavelengthComputeInput],
    *,
    reduction_method: str,
    trimmed_mean_fraction: float,
    default_reference_inner_radius_px: float,
    default_reference_outer_radius_px: float,
    masks_dir: Path,
    chromatic_dir: Path,
    settings_dir: Path,
    naming: FrameNamingScheme,
    all_rois: tuple[AreaRoi, ...] = (),
    detection_settings: AreaRoiDetectionSettings | None = None,
    reference_exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE,
    sample_exclusion_cache: dict[tuple[int, float], np.ndarray] | None = None,
    cancel_event=None,
) -> CellResult | None:
    """Compute one (ROI, cube) cell's reduced spectrum (raw sample/
    reference pair per wavelength) plus the provenance record describing
    what produced it.

    **Persists this cell's mask/chromatic/settings snapshots as a real
    side effect** - deliberately here, not in `compute_fingerprint` (see
    that function's own docstring): this is the "written lazily, only
    when actually analyzed" moment the provenance design doc calls for -
    a mask/chromatic file only gets created once a cell genuinely needing
    it is computed, never during planning/preview.

    `reference_exclusion_mode` (see `provenance.REFERENCE_EXCLUSION_MODES`)
    selects whether this ROI's reference ring drops pixels belonging to
    sample apertures. In `"exclude_all_sample_rois"` mode the union is
    built from **`all_rois`, every ROI - never a selected subset**, so a
    cell's value never depends on what else happened to be selected when
    it was computed (the old app's `all_selected_sample_mask` did depend
    on that; see `provenance.REFERENCE_EXCLUSION_MODES`' docstring).

    `detection_settings` (`RoiToolbox.detection_settings()`) is passed
    straight through to `apply_preprocessing` as its `mask_settings`, which
    is what lets `flatten_background_exclude_mask` actually exclude the
    ignore mask from the background estimate (see the module docstring's
    port-gap note). Only its `ignore_marked_pixels` field reaches that far
    today, but the whole object is passed rather than that one flag, so this
    stays correct if `ignored_pixel_mask` ever reads another field. `None`
    restores the old, silently-inert behaviour - kept only so a test can
    call this function without one.

    `sample_exclusion_cache` memoizes that union per (cube, wavelength) -
    it is identical for every cell at a given frame, and rebuilding it per
    cell would be O(ROIs x cells) rasterizations instead of O(ROIs). Passed
    in rather than held internally so its lifetime is the caller's to
    decide (one `run_analysis` call), following the same explicit
    cache-as-parameter convention the old app's
    `_scoped_formula_spectrum_task` already used for `roi_mask_cache`. No
    lock: only one `AnalysisWorker` task runs at a time and it processes
    cells sequentially, so unlike the old app's `ThreadPoolExecutor` there
    is no concurrent access to guard.

    `cancel_event` (a `threading.Event`, typed loosely here to avoid
    importing `threading` into type signatures this file doesn't otherwise
    need) is checked **between wavelengths, never mid-wavelength** - if set
    before the first wavelength completes, returns `None` (nothing to
    store); if set partway through, returns whatever wavelengths finished
    first - always a valid partial spectrum, never a half-computed one.

    Stage timing (raster/reduce split - preprocessing is folded into
    "preprocess" since `apply_preprocessing` doesn't expose finer splits
    without its own `log_stage_timing` wiring) is aggregated across every
    wavelength and logged **once per cell**, not per wavelength (AGENTS.md
    Performance Work rule - logging in the inner loop becomes the
    bottleneck otherwise).
    """
    wavelengths = sorted(wavelength_inputs.keys())
    sample_values: dict[float, float] = {}
    reference_values: dict[float, float] = {}
    snapshots: dict[float, SettingsSnapshot] = {}
    stage_seconds = {"preprocess": 0.0, "rasterize": 0.0, "reduce": 0.0}
    cell_started = time.perf_counter()

    for wavelength_nm in wavelengths:
        if cancel_event is not None and cancel_event.is_set():
            break
        wl_input = wavelength_inputs[wavelength_nm]

        t0 = time.perf_counter()
        processed = apply_preprocessing(
            wl_input.raw_image,
            wl_input.geometry_settings,
            wl_input.background_settings,
            # Both of these only ever feed the *background estimate*, and
            # `apply_preprocessing` gates each on its own
            # `BackgroundSettings` toggle - so passing them unconditionally
            # here is correct, not a behaviour change when the toggles are
            # off. See the module docstring's port-gap note.
            #
            # Deliberately **not** chromatically warped, matching both the
            # old app and `_roi_exclusion_mask`'s own shape: it grows each
            # ROI's radius by 35% before excluding it, a margin far wider
            # than the sub-pixel-to-few-pixel shift a chromatic affine
            # applies, so warping would cost a per-frame transform to move
            # a boundary that is already deliberately loose.
            rois=list(all_rois) or None,
            mask_settings=detection_settings,
            # Processed-space, chromatically warped - see _mask_for_compute
            # and the module docstring's assumption-1 note.
            external_mask=_mask_for_compute(wl_input),
            external_mask_processed=True,
        )
        stage_seconds["preprocess"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        image_shape = processed.shape[:2]
        sample_mask = rasterize_sample(roi, image_shape, wl_input.chromatic_affine)
        reference_mask = rasterize_reference(
            roi, image_shape, wl_input.chromatic_affine,
            default_inner_radius_px=default_reference_inner_radius_px,
            default_outer_radius_px=default_reference_outer_radius_px,
        )
        if reference_exclusion_mode == "exclude_all_sample_rois" and all_rois:
            cache_key = (int(cube_index), float(wavelength_nm))
            union = None if sample_exclusion_cache is None else sample_exclusion_cache.get(cache_key)
            if union is None:
                union = _sample_exclusion_union(all_rois, image_shape, wl_input.chromatic_affine)
                if sample_exclusion_cache is not None:
                    sample_exclusion_cache[cache_key] = union
            # Sample pixels only - reference rings overlapping each other are
            # counted normally (the maintainer's explicit framing of this
            # mode; see REFERENCE_EXCLUSION_MODES).
            reference_mask = reference_mask & ~union
        stage_seconds["rasterize"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        sample_pixels = processed[sample_mask]
        reference_pixels = processed[reference_mask]
        ref_yy, ref_xx = np.nonzero(reference_mask)
        sample_value, reference_value = reduce_sample_and_reference(
            sample_pixels, reference_pixels, reduction_method,
            trimmed_mean_fraction=trimmed_mean_fraction,
            reference_xx=ref_xx.astype(np.float64), reference_yy=ref_yy.astype(np.float64),
            sample_x=float(roi.center_x), sample_y=float(roi.center_y),
        )
        sample_values[wavelength_nm] = sample_value
        reference_values[wavelength_nm] = reference_value
        stage_seconds["reduce"] += time.perf_counter() - t0

        mask_ref = None
        if wl_input.resolved_mask is not None and wl_input.mask_authored_frame is not None and wl_input.mask_scope is not None:
            mask_cube, mask_wl = wl_input.mask_authored_frame
            # The **as-authored** mask, not `_mask_for_compute`'s
            # processed/warped one: this file is named after the frame the
            # mask was authored at, and every wavelength that inherits that
            # same mask must therefore persist byte-identical pixels, or the
            # content-dedup in `persist_mask_snapshot` would mint a new
            # version per wavelength for what is really one mask.
            mask_8bit = np.asarray(wl_input.resolved_mask, dtype=bool).astype(np.uint8) * 255
            mask_ref = persist_mask_snapshot(
                masks_dir, mask_8bit, cube_index=mask_cube, wavelength_nm=mask_wl,
                tag=mask_scope_tag(wl_input.mask_scope), naming=naming,
            )
        chromatic_ref = persist_chromatic_snapshot(
            chromatic_dir, wl_input.chromatic_affine, cube_index=cube_index,
            wavelength_nm=wavelength_nm, naming=naming,
        )
        snapshots[wavelength_nm] = SettingsSnapshot(
            geometry=asdict(wl_input.geometry_settings),
            mask=mask_ref,
            chromatic=chromatic_ref,
            background=asdict(wl_input.background_settings),
            reduction_method=reduction_method,
            reference_exclusion_mode=reference_exclusion_mode,
            # Only recorded when it actually affects the result - in "none"
            # mode other ROIs' geometry is genuinely not an input, so
            # including it would invalidate every cell on any ROI move for
            # no reason.
            sample_exclusion=(
                sample_exclusion_digest(all_rois)
                if reference_exclusion_mode == "exclude_all_sample_rois" and all_rois
                else None
            ),
            # Same idea, for the *other* place other ROIs became an input
            # above: with background ROI exclusion on, moving ROI X changes
            # the background under ROI Y. Self-gating (returns None unless
            # flattening and an exclusion toggle are both on), so no
            # condition is needed here - and it must stay identical to
            # engine.py's planning-side call, or every cell would look
            # stale against its own stored fingerprint.
            background_exclusion=background_exclusion_digest(
                all_rois, wl_input.background_settings, detection_settings
            ),
        )

    if not sample_values:
        return None

    logger.debug(
        "compute_cell roi=%s cube=%s: %d/%d wavelengths, total=%.3fs preprocess=%.3fs rasterize=%.3fs reduce=%.3fs",
        roi.area_roi_id, cube_index, len(sample_values), len(wavelengths),
        time.perf_counter() - cell_started, stage_seconds["preprocess"], stage_seconds["rasterize"], stage_seconds["reduce"],
    )

    computed_wavelengths = tuple(sorted(sample_values.keys()))
    provenance = compute_fingerprint(
        roi_geometry=roi_geometry_fingerprint_fields(roi),
        reduction_method=reduction_method,
        per_wavelength_settings=snapshots,
        settings_dir=settings_dir,
    )
    return CellResult(
        wavelengths_nm=computed_wavelengths,
        sample_values=tuple(sample_values[wl] for wl in computed_wavelengths),
        reference_values=tuple(reference_values[wl] for wl in computed_wavelengths),
        provenance=provenance,
    )
