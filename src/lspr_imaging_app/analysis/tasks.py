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

**Two flagged, unverified assumptions - not guessed at confidently**:
1. `MaskModule`'s resolved mask is passed as `apply_preprocessing`'s
   `external_mask` with `external_mask_processed=False` (i.e., assumed
   authored in *raw* image space, needing this call's own crop/rotate/flip
   step same as the raw image does) - not confirmed against the real
   mask-drawing GUI code, which would show which coordinate space a
   mask is actually painted in. Getting this wrong wouldn't crash
   anything, just silently misalign the mask against the image it's
   supposed to exclude from - flag for whoever wires a real panel to this.
2. `roi/rasterize.py`'s binary `rasterize_sample`/`rasterize_reference` are
   used here, not `rasterize_fractional` (§6a) - deliberate, not an
   oversight: `analysis/reduction.py`'s `weighted_*` functions that would
   actually *consume* fractional weights are still `NotImplementedError`,
   so there's nothing yet to plug a fractional mask into.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import apply_preprocessing
from ..roi.model import AreaRoi
from ..roi.rasterize import rasterize_reference, rasterize_sample
from .provenance import (
    FrameNamingScheme,
    ProvenanceRecord,
    SettingsSnapshot,
    compute_fingerprint,
    persist_chromatic_snapshot,
    persist_mask_snapshot,
    roi_geometry_fingerprint_fields,
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
    """`MaskModule.resolve_mask_source(...)`'s result, if any applies at
    this wavelength - already warped into this wavelength's own geometry
    if it was authored at a different frame (the caller's job, via
    `ChromaticModule.warp_mask_between`, per `MaskModule`'s own module
    boundary rule - this file never reaches into Chromatic itself
    either)."""
    mask_authored_frame: tuple[int, float] | None
    """The `(cube_index, wavelength_nm)` the mask was actually authored at
    (for provenance file naming) - `None` if `resolved_mask` is `None`."""
    mask_scope: str | None
    """`"persi"` or `"indiv"` (matching `MaskModule`'s own `"persistent"`/
    `"individual"` - see the provenance design doc) - `None` if
    `resolved_mask` is `None`."""


@dataclass(frozen=True)
class CellResult:
    """One (ROI, cube) cell's computed result - the raw reduced
    sample/reference pair per wavelength (see module docstring for why not
    a formula-applied value), plus its provenance."""

    wavelengths_nm: tuple[float, ...]
    sample_values: tuple[float, ...]
    reference_values: tuple[float, ...]
    provenance: ProvenanceRecord


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
            external_mask=wl_input.resolved_mask,
            external_mask_processed=False,  # flagged assumption - see module docstring
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
            mask_8bit = np.asarray(wl_input.resolved_mask, dtype=bool).astype(np.uint8) * 255
            mask_ref = persist_mask_snapshot(
                masks_dir, mask_8bit, cube_index=mask_cube, wavelength_nm=mask_wl,
                tag=wl_input.mask_scope, naming=naming,
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
