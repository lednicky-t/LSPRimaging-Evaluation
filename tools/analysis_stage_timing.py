"""Phase-0 timing of the analysis path (docs/analysis_redesign_plan_2026-10-19.md): where does the time go?

Read-only with respect to the dataset and the session: everything the analysis persists goes to a temporary folder that is
deleted at the end. Builds the app's real modules headless (no window), restores the session into them, then times:

  1. reads        TIFF / zarr full plane, zarr scoped reads (cold = first read, warm = second read)
  2. frame stage  per sub-step of apply_preprocessing, plus the per-frame lookups (mask, chromatic affine)
  3. ROI stage    full-frame masks (today) vs patch-scoped masks (exist already), and are they identical?
  4. end to end   today's compute_cell (ROI-major), stage split from its own debug log line
  5. threads      read + preprocess throughput with 1..8 threads

    python tools/analysis_stage_timing.py --tiff "D:/data/run/images" --session "D:/data/run/images/sessions/2026-09-27_143549"
    python tools/analysis_stage_timing.py --tiff ... --zarr "D:/data/run/images/x.ome.zarr" --session ... --cubes 4 --json out.json

Run from a terminal, with nothing else heavy running (the numbers depend on it). "Cold" depends on the operating system's
file cache: a file read for the first time since boot is cold, one read a minute ago is not.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_SRC = Path(__file__).resolve().parents[1] / "src"
for extra in (APP_SRC, *(APP_SRC.parents[3] / "packages" / name / "src" for name in ("lspr_ui", "lspr_core", "lspr_io"))):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app import app_rewrite  # noqa: E402
from lspr_imaging_app.analysis.tasks import compute_cell  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools import preprocess as pp  # noqa: E402
from lspr_imaging_app.image_tools.background import estimate as bg_estimate  # noqa: E402
from lspr_imaging_app.analysis.reduction import reduce_sample_and_reference  # noqa: E402
from lspr_imaging_app.roi import rasterize as rz  # noqa: E402
from lspr_imaging_app.storage.session import load_session  # noqa: E402

RESULTS: dict = {}
_QT_APP = None


def ms(seconds: float) -> float:
    return seconds * 1000.0


def stats(values_ms: list[float]) -> str:
    if not values_ms:
        return "n/a"
    return f"{statistics.mean(values_ms):8.1f} ms  (median {statistics.median(values_ms):7.1f}, min {min(values_ms):7.1f}, max {max(values_ms):7.1f}, n={len(values_ms)})"


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, ms(time.perf_counter() - started)


def header(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}", flush=True)


# -- setup ---------------------------------------------------------------------------------------------------


def load_one(folder: Path):
    result = io.load_dataset(folder)
    if isinstance(result, io.DatasetLoadChoice):
        result = result.candidates[0]
    return result


def build_modules(dataset, session_root: Path):
    modules = app_rewrite._build_modules()
    modules.dataset.load_dataset(dataset)
    state = load_session(session_root)
    if state is None:
        raise SystemExit(f"No session found in {session_root}")
    app_rewrite.apply_session(
        state, modules.geometry, modules.mask, modules.chromatic, modules.background, modules.roi_toolbox,
        modules.selection, modules.analysis_settings, modules.frame_roles, modules.mask_pointers,
    )
    # A module reacting to the restore (e.g. ROI geometry sync) must not have moved a ROI: a different ROI position would
    # silently change every number below.
    moved = [
        (a.area_roi_id, a.center_x, b.center_x)
        for a, b in zip(state.rois, modules.roi_toolbox.rois())
        if abs(a.center_x - b.center_x) > 1e-9 or abs(a.center_y - b.center_y) > 1e-9
    ]
    if moved or len(state.rois) != len(modules.roi_toolbox.rois()):
        raise SystemExit(f"Restoring the session changed ROI positions or count ({len(moved)} moved): timings would not be comparable.")
    if modules.roi_toolbox.has_timeline():
        print("NOTE: this session has a ROI geometry timeline; this tool times the no-timeline path only.")
    return modules, state


def spectral_wavelengths(modules, cube: int) -> list[float]:
    return [wl for wl in modules.dataset.wavelengths_for_cube(cube) if modules.frame_roles.is_spectral(cube, wl)]


def pick_cubes(modules, count: int) -> list[int]:
    cubes = list(modules.dataset.spectral_cubes())
    if count >= len(cubes):
        return cubes
    positions = np.linspace(len(cubes) * 0.05, len(cubes) * 0.95, count).astype(int)
    return [cubes[i] for i in positions]


# -- 1. reads ------------------------------------------------------------------------------------------------


def time_reads(label: str, dataset, cubes: list[int], modules, fast_tiff: bool = False) -> dict:
    records = io.dataset_record_map(dataset)
    io.use_fast_tiff_decoder(fast_tiff)
    out = {}
    for pass_name in ("cold", "warm"):
        per_plane = []
        for cube in cubes:
            for wl in spectral_wavelengths(modules, cube):
                _, took = timed(io.load_image_array_uncached, str(records[(cube, wl)].path))
                per_plane.append(took)
        out[pass_name] = per_plane
        print(f"  {label:<28s} {pass_name:<5s} full plane: {stats(per_plane)}", flush=True)
    io.use_fast_tiff_decoder(False)
    return {k: statistics.mean(v) for k, v in out.items()}


def time_zarr_scoped(dataset, cubes: list[int], modules, rois, geometry) -> dict:
    """Scoped reads of the raw-space window around ROIs. The window is the ROI's reference ring plus a margin, placed
    approximately (processed position + crop offset; the 1.4 degree rotation moves it by a few pixels, which does not
    change which chunks are touched in a meaningful way)."""
    records = io.dataset_record_map(dataset)
    crop = geometry.crop
    offset_x, offset_y = (crop.x, crop.y) if crop.enabled else (0, 0)
    height, width = io.load_image_shape(str(next(iter(records.values())).path))
    det = modules.roi_toolbox.detection_settings()

    def window(roi, margin: int) -> tuple[int, int, int, int]:
        outer = roi.reference_outer_diameter_px or det.reference_outer_diameter_px
        half = int(outer / 2 + margin)
        cx, cy = int(roi.center_x + offset_x), int(roi.center_y + offset_y)
        return max(cy - half, 0), min(cy + half, height), max(cx - half, 0), min(cx + half, width)

    spread = lambda n: [rois[int(i)] for i in np.linspace(0, len(rois) - 1, n)]  # noqa: E731
    cases = [
        ("1 ROI, ring + 8 px", spread(1), 8),
        ("5 ROIs, ring + 8 px", spread(5), 8),
        ("20 ROIs, ring + 8 px", spread(20), 8),
        ("160 ROIs, ring + 8 px (one read each)", list(rois), 8),
        ("5 ROIs, ring + 150 px (windowed background)", spread(5), 150),
    ]
    out = {}
    wl_by_cube = {c: spectral_wavelengths(modules, c) for c in cubes}
    # One full-plane read for reference, same cubes, taken from the cheapest source we have (the direct shard reader).
    full = []
    for cube in cubes:
        wl = wl_by_cube[cube][len(wl_by_cube[cube]) // 2]
        _, took = timed(io.load_image_array_uncached, str(records[(cube, wl)].path))
        full.append(took)
    print(f"  {'full plane (reference)':<46s} {stats(full)}")
    out["full_plane_ms"] = statistics.mean(full)
    for name, chosen, margin in cases:
        per_frame = []
        for cube in cubes:
            wl = wl_by_cube[cube][len(wl_by_cube[cube]) // 2]
            started = time.perf_counter()
            for roi in chosen:
                y0, y1, x0, x1 = window(roi, margin)
                io.dataset_load_plane_roi(dataset, cube, wl, y0, y1, x0, x1, record=records[(cube, wl)])
            per_frame.append(ms(time.perf_counter() - started))
        print(f"  {name:<46s} {stats(per_frame)}   (all ROIs of one frame)")
        out[name] = statistics.mean(per_frame)
    return out


# -- 2. frame stage ------------------------------------------------------------------------------------------


def frame_inputs(modules, cube: int, wl: float):
    """What the analysis needs besides the pixels, resolved the way `AnalysisEngine._gather_wavelength_inputs` does."""
    resolved, t_mask = timed(modules.mask.resolve_mask_source, (cube, wl))
    affine, t_affine = timed(modules.chromatic.affine_for, (cube, wl))
    warp = None
    mask_array = None
    t_between = 0.0
    if resolved is not None:
        authored_frame, mask_array, _scope = resolved
        if authored_frame != (cube, wl):
            warp, t_between = timed(modules.chromatic.affine_between, authored_frame, (cube, wl))
    ignore, t_ignore = timed(pp.resolve_external_mask, mask_array, modules.geometry.settings(), warp)
    return affine, ignore, {"mask_lookup": t_mask, "affine_for": t_affine, "affine_between": t_between, "resolve_external_mask": t_ignore}


def time_frame_stage(modules, dataset, cubes: list[int]) -> tuple[dict, list]:
    records = io.dataset_record_map(dataset)
    geometry = modules.geometry.settings()
    background = modules.background.settings()
    det = modules.roi_toolbox.detection_settings()
    rois = list(modules.roi_toolbox.rois())
    parts: dict[str, list[float]] = {k: [] for k in (
        "mask_lookup", "affine_for", "affine_between", "resolve_external_mask",
        "read (uncached)", "rotate/crop", "ROI exclusion mask", "background estimate", "apply_background (divide)", "apply_preprocessing TOTAL",
    )}
    frames = []
    masked_frames = 0
    for cube in cubes:
        for wl in spectral_wavelengths(modules, cube):
            affine, ignore, lookups = frame_inputs(modules, cube, wl)
            for key, value in lookups.items():
                parts[key].append(value)  # `timed` already returns milliseconds
            masked_frames += ignore is not None
            raw, t_read = timed(io.load_image_array_uncached, str(records[(cube, wl)].path))
            parts["read (uncached)"].append(t_read)

            spatial, t = timed(pp.apply_spatial_preprocessing, raw, geometry)
            parts["rotate/crop"].append(t)
            image_f32 = spatial.astype(np.float32, copy=False)
            usable_ignore = ignore if ignore is not None and ignore.shape == spatial.shape[:2] else None
            valid_mask, t = timed(
                lambda: ~bg_estimate._combined_exclusion_mask(
                    image_f32, rois=rois if background.flatten_background_exclude_area_rois else None,
                    mask_settings=det if background.flatten_background_exclude_mask else None,
                    external_mask=usable_ignore if background.flatten_background_exclude_mask else None,
                    dilation_px=int(background.flatten_background_exclusion_dilation_px),
                )
            )
            parts["ROI exclusion mask"].append(t)
            (bgnd, baseline), t = timed(
                bg_estimate._background_and_baseline, image_f32, valid_mask,
                float(background.flatten_background_sigma_px), int(background.flatten_background_binning),
            )
            parts["background estimate"].append(t)
            _, t = timed(bg_estimate.apply_background, image_f32, bgnd, baseline)
            parts["apply_background (divide)"].append(t)

            processed, t = timed(
                pp.apply_preprocessing, raw, geometry, background, rois=rois, mask_settings=det, external_mask=ignore
            )
            parts["apply_preprocessing TOTAL"].append(t)
            frames.append((cube, wl, processed, affine, ignore))
    for key, values in parts.items():
        print(f"  {key:<28s} {stats(values)}")
    print(f"  frames that have an ignore mask: {masked_frames} of {len(frames)} (a frame without one skips the mask work)")
    return {k: statistics.mean(v) for k, v in parts.items()}, frames


# -- 3. ROI stage --------------------------------------------------------------------------------------------


def _incremental_union(rois, shape, affine) -> np.ndarray:
    """Every ROI's sample aperture OR-ed together, built the way `compute_cell` builds it (one mask at a time)."""
    union = np.zeros(shape, dtype=bool)
    for other in rois:
        union |= rz.rasterize_sample(other, shape, affine)
    return union


def roi_values_full(roi, processed, valid, affine, ignore, union, inner, outer, reduction, trimmed):
    """One ROI's (sample, reference) at one frame, exactly as `compute_cell` does it today: full-frame masks."""
    shape = processed.shape[:2]
    sample_mask = rz.rasterize_sample(roi, shape, affine)
    reference_mask = rz.rasterize_reference(roi, shape, affine, default_inner_diameter_px=inner, default_outer_diameter_px=outer)
    reference_mask = reference_mask & ~union
    if ignore is not None and ignore.shape == shape:
        sample_mask = sample_mask & ~ignore
        reference_mask = reference_mask & ~ignore
    sample_valid = sample_mask & valid
    reference_valid = reference_mask & valid
    yy, xx = np.nonzero(reference_valid)
    return reduce_sample_and_reference(
        processed[sample_valid], processed[reference_valid], reduction, trimmed_mean_fraction=trimmed,
        reference_xx=xx.astype(np.float64), reference_yy=yy.astype(np.float64), sample_x=float(roi.center_x), sample_y=float(roi.center_y),
    )


def roi_values_patch(roi, rois, centers, sample_radii, processed, valid, affine, ignore, inner, outer, reduction, trimmed):
    """The same numbers from a small window around the ROI (prototype of the planned ROI stage). Window = the ring's reach
    plus the chromatic shift plus 3 px; neighbours' sample apertures are rasterised only inside the window."""
    height, width = processed.shape[:2]
    _inner_d, outer_d = rz.effective_reference_diameters(roi, inner, outer)
    half = max(float(outer_d), float(roi.sample_diameter_px)) / 2.0 + 3.0
    a = np.asarray(affine, dtype=np.float64)
    cx, cy = float(roi.center_x), float(roi.center_y)
    tx = a[0, 0] * cx + a[0, 1] * cy + a[0, 2]
    ty = a[1, 0] * cx + a[1, 1] * cy + a[1, 2]
    x0 = int(max(np.floor(min(cx, tx) - half), 0))
    x1 = int(min(np.ceil(max(cx, tx) + half) + 1, width))
    y0 = int(max(np.floor(min(cy, ty) - half), 0))
    y1 = int(min(np.ceil(max(cy, ty) + half) + 1, height))
    patch_shape = (y1 - y0, x1 - x0)
    sample_mask = rz.rasterize_sample_for_patch(roi, (x0, y0), patch_shape, affine)
    reference_mask = rz.rasterize_reference_for_patch(
        roi, (x0, y0), patch_shape, affine, default_inner_diameter_px=inner, default_outer_diameter_px=outer
    )
    reach_x, reach_y = (x1 - x0) / 2.0 + 10.0, (y1 - y0) / 2.0 + 10.0
    mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    near = np.nonzero((np.abs(centers[:, 0] - mid_x) < reach_x + sample_radii) & (np.abs(centers[:, 1] - mid_y) < reach_y + sample_radii))[0]
    for index in near:
        reference_mask &= ~rz.rasterize_sample_for_patch(rois[index], (x0, y0), patch_shape, affine)
    if ignore is not None and ignore.shape == processed.shape[:2]:
        ignore_patch = ignore[y0:y1, x0:x1]
        sample_mask = sample_mask & ~ignore_patch
        reference_mask = reference_mask & ~ignore_patch
    valid_patch = valid[y0:y1, x0:x1]
    patch = processed[y0:y1, x0:x1]
    sample_valid = sample_mask & valid_patch
    reference_valid = reference_mask & valid_patch
    yy, xx = np.nonzero(reference_valid)
    return reduce_sample_and_reference(
        patch[sample_valid], patch[reference_valid], reduction, trimmed_mean_fraction=trimmed,
        reference_xx=(xx + x0).astype(np.float64), reference_yy=(yy + y0).astype(np.float64), sample_x=cx, sample_y=cy,
    )


def time_roi_stage(modules, frames: list) -> dict:
    det = modules.roi_toolbox.detection_settings()
    inner, outer = det.reference_inner_diameter_px, det.reference_outer_diameter_px
    rois = list(modules.roi_toolbox.rois())
    trimmed = modules.analysis_engine._trimmed_mean_fraction
    reduction = det.reduction_method
    centers = np.array([[r.center_x, r.center_y] for r in rois], dtype=np.float64)
    sample_radii = np.array([r.sample_diameter_px / 2.0 for r in rois], dtype=np.float64)
    use = frames[:: max(len(frames) // 3, 1)][:3]  # three frames, spread over the sample
    t_union, t_isfinite, t_full, t_patch = [], [], [], []
    differ = 0
    worst = 0.0
    compared = 0
    for cube, wl, processed, affine, ignore in use:
        shape = processed.shape[:2]
        union, t = timed(_incremental_union, rois, shape, affine)
        t_union.append(t)
        valid, t = timed(np.isfinite, processed)
        t_isfinite.append(t)
        for roi in rois:
            full, t = timed(roi_values_full, roi, processed, valid, affine, ignore, union, inner, outer, reduction, trimmed)
            t_full.append(t)
            patch, t = timed(roi_values_patch, roi, rois, centers, sample_radii, processed, valid, affine, ignore, inner, outer, reduction, trimmed)
            t_patch.append(t)
            compared += 1
            for a, b in zip(full, patch):
                if not (a == b or (np.isnan(a) and np.isnan(b))):
                    differ += 1
                    worst = max(worst, abs(float(a) - float(b)))
                    break
    print(f"  per frame, union of all {len(rois)} ROIs' sample apertures (built once per frame, shared)   {stats(t_union)}")
    print(f"  per frame, np.isfinite(processed)                                                    {stats(t_isfinite)}")
    print(f"  per ROI, FULL-FRAME masks (today)     rasterise + exclude + gather + reduce       {stats(t_full)}")
    print(f"  per ROI, PATCH-scoped (prototype)     same steps in a small window                {stats(t_patch)}")
    print(f"  (sample, reference) values identical, full-frame vs patch: {compared - differ} of {compared} ROI/frame pairs"
          + ("" if not differ else f"   <-- {differ} DIFFER, largest difference {worst:.3g}"))
    return {
        "union_per_frame_ms": statistics.mean(t_union), "isfinite_ms": statistics.mean(t_isfinite),
        "roi_full_ms": statistics.mean(t_full), "roi_patch_ms": statistics.mean(t_patch),
        "values_compared": compared, "values_differ": differ,
    }


# -- 4. end to end: today's compute_cell ----------------------------------------------------------------------


class _CellLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.rows: list[tuple] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("compute_cell roi="):
            self.rows.append(record.args)


def time_end_to_end(modules, cubes: list[int], scratch: Path) -> dict:
    engine = modules.analysis_engine
    det = modules.roi_toolbox.detection_settings()
    rois = tuple(modules.roi_toolbox.rois())
    naming = engine._naming()
    inner, outer = engine._default_reference_diameters()
    chosen = [rois[int(i)] for i in np.linspace(0, len(rois) - 1, 6)]
    handler = _CellLog()
    task_logger = logging.getLogger("lspr_imaging_app.analysis.tasks")
    previous_level = task_logger.level
    task_logger.setLevel(logging.DEBUG)
    task_logger.addHandler(handler)
    first_cells, later_cells, gathers_cold, gathers_warm = [], [], [], []
    breakdown = {"preprocess": [], "rasterize": [], "reduce": []}
    try:
        for cube in cubes:
            exclusion_cache: dict = {}
            for index, roi in enumerate(chosen):
                # The engine calls this once per cell. The first call of a cube reads the planes (cached afterwards).
                inputs, t_gather = timed(engine._gather_wavelength_inputs, cube)
                (gathers_cold if index == 0 else gathers_warm).append(t_gather)
                started = time.perf_counter()
                compute_cell(
                    roi, cube, inputs,
                    reduction_method=det.reduction_method, trimmed_mean_fraction=engine._trimmed_mean_fraction,
                    default_reference_inner_diameter_px=inner, default_reference_outer_diameter_px=outer,
                    masks_dir=scratch / "masks", chromatic_dir=scratch / "chromatic", settings_dir=scratch / "settings",
                    naming=naming, all_rois=rois, detection_settings=det,
                    reference_exclusion_mode=engine._reference_exclusion_mode(),
                    sample_exclusion_cache=exclusion_cache, coverage_thresholds=engine._coverage_thresholds(),
                )
                took = ms(time.perf_counter() - started)
                (first_cells if index == 0 else later_cells).append(took)
                if index > 0 and handler.rows:
                    _roi, _cube, _n, _nwl, _total, pre, rast, red = handler.rows[-1]
                    breakdown["preprocess"].append(ms(pre))
                    breakdown["rasterize"].append(ms(rast))
                    breakdown["reduce"].append(ms(red))
    finally:
        task_logger.removeHandler(handler)
        task_logger.setLevel(previous_level)
    print(f"  _gather_wavelength_inputs, first of a cube (reads 27 planes)   {stats(gathers_cold)}")
    print(f"  _gather_wavelength_inputs, later cells (planes cached)         {stats(gathers_warm)}")
    print(f"  compute_cell, first cell of a cube (builds the exclusion union){stats(first_cells)}")
    print(f"  compute_cell, later cells of a cube                            {stats(later_cells)}")
    for name, values in breakdown.items():
        print(f"      of which {name:<12s}                                      {stats(values)}")
    per_cell = statistics.mean(later_cells) + statistics.mean(gathers_warm)
    cells = len(rois) * len(list(modules.dataset.spectral_cubes()))
    print(f"\n  Today's ROI-major loop, per cell = compute_cell + gather = {per_cell:.0f} ms")
    print(f"  Full run: {len(rois)} ROIs x {len(list(modules.dataset.spectral_cubes()))} cubes = {cells} cells -> {cells * per_cell / 3.6e6:.1f} hours (single thread, as the engine runs)")
    return {
        "compute_cell_later_ms": statistics.mean(later_cells), "compute_cell_first_ms": statistics.mean(first_cells),
        "gather_warm_ms": statistics.mean(gathers_warm), "gather_cold_ms": statistics.mean(gathers_cold),
        "breakdown_ms": {k: statistics.mean(v) for k, v in breakdown.items()}, "per_cell_ms": per_cell, "cells": cells,
        "projected_hours": cells * per_cell / 3.6e6,
    }


# -- 4b. provenance bookkeeping: cost of one settings-snapshot lookup as the folder fills ----------------------


def time_provenance_scaling(modules, scratch: Path) -> dict:
    """`persist_settings_snapshot` (called per wavelength per cell by `compute_cell`, and per wavelength per cell by the
    planner) globs and parses EVERY existing settings_v*.json to find an identical one. Each snapshot names its frame's own
    chromatic version, so there is about one file per frame. Time one lookup as the folder grows to a full dataset's size."""
    from dataclasses import asdict

    from lspr_imaging_app.analysis import provenance as pv

    engine = modules.analysis_engine
    rois = tuple(modules.roi_toolbox.rois())
    det = modules.roi_toolbox.detection_settings()
    geometry, background = modules.geometry.settings(), modules.background.settings()
    sample_exclusion = pv.sample_exclusion_digest(rois)
    background_exclusion = pv.background_exclusion_digest(rois, background, det)
    thresholds = engine._coverage_thresholds().as_dict()
    per_cube = max(len(spectral_wavelengths(modules, next(iter(modules.dataset.spectral_cubes())))), 1)

    def snapshot(index: int) -> "pv.SettingsSnapshot":
        return pv.SettingsSnapshot(
            geometry=asdict(geometry), mask=None,
            chromatic=pv.ChromaticSnapshotRef(cube_index=index // per_cube, wavelength_nm=470.0 + 10.0 * (index % per_cube), version=1),
            background=asdict(background), reduction_method=det.reduction_method,
            reference_exclusion_mode=pv.DEFAULT_REFERENCE_EXCLUSION_MODE, sample_exclusion=sample_exclusion,
            background_exclusion=background_exclusion, coverage_thresholds=thresholds,
        )

    folder = scratch / "prov_settings"
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    points: list[tuple[int, float]] = []
    total_frames = len(list(modules.dataset.spectral_cubes())) * per_cube
    for target in (per_cube, 10 * per_cube, 50 * per_cube, 150 * per_cube, total_frames):
        for index in range(written, target):
            (folder / f"settings_v{index + 1}.json").write_text(json.dumps(snapshot(index).as_json(), indent=2), encoding="utf-8")
        written = target
        times = [timed(pv.persist_settings_snapshot, folder, snapshot((k * 997) % target))[1] for k in range(2 if target > 2000 else 4)]
        size = (folder / "settings_v1.json").stat().st_size
        points.append((target, statistics.mean(times)))
        print(f"  {target:5d} snapshot files ({size / 1024:.0f} KiB each): one lookup of an existing snapshot = {statistics.mean(times):9.1f} ms", flush=True)
    per_file = statistics.mean(t / n for n, t in points[1:])
    cubes = len(list(modules.dataset.spectral_cubes()))
    calls = len(rois) * cubes * per_cube
    mean_files = total_frames / 2.0
    hours = calls * per_file * mean_files / 3.6e6
    print(f"\n  ~{per_file:.2f} ms per file per lookup. One planning pass makes {len(rois)} ROIs x {cubes} cubes x {per_cube} wavelengths = {calls:,} lookups,")
    print(f"  against a folder that grows to {total_frames:,} files (mean {mean_files:,.0f}): ~{hours:,.0f} hours (~{hours / 24:,.0f} days) for the fingerprints alone. The run repeats it.")
    return {"points": points, "ms_per_file_per_lookup": per_file, "calls": calls, "projected_hours": hours}


# -- 4c. what a frame-major run would cost, from the numbers above ---------------------------------------------


def print_projection(modules, results: dict) -> None:
    cubes = len(list(modules.dataset.spectral_cubes()))
    per_cube = len(spectral_wavelengths(modules, next(iter(modules.dataset.spectral_cubes()))))
    frames = cubes * per_cube
    n_rois = len(list(modules.roi_toolbox.rois()))
    frame = results.get("frame", {})
    roi = results.get("roi", {})
    reads = results.get("reads", {})
    if not frame or not roi:
        print("  (needs sections 2 and 3)")
        return
    sources = {}
    if "tiff_tifffile" in reads:
        sources["TIFF, tifffile"] = reads["tiff_tifffile"]["warm"]
    if "tiff_fast" in reads:
        sources["TIFF, native decoder"] = reads["tiff_fast"]["warm"]
    if "zarr_full" in reads:
        sources["OME-Zarr"] = reads["zarr_full"]["warm"]
    print(f"  Frame-major run, ONE thread, {frames:,} frames x {n_rois} ROIs, background flattening as in the session:")
    print(f"  (frame = read + preprocess {frame['apply_preprocessing TOTAL']:.0f} ms; full-frame ROI path also builds a {roi['union_per_frame_ms']:.0f} ms union per frame; "
          f"ROI = {roi['roi_full_ms']:.2f} ms full-frame today, {roi['roi_patch_ms']:.2f} ms patch-scoped)")
    for name, read_ms in sources.items():
        # The full-frame path shares one union of sample apertures per frame; the patch path rasterises neighbours inside its
        # own window instead and needs no union.
        for label, per_roi, union_ms in (
            ("full-frame ROI masks", roi["roi_full_ms"], roi["union_per_frame_ms"]),
            ("patch-scoped ROI masks", roi["roi_patch_ms"], 0.0),
        ):
            per_frame = read_ms + frame["apply_preprocessing TOTAL"] + union_ms + n_rois * per_roi
            print(f"    {name:<22s} {label:<24s} {per_frame:7.0f} ms per frame -> {frames * per_frame / 60000.0:7.1f} min")


# -- 5. threads ----------------------------------------------------------------------------------------------


def time_threads(label: str, dataset, modules, cubes: list[int], thread_counts: list[int]) -> dict:
    """Read + preprocess throughput. Inputs (mask, affine) are resolved up front on this thread, as the planned runner
    will do; the worker threads touch no module. Plain threads only (never QThreadPool near a zarr read)."""
    records = io.dataset_record_map(dataset)
    geometry = modules.geometry.settings()
    background = modules.background.settings()
    det = modules.roi_toolbox.detection_settings()
    rois = list(modules.roi_toolbox.rois())
    jobs = []
    for cube in cubes:
        for wl in spectral_wavelengths(modules, cube):
            _affine, ignore, _ = frame_inputs(modules, cube, wl)
            jobs.append((str(records[(cube, wl)].path), ignore))
    jobs = jobs[:24]

    def read_only(job):
        return io.load_image_array_uncached(job[0]).shape

    def read_and_preprocess(job):
        raw = io.load_image_array_uncached(job[0])
        return pp.apply_preprocessing(raw, geometry, background, rois=rois, mask_settings=det, external_mask=job[1]).shape

    out = {}
    for task_name, fn in (("read only", read_only), ("read + preprocess", read_and_preprocess)):
        for fn_warm in jobs:  # warm the file cache once so every thread count sees the same state
            io.load_image_array_uncached(fn_warm[0])
        base = None
        for threads in thread_counts:
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=threads) as pool:
                list(pool.map(fn, jobs))
            elapsed = time.perf_counter() - started
            rate = len(jobs) / elapsed
            base = base or rate
            print(f"  {label:<6s} {task_name:<18s} {threads} thread(s): {rate:6.1f} frames/s  ({1000 / rate:6.1f} ms per frame, speed-up x{rate / base:.2f})", flush=True)
            out[f"{task_name}/{threads}"] = rate
    return out


# -- main ----------------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tiff", type=Path, required=True, help="the TIFF stack folder")
    parser.add_argument("--zarr", type=Path, default=None, help="an OME-Zarr export of the same frames (optional)")
    parser.add_argument("--session", type=Path, required=True, help="the session folder (the one holding session/session.json)")
    parser.add_argument("--cubes", type=int, default=4, help="how many cubes to sample, spread over the run (default 4)")
    parser.add_argument("--cube-list", type=str, default="", help="explicit cube indices, e.g. 15,200,250 (overrides --cubes)")
    parser.add_argument("--threads", type=str, default="1,2,4,6,8", help="thread counts to try (default 1,2,4,6,8)")
    parser.add_argument("--skip", type=str, default="", help="sections to skip: reads,frame,roi,e2e,provenance,threads")
    parser.add_argument("--json", type=Path, default=None, help="also write the numbers to this JSON file")
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])  # the modules are QObjects; keep the application alive
    scratch = Path(tempfile.mkdtemp(prefix="lspr_stage_timing_"))
    try:
        tiff_ds = load_one(args.tiff)
        modules, state = build_modules(tiff_ds, args.session)
        cubes = [int(c) for c in args.cube_list.split(',') if c.strip()] if args.cube_list else pick_cubes(modules, args.cubes)
        rois = list(modules.roi_toolbox.rois())
        geometry = modules.geometry.settings()
        background = modules.background.settings()
        header("Setup")
        print(f"  cpu threads: {os.cpu_count()}   python {sys.version.split()[0]}")
        print(f"  dataset: {len(list(modules.dataset.spectral_cubes()))} cubes, {len(spectral_wavelengths(modules, cubes[0]))} spectral wavelengths per cube, plane {io.load_image_shape(str(next(iter(io.dataset_record_map(tiff_ds).values())).path))}")
        print(f"  session: {len(rois)} ROIs, {len(state.mask_changes)} mask changes, {len(state.chromatic_models)} chromatic models")
        print(f"  geometry: rotation {geometry.rotation_angle_deg:.2f} deg, crop {geometry.crop.width}x{geometry.crop.height} at ({geometry.crop.x},{geometry.crop.y}), enabled={geometry.crop.enabled}")
        print(f"  background: flatten={background.flatten_background_enabled}, sigma {background.flatten_background_sigma_px}, bin {background.flatten_background_binning}, "
              f"exclude ROIs={background.flatten_background_exclude_area_rois}, exclude mask={background.flatten_background_exclude_mask}")
        print(f"  reduction: {modules.roi_toolbox.detection_settings().reduction_method}   sampled cubes: {cubes}")
        RESULTS["setup"] = {"cubes": cubes, "rois": len(rois), "cpu_threads": os.cpu_count()}

        zarr_ds = load_one(args.zarr) if args.zarr else None

        if "reads" not in skip:
            header("1. READS (per plane, uncached reader; 'cold' = first read, 'warm' = second read of the same files)")
            RESULTS["reads"] = {"tiff_tifffile": time_reads("TIFF (tifffile, analysis default)", tiff_ds, cubes, modules)}
            RESULTS["reads"]["tiff_fast"] = time_reads("TIFF (native decoder)", tiff_ds, cubes, modules, fast_tiff=True)
            if zarr_ds is not None:
                RESULTS["reads"]["zarr_full"] = time_reads("OME-Zarr", zarr_ds, cubes, modules)
                print("\n  OME-Zarr scoped reads (raw-space windows around the session's ROIs; middle wavelength of each sampled cube):")
                RESULTS["reads"]["zarr_scoped"] = time_zarr_scoped(zarr_ds, cubes, modules, rois, geometry)

        frames: list = []
        if "frame" not in skip or "roi" not in skip:
            header("2. FRAME STAGE (per frame, today's code path, sub-steps timed separately)")
            RESULTS["frame"], frames = time_frame_stage(modules, tiff_ds, cubes[: max(args.cubes // 2, 2)])
        if "roi" not in skip and frames:
            header("3. ROI STAGE (3 frames x all ROIs): full-frame masks (today) vs patch-scoped masks")
            RESULTS["roi"] = time_roi_stage(modules, frames)
        if "e2e" not in skip:
            header("4. END TO END: today's ROI-major compute_cell (6 ROIs x sampled cubes, extrapolated)")
            RESULTS["e2e"] = time_end_to_end(modules, cubes[:2], scratch)
        if "provenance" not in skip:
            header("4b. PROVENANCE BOOKKEEPING: one settings-snapshot lookup as the snapshot folder fills up")
            RESULTS["provenance"] = time_provenance_scaling(modules, scratch)
        header("4c. PROJECTION: a frame-major run from the measured parts (single thread)")
        print_projection(modules, RESULTS)
        if "threads" not in skip:
            header("5. THREADS: read + preprocess throughput (24 frames, warm file cache)")
            counts = [int(t) for t in args.threads.split(",")]
            RESULTS["threads"] = {"tiff": time_threads("TIFF", tiff_ds, modules, cubes[:1], counts)}
            if zarr_ds is not None:
                RESULTS["threads"]["zarr"] = time_threads("zarr", zarr_ds, modules, cubes[:1], counts)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if args.json:
        args.json.write_text(json.dumps(RESULTS, indent=2, default=float), encoding="utf-8")
        print(f"\nnumbers written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
