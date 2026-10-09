"""Array lab: measure today's ROI-array detection and compare spot-size (edge) models on real data.

Offline experiment for the ROIs-tab "Array" section (design: `docs/roi_array_section_audit_2026-10-08.md`).
Not part of the app. Run:

    python tools/array_lab.py <folder with imLCTFatWL<wl>Frame<n>.tiff> [--frame 0] [--wl 600]

What it prints:
1. Baseline (the code the app has today): `estimate_array_geometry` and `detect_rois` timings and the
   geometry they find (rows, cols, pitch, diameter, how many spots).
2. Spot sizes on those centres for every edge model in `roi.edge_size.EDGE_MODELS` (and a few parameter
   variants): median, spread (MAD) and minimum diameter over the array, plus the per-model time.

Edge sizes are measured on a *contrast map* (`image_features.contrast_map`, spot positive), with light
smoothing only (sigma 0.7 px) so the lab does not add blur of its own.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import numpy as np
import tifffile

from lspr_imaging_app.image_features import contrast_map, fill_invalid
from lspr_imaging_app.processing.roi_array_geometry import estimate_array_geometry
from lspr_imaging_app.roi.array_detection import find_array
from lspr_imaging_app.roi.detection import detect_rois
from lspr_imaging_app.roi.edge_size import EDGE_MODELS, EdgeSizeParams, measure_spot_sizes, summarize
from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
from lspr_imaging_app.roi.ring_size import RingParams, measure_rings, neighbour_warnings, summarize_rings


def load_plane(folder: Path, frame: int, wavelength: int) -> np.ndarray:
    pattern = re.compile(rf"^imLCTFatWL{wavelength}Frame{frame}\.tiff?$")
    for path in folder.iterdir():
        if pattern.match(path.name):
            return tifffile.imread(path).astype(np.float32)
    raise SystemExit(f"No imLCTFatWL{wavelength}Frame{frame}.tiff in {folder}")


def timed(function, *args, **kwargs):
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, (time.perf_counter() - started) * 1000.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", type=Path)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--wl", type=int, default=600)
    args = parser.parse_args()

    image = load_plane(args.images, args.frame, args.wl)
    print(f"image {image.shape[1]} x {image.shape[0]} px, {args.wl} nm, frame {args.frame}")

    # 1. baseline -------------------------------------------------------------------------
    diagnostics: dict = {}
    geometry, ms = timed(estimate_array_geometry, image, diagnostics=diagnostics)
    print(f"\n[baseline] estimate_array_geometry: {ms:.0f} ms")
    if geometry is None:
        print("  rejected:", diagnostics.get("reason"))
        return
    print(
        f"  rows x cols = {geometry.rows} x {geometry.cols}, pitch {geometry.spacing_px:.2f} px, "
        f"diameter {2 * geometry.radius_px:.2f} px, blobs {geometry.blob_count}"
    )
    settings = AreaRoiDetectionSettings(
        sample_diameter_px=2.0 * geometry.radius_px,
        array_rows=geometry.rows,
        array_cols=geometry.cols,
        array_spacing_px=int(round(geometry.spacing_px)),
    )
    rois, ms = timed(detect_rois, image, settings)
    print(f"[baseline] detect_rois with that geometry: {ms:.0f} ms, {len(rois)} ROIs, {sum(r.inferred for r in rois)} inferred")
    centers = np.array([(r.center_x, r.center_y) for r in rois], dtype=np.float64)

    # 1b. new lattice detector --------------------------------------------------------------
    new_diagnostics: dict = {}
    fit, ms = timed(find_array, image, diagnostics=new_diagnostics)
    print(f"\n[new] find_array: {ms:.0f} ms")
    if fit is None:
        print("  rejected:", new_diagnostics.get("reason"))
    else:
        print(
            f"  rows x cols = {fit.rows} x {fit.cols}, pitch x {fit.pitch_x_px:.2f} / y {fit.pitch_y_px:.2f} px, "
            f"tilt {fit.tilt_deg:.2f} deg, skew {fit.skew_deg:.2f} deg, rough diameter {fit.rough_diameter_px:.1f} px"
        )
        print(f"  spots found {fit.found_count} of {fit.count}, residual rms {fit.residual_rms_px:.2f} px, warnings {list(fit.warnings)}")

    # 2. edge models ----------------------------------------------------------------------
    filled, valid = fill_invalid(image)
    contrast = contrast_map(filled, background_px=101, smooth_sigma=0.7)
    if fit is not None:  # clean centres: only the spots the new detector really found
        centers = fit.centers_xy[fit.found]
        pitch_limit = 0.5 * min(fit.pitch_x_px, fit.pitch_y_px)
        rough = fit.rough_diameter_px
    else:
        pitch_limit = 0.5 * geometry.spacing_px
        rough = 2.0 * geometry.radius_px
    print(f"\n[edge models] {len(centers)} spots, rough diameter {rough:.1f} px, search limit radius {pitch_limit:.1f} px")
    variants: list[tuple[str, EdgeSizeParams]] = [(m, EdgeSizeParams(model=m, max_radius_px=pitch_limit)) for m in EDGE_MODELS]
    variants += [
        ("plateau_fraction f=0.05", EdgeSizeParams("plateau_fraction", fraction=0.05, max_radius_px=pitch_limit)),
        ("plateau_fraction f=0.30", EdgeSizeParams("plateau_fraction", fraction=0.30, max_radius_px=pitch_limit)),
        ("plateau_fraction x4 oversample", EdgeSizeParams("plateau_fraction", oversample=4, max_radius_px=pitch_limit)),
        ("plateau_combined x4 oversample", EdgeSizeParams("plateau_combined", oversample=4, max_radius_px=pitch_limit)),
    ]
    print(f"  {'model':34s} {'n':>4s} {'median':>8s} {'MAD':>6s} {'min':>7s} {'ms':>6s}")
    for label, params in variants:
        sizes, ms = timed(measure_spot_sizes, contrast, centers, rough, params, valid)
        stats = summarize(sizes)
        print(f"  {label:34s} {stats['count']:4.0f} {stats['median']:8.2f} {stats['mad']:6.2f} {stats['min']:7.2f} {ms:6.0f}")
    # 3. reference rings, for two sample sizes (plateau 15 % and half-max) -------------------------
    if fit is not None:
        for label, model in (("plateau_fraction", "plateau_fraction"), ("half_max", "half_max")):
            sizes = measure_spot_sizes(contrast, centers, rough, EdgeSizeParams(model=model, max_radius_px=pitch_limit), valid)
            sample = summarize(sizes)["median"]
            for ring_label, ring_params in (
                ("measured, 5 %", RingParams(max_radius_px=pitch_limit)),
                ("measured, 15 %", RingParams(max_radius_px=pitch_limit, background_fraction=0.15)),
                ("ratio 1.4", RingParams(inner_mode="ratio", inner_ratio=1.4)),
            ):
                rings, ms = timed(measure_rings, contrast, centers, sample, ring_params, valid)
                stats = summarize_rings(rings)
                inner = np.array([r.inner_diameter_px for r in rings if r.ok])
                end = np.array([r.spot_end_diameter_px for r in rings if r.ok])
                print(
                    f"  [ring] sample {label} {sample:.1f} px, {ring_label:15s}: n {stats['count']:.0f}, inner median {np.median(inner):.1f} "
                    f"(min {inner.min():.1f}, max {inner.max():.1f}, robust max {stats['inner_robust']:.1f}, {stats['outliers']:.0f} outliers), outer max {stats['outer_max']:.1f}"
                    + (f", spot end median {np.nanmedian(end):.1f}" if np.isfinite(end).any() else "")
                    + f", {ms:.0f} ms, neighbour warnings {len(neighbour_warnings(centers, sample, rings))}"
                )
    failures = [s.reason for s in measure_spot_sizes(contrast, centers, rough, variants[0][1], valid) if not s.ok]
    if failures:
        print(f"  unmeasurable spots (first model): {len(failures)}; reasons: {sorted(set(failures))}")


if __name__ == "__main__":
    main()
