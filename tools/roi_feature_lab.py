"""Find small features on / around the ROIs of a real session, from one frame's pixels alone (2026-10-29).

Lab for `image_tools/mask/roi_features.py` (method, tests and the real-data results: `docs/roi_feature_detection_2026-10-29.md`).
Read-only with respect to the dataset and the session: the modules are built headless with autosave suspended, nothing is written
except into the output folder.

    python tools/roi_feature_lab.py --dataset "D:/data/run/images/x.ome.zarr" --session "D:/data/run/images/x.ome.zarr/sessions/2026-10-22_172035" ^
        --cube 0 --wavelength 610 --rois 139 105 17 4 1 117 151 69 9 76 --out "D:/lab_out"

Writes into --out:
  features_c<cube>_<wl>nm.csv   one row per ROI that has a feature: counts, areas, the strongest score, the estimated effect on its S/R ratio
  mask_c<cube>_<wl>nm.png       the features of the ROIs as one mask image (255 = masked; ROIs over --max-share left out), in raw pixels, for inspection or
                                for Mask -> Load. NOTE: Load REPLACES the mask in effect at the frame and scope it is loaded at (it does
                                not add to it, and it is not undoable): do not load it where a mask you want to keep is stored.
  montage_c<cube>_<wl>nm.png    image / score / mask side by side for --rois (or the 12 ROIs with the largest effect); needs matplotlib

The detector needs the ROIs of one array in the frame (at least 8) to know what a ROI normally looks like; a frame is analysed as a whole
(about 16 s for 160 ROIs on one core, 92 ms per ROI for the expensive step).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_SRC = Path(__file__).resolve().parents[1] / "src"
for extra in (APP_SRC, *(APP_SRC.parents[3] / "packages" / name / "src" for name in ("lspr_ui", "lspr_core", "lspr_io"))):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import analysis_stage_timing as stage  # noqa: E402  (its dataset / session loading, kept in one place)
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools import preprocess as pp  # noqa: E402
from lspr_imaging_app.image_tools.mask import roi_features as rf  # noqa: E402
from lspr_imaging_app.image_tools.mask.io import write_mask_image  # noqa: E402
from lspr_imaging_app.roi import rasterize as rz  # noqa: E402

_FIT_MARGIN_PX = 6.0  # the radial model reaches this far beyond the reference ring's outer edge


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", required=True, type=Path, help="the OME-Zarr folder or the TIFF folder")
    ap.add_argument("--session", required=True, type=Path, help="the session folder (contains session/session.json or session.json)")
    ap.add_argument("--cube", type=int, default=None, help="cube to analyse (default: the first)")
    ap.add_argument("--wavelength", type=float, default=None, help="nm (default: the middle wavelength of the cube)")
    ap.add_argument("--rois", type=int, nargs="*", default=None, help="ROI ids for the montage")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--z-seed", type=float, default=rf.FeatureSettings.z_seed)
    ap.add_argument("--z-grow", type=float, default=rf.FeatureSettings.z_grow)
    ap.add_argument("--max-width", type=float, default=rf.FeatureSettings.max_width_px)
    ap.add_argument("--modes", type=int, default=rf.FeatureSettings.modes)
    ap.add_argument("--max-share", type=float, default=0.4, help="a ROI whose features would cover more than this share of its sample disk or reference ring is left out of the mask file (too little would be left to measure with) and listed")
    args = ap.parse_args()
    settings = rf.FeatureSettings(z_seed=args.z_seed, z_grow=args.z_grow, max_width_px=args.max_width, modes=args.modes)

    app = QApplication.instance() or QApplication([])
    assert app is not None  # the modules need a Qt application object; this keeps the one made here referenced for the whole run
    dataset = stage.load_one(args.dataset)
    modules, state = stage.build_modules(dataset, args.session)
    cubes = list(modules.dataset.spectral_cubes())
    cube = cubes[0] if args.cube is None else args.cube
    wavelengths = stage.spectral_wavelengths(modules, cube)
    target = args.wavelength if args.wavelength is not None else wavelengths[len(wavelengths) // 2]
    wavelength = min(wavelengths, key=lambda w: abs(w - target))
    print(f"frame: cube {cube}, {wavelength:.0f} nm")

    geometry, background = modules.geometry.settings(), modules.background.settings()
    det = modules.roi_toolbox.detection_settings()
    rois = {r.area_roi_id: r for r in modules.roi_toolbox.rois()}
    inner_default, outer_default = det.reference_inner_diameter_px, det.reference_outer_diameter_px
    records = io.dataset_record_map(dataset)
    raw = io.load_image_array_uncached(str(records[(cube, wavelength)].path))
    image = pp.apply_preprocessing(raw, geometry, background, rois=list(rois.values()), mask_settings=det, external_mask=None)
    affine = modules.chromatic.affine_for((cube, wavelength))
    shape = image.shape[:2]

    started = time.perf_counter()
    patches: dict[int, rf.RoiPatch] = {}
    windows: dict[int, tuple] = {}
    for rid, roi in sorted(rois.items()):
        sample_full = rz.rasterize_sample(roi, shape, affine)
        ring_full = rz.rasterize_reference(roi, shape, affine, default_inner_diameter_px=inner_default, default_outer_diameter_px=outer_default)
        ys, xs = np.nonzero(sample_full)
        if xs.size == 0:
            continue
        outer = float(roi.reference_outer_diameter_px or outer_default) / 2.0
        radius = outer + _FIT_MARGIN_PX
        half = int(np.ceil(radius)) + 6
        cx_s, cy_s = float(xs.mean()), float(ys.mean())
        y0, y1 = max(int(round(cy_s)) - half, 0), min(int(round(cy_s)) + half + 1, shape[0])
        x0, x1 = max(int(round(cx_s)) - half, 0), min(int(round(cx_s)) + half + 1, shape[1])
        window = image[y0:y1, x0:x1]
        valid = np.isfinite(window)
        windows[rid] = (window, sample_full[y0:y1, x0:x1], (ring_full & ~sample_full)[y0:y1, x0:x1], (y0, y1, x0, x1))
        patch = rf.fit_patch(np.where(valid, window, 0.0), valid, (cx_s - x0, cy_s - y0), radius)
        if patch is not None:
            patches[rid] = patch
    print(f"fitted {len(patches)} of {len(rois)} ROIs in {time.perf_counter() - started:.1f} s")
    try:
        out = rf.detect(patches, {rid: windows[rid][1] | windows[rid][2] for rid in patches}, settings)
    except rf.NotEnoughRois as exc:
        print(f"Cannot judge: {exc}")
        return 2
    print(f"scored and searched in {time.perf_counter() - started:.1f} s (all steps)")

    args.out.mkdir(parents=True, exist_ok=True)
    tag = f"c{cube}_{wavelength:.0f}nm"
    mask_full = np.zeros(shape, dtype=bool)
    rows = []
    effects: dict[int, float] = {}
    shares: dict[int, tuple[float, float]] = {}
    too_much: list[int] = []
    for rid, (score, found) in sorted(out.items()):
        window, sample, ring, (y0, y1, x0, x1) = windows[rid]
        es, er = rf.ratio_effect(window, score.deviation, sample, ring, found.mask)
        effects[rid] = es - er
        shares[rid] = (rf.masked_share(found.mask, sample), rf.masked_share(found.mask, ring))
        if found.features:
            if max(shares[rid]) > args.max_share:
                too_much.append(rid)  # reported, not masked: masking would leave too few pixels for a measurement
            else:
                mask_full[y0:y1, x0:x1] |= found.mask
            rows.append({
                "roi": rid, "features": len(found.features), "dark": sum(f.sign < 0 for f in found.features), "bright": sum(f.sign > 0 for f in found.features),
                "area_px": sum(f.area_px for f in found.features), "max_abs_z": round(max(f.peak_z for f in found.features), 1),
                "masked_px": int(found.mask.sum()), "effect_on_ratio_percent": round(100.0 * (es - er), 3),
                "sample_masked_percent": round(100.0 * shares[rid][0], 1), "ring_masked_percent": round(100.0 * shares[rid][1], 1),
                "in_mask_file": rid not in too_much,
            })
    with open(args.out / f"features_{tag}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["roi"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} of {len(out)} ROIs have at least one feature; {int(mask_full.sum())} px in the mask; table: features_{tag}.csv")
    if too_much:
        print(f"left out of the mask file (more than {args.max_share:.0%} of the sample disk or ring would go): ROIs {too_much}")

    raw_shape = modules.dataset.raw_plane_shape()
    raw_mask = pp.histogram_highlight_mask_to_raw(mask_full.astype(np.float32), 0.5, 1.5, raw_shape, geometry)  # processed -> raw pixels, as the app does for a selection
    write_mask_image(raw_mask, args.out / f"mask_{tag}.png")
    print(f"mask image: mask_{tag}.png ({int(raw_mask.sum())} px in raw pixels)")

    chosen = args.rois if args.rois else [rid for rid, _ in sorted(effects.items(), key=lambda kv: -abs(kv[1]))[:12]]
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed: no montage written")
        return 0
    chosen = [rid for rid in chosen if rid in out]
    fig, axes = plt.subplots(len(chosen), 3, figsize=(10.5, 3.5 * len(chosen)), squeeze=False)
    for row, rid in enumerate(chosen):
        window, sample, ring, _ = windows[rid]
        score, found = out[rid]
        lo, hi = np.nanpercentile(window, [1, 99.5])
        axes[row, 0].imshow(window, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest")
        axes[row, 0].set_title(f"ROI {rid}", fontsize=9)
        axes[row, 1].imshow(score.z, cmap="RdBu_r", vmin=-8, vmax=8, interpolation="nearest")
        axes[row, 1].set_title("score z (red brighter, blue darker)", fontsize=8)
        axes[row, 2].imshow(window, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest")
        overlay = np.zeros(window.shape + (4,))
        overlay[found.mask] = (1.0, 0.1, 0.1, 0.55)
        axes[row, 2].imshow(overlay, interpolation="nearest")
        axes[row, 2].set_title(
            f"{len(found.features)} feature(s); masks {100 * shares[rid][0]:.0f} % of the disk, {100 * shares[rid][1]:.0f} % of the ring; ratio effect {100 * effects[rid]:+.2f} %"
            + ("; LEFT OUT" if rid in too_much else ""), fontsize=7,
        )
        for ax in axes[row]:
            ax.contour(sample.astype(float), [0.5], colors="cyan", linewidths=0.6)
            ax.contour(ring.astype(float), [0.5], colors="gold", linewidths=0.6)
            ax.set_xticks([])
            ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(args.out / f"montage_{tag}.png", dpi=70)
    plt.close(fig)
    print(f"montage: montage_{tag}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
