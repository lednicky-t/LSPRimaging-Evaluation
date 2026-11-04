"""Map every small feature of a real dataset without ROIs (2026-11-03). Lab for `image_tools/mask/feature_map.py` (record:
`docs/roi_feature_detection_2026-10-29.md` s.10). Read-only for the dataset and the session (modules built headless, autosave
suspended); writes only into --out. The session is used for the geometry (rotation, crop) and the chromatic correction; its ROIs only
label the table and choose the close-ups, they are not used to find anything.

    python tools/feature_map_lab.py --dataset "<x.ome.zarr>" --session "<x.ome.zarr>/sessions/<id>" --cubes 12 ^
        --rois 105 37 53 69 --out "<folder>" [--kinds dirt_dark structure_missing] [--dirt-min 0.02] [--structure-min 0.1]

Writes features.csv (one row per feature), mask.png (the enabled kinds, raw pixels, 255 = masked; Mask -> Load REPLACES the mask at
that frame and scope), field.png (the whole picture, kinds in colour) and closeups.png (needs matplotlib).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
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
from scipy import ndimage  # noqa: E402

import analysis_stage_timing as stage  # noqa: E402
from lspr_imaging_app import plane_reader  # noqa: E402
from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools import preprocess as pp  # noqa: E402
from lspr_imaging_app.image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix  # noqa: E402
from lspr_imaging_app.image_tools.mask import feature_map as fm  # noqa: E402
from lspr_imaging_app.image_tools.mask.io import write_mask_image  # noqa: E402

_COLOURS = {fm.DIRT_DARK: (0.1, 0.4, 1.0), fm.DIRT_BRIGHT: (1.0, 0.85, 0.0), fm.STRUCTURE_MISSING: (1.0, 0.1, 0.8), fm.STRUCTURE_EXTRA: (0.1, 0.9, 0.2), fm.DIRT_FAINT: (0.55, 0.95, 1.0)}
_LEGEND = "blue = dirt dark, yellow = dirt bright, magenta = nanostructure missing, green = nanostructure extra, light cyan = faint dirt (not masked by default)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--session", required=True, type=Path)
    ap.add_argument("--cubes", type=int, default=12, help="the first N spectral cubes are averaged (features are static)")
    ap.add_argument("--rois", type=int, nargs="*", default=None, help="ROI ids for the close-ups (only to choose where to look)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--kinds", nargs="*", default=sorted(fm.FeatureMapSettings.kinds), choices=fm.KINDS)
    ap.add_argument("--dirt-min", type=float, default=fm.FeatureMapSettings.dirt_min)
    ap.add_argument("--structure-min", type=float, default=fm.FeatureMapSettings.structure_min)
    ap.add_argument("--certainty", type=float, default=fm.FeatureMapSettings.certainty)
    ap.add_argument("--dump", action="store_true", help="also write logs.npy (the aligned log stack) for fast experiments")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    settings = fm.FeatureMapSettings(dirt_min=args.dirt_min, structure_min=args.structure_min, certainty=args.certainty, kinds=frozenset(args.kinds))

    app = QApplication.instance() or QApplication([])
    assert app is not None
    dataset = stage.load_one(args.dataset)
    modules, _ = stage.build_modules(dataset, args.session)
    cubes = list(modules.dataset.spectral_cubes())[: args.cubes]
    wls = stage.spectral_wavelengths(modules, cubes[0])
    ref_wl = min(wls, key=lambda w: abs(w - 610.0))
    records = io.dataset_record_map(dataset)
    geometry = modules.geometry.settings()
    background = dataclasses.replace(modules.background.settings(), flatten_background_enabled=False)  # the map takes shading out itself
    started = time.perf_counter()

    means: dict[float, np.ndarray] = {}
    for wl in wls:
        acc = None
        for cube in cubes:
            raw = plane_reader.load_image_array_uncached(str(records[(cube, wl)].path))
            img = pp.apply_preprocessing(raw, geometry, background).astype(np.float64)
            acc = img if acc is None else acc + img
        means[wl] = acc / len(cubes)
    shape = means[ref_wl].shape
    print(f"mean of {len(cubes)} cubes x {len(wls)} wavelengths: {time.perf_counter() - started:.0f} s", flush=True)

    # every wavelength onto the reference wavelength's pixels (chromatic correction)
    affine_ref = modules.chromatic.affine_for((cubes[0], ref_wl))
    to_ref = np.vstack([invert_affine_matrix(affine_ref), [0.0, 0.0, 1.0]])
    oy, ox = np.indices(shape, dtype=np.float64)
    logs = np.empty((len(wls), *shape), dtype=np.float32)
    for j, wl in enumerate(wls):
        m = np.vstack([modules.chromatic.affine_for((cubes[0], wl)), [0.0, 0.0, 1.0]]) @ to_ref
        sx, sy = m[0, 0] * ox + m[0, 1] * oy + m[0, 2], m[1, 0] * ox + m[1, 1] * oy + m[1, 2]
        img = ndimage.map_coordinates(means[wl], [sy, sx], order=1, mode="constant", cval=np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            logs[j] = np.log(np.where(img > 0, img, np.nan))
    if args.dump:
        np.save(args.out / "logs.npy", logs)
    print(f"aligned: {time.perf_counter() - started:.0f} s", flush=True)

    t = time.perf_counter()
    result = fm.feature_map(logs, settings)
    print(f"map: {len(result.disks)} disks found, {len(result.features)} features, {time.perf_counter() - t:.1f} s", flush=True)

    rois = {r.area_roi_id: r for r in modules.roi_toolbox.rois()}
    roi_xy = {rid: apply_affine_to_points(np.array([[r.center_x, r.center_y]]), affine_ref)[0] for rid, r in rois.items()}

    def nearest_roi(x: float, y: float) -> int:
        return min(roi_xy, key=lambda k: np.hypot(roi_xy[k][0] - x, roi_xy[k][1] - y))

    rows = [{"near_roi": nearest_roi(*f.centroid), "kind": f.kind, "area_px": f.area_px, "size_percent": round(100 * f.size, 1),
             "peak_z": round(f.peak_z, 1), "x": round(f.centroid[0], 1), "y": round(f.centroid[1], 1)} for f in result.features]
    with open(args.out / "features.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["near_roi", "kind", "area_px", "size_percent", "peak_z", "x", "y"])
        writer.writeheader()
        writer.writerows(rows)
    for kind in fm.KINDS:
        print(f"{fm.KIND_LABELS[kind]:<32s} {sum(r['kind'] == kind for r in rows):4d} features, {int(result.kind_masks[kind].sum()):6d} px")
    if args.rois:
        for rid in args.rois:
            near = [f"{r['kind']} {r['size_percent']:+.1f}% ({r['area_px']} px)" for r in rows if r["near_roi"] == rid]
            print(f"  near ROI {rid}: {', '.join(near) if near else 'nothing'}")
    raw_mask = pp.histogram_highlight_mask_to_raw(result.mask.astype(np.float32), 0.5, 1.5, modules.dataset.raw_plane_shape(), geometry)
    write_mask_image(raw_mask, args.out / "mask.png")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return 0
    picture = means[ref_wl]
    lo, hi = np.nanpercentile(picture, [1, 99.5])
    overlay = np.zeros(shape + (4,))
    for kind, colour in _COLOURS.items():
        overlay[result.kind_masks[kind]] = (*colour, 0.65)
    fig, ax = plt.subplots(figsize=(shape[1] / 60, shape[0] / 60))
    ax.imshow(picture, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest")
    ax.imshow(overlay, interpolation="nearest")
    for rid, (x, y) in roi_xy.items():
        ax.text(x, y, str(rid), color="white", fontsize=6, ha="center", va="center")
    ax.set_title(_LEGEND, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(args.out / "field.png", dpi=90)
    plt.close(fig)
    print("whole field: field.png")

    chosen = [r for r in (args.rois or []) if r in roi_xy]
    if chosen:
        half = int(2.2 * float(np.median([d.radius for d in result.disks])))
        fig, axes = plt.subplots(len(chosen), 4, figsize=(11, 2.8 * len(chosen)), squeeze=False)
        for row, rid in enumerate(chosen):
            x, y = (int(round(v)) for v in roi_xy[rid])
            box = (slice(max(y - half, 0), y + half + 1), slice(max(x - half, 0), x + half + 1))
            text = "; ".join(f"{fm.KIND_LABELS[r['kind']].split(' (')[0]} {r['size_percent']:+.0f}%" for r in rows if r["near_roi"] == rid) or "nothing"
            for col, (data, title, kw) in enumerate((
                (picture[box], f"ROI {rid}", dict(cmap="gray", vmin=lo, vmax=hi)),
                (result.grey_z[box], "dirt score (blue darker)", dict(cmap="RdBu_r", vmin=-10, vmax=10)),
                (result.structure_z[box], "nanostructure score (blue = missing)", dict(cmap="RdBu_r", vmin=-10, vmax=10)),
                (picture[box], text, dict(cmap="gray", vmin=lo, vmax=hi)),
            )):
                ax = axes[row, col]
                ax.imshow(data, interpolation="nearest", **kw)
                if col == 3:
                    ax.imshow(overlay[box], interpolation="nearest")
                ax.set_title(title, fontsize=7)
                ax.set_xticks([])
                ax.set_yticks([])
        fig.suptitle(_LEGEND, fontsize=9)
        fig.tight_layout()
        fig.savefig(args.out / "closeups.png", dpi=70)
        print("close-ups: closeups.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
