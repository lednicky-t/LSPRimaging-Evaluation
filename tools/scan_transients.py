"""Scan an LSPRi dataset for transients (flowing particles, hairs, bubbles) and print the event list.

Read-only: never writes into the dataset. The same code the Mask tab's "Scan" will use
(`lspr_imaging_app.image_tools.mask.transient_scan`), run from a terminal so the method can be checked on real data without the
app. Method and measurements: docs/auto_transient_mask_exploration_2026-10-16.md; plan: docs/auto_transient_scan_and_mask_plan_2026-10-16.md.

    python tools/scan_transients.py "D:/data/run/images" --stride 3
    python tools/scan_transients.py "D:/data/run/images" --cubes 55:70 --stride 1 --csv events.csv

An event is one object in one frame. `particle` = compact, `global` = large (a pointer only), `artefact` = it did not move
between consecutive frames, or sits alone on the image border. Particles seen in consecutive frames share a passage number and a speed.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[1] / "src"
for extra in (APP_SRC, *(APP_SRC.parents[3] / "packages" / name / "src" for name in ("lspr_ui", "lspr_core", "lspr_io"))):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from lspr_imaging_app.dataset import io  # noqa: E402
from lspr_imaging_app.image_tools.mask.transient_detection import (  # noqa: E402
    KIND_GLOBAL,
    KIND_PARTICLE,
    KIND_ARTEFACT,
    DetectionParams,
)
from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, scan_transients  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="the dataset folder (the one holding the TIFF stack or the .ome.zarr)")
    parser.add_argument("--stride", type=int, default=3, help="check every n-th wavelength (default 3; 1 = every frame)")
    parser.add_argument("--sigma", type=float, default=60.0, help="how many noise sigma deep an object must be (default 60)")
    parser.add_argument("--max-area", type=int, default=3000, help="particle area limit in px; larger is 'global' (default 3000)")
    parser.add_argument("--bin", type=int, default=4, help="screening bin factor (default 4)")
    parser.add_argument("--cubes", type=str, default=None, help="cube range A:B, inclusive (default: all)")
    parser.add_argument("--workers", type=int, default=None, help="threads per pool (default: all cores but two, at most 6)")
    parser.add_argument("--no-fast-decoder", action="store_true", help="read TIFF with tifffile instead of the native decoder")
    parser.add_argument("--csv", type=Path, default=None, help="also write every event to this CSV file")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset = io.load_dataset(args.folder)
    if isinstance(dataset, io.DatasetLoadChoice):
        dataset = dataset.candidates[0]
        print(f"The folder holds several datasets; using {dataset.source_format}: {dataset.folder}")
    records = io.dataset_record_map(dataset)
    frame_index: dict[int, list[float]] = {}
    for cube, wavelength in records:
        frame_index.setdefault(int(cube), []).append(float(wavelength))

    def load_plane(cube: int, wavelength: float):
        return io.load_image_array_uncached(str(records[(cube, wavelength)].path))

    cube_range = None
    if args.cubes:
        low, high = (int(part) for part in args.cubes.split(":"))
        cube_range = (low, high)
    params = ScanParams(
        detection=DetectionParams(sigma_factor=args.sigma, max_particle_area_px=args.max_area, bin_factor=args.bin),
        stride=args.stride,
        cube_range=cube_range,
        **({} if args.workers is None else {"workers": args.workers}),
    )

    last_percent = [-1]

    def progress(fraction: float, message: str) -> None:
        percent = int(fraction * 100)
        if percent != last_percent[0] and percent % 5 == 0:
            last_percent[0] = percent
            print(f"  {percent:3d} %  {message}", flush=True)

    worker_init = None if args.no_fast_decoder else (lambda: io.use_fast_tiff_decoder(True))
    result = scan_transients(frame_index, load_plane, params, progress=progress, worker_init=worker_init)

    print(
        f"\n{result.frames_in_range} frames in range, {result.frames_screened} screened, {result.frames_hit} with something, "
        f"{len(result.events)} events, {len(result.failed_frames)} unreadable, {result.seconds:.1f} s "
        f"(screen {result.stage_seconds['screen']:.1f} s, expand {result.stage_seconds['expand']:.1f} s, "
        f"confirm {result.stage_seconds['confirm']:.1f} s, fill {result.stage_seconds['fill']:.1f} s); "
        f"{sum(e.inferred for e in result.events)} events found by gap filling"
    )
    passages: dict[int, list] = {}
    for event in result.events:
        if event.passage_id is not None:
            passages.setdefault(event.passage_id, []).append(event)
    print(f"\nPassages (a particle seen in consecutive frames): {len(passages)}")
    for number, events in sorted(passages.items()):
        events.sort(key=lambda e: e.frame)
        steps = [e.step_px for e in events if e.step_px]
        areas = [e.area_px for e in events]
        first, last = events[0].frame, events[-1].frame
        print(
            f"  #{number:<3d} cube {first[0]} {first[1]:g} nm -> cube {last[0]} {last[1]:g} nm  {len({e.frame for e in events}):2d} frames  "
            f"area {min(areas)}-{max(areas)} px  step {min(steps):.0f}-{max(steps):.0f} px/frame"
            if steps
            else f"  #{number:<3d} cube {first[0]} {first[1]:g} nm  {len({e.frame for e in events})} frames"
        )
    singles = [e for e in result.events if e.kind == KIND_PARTICLE and e.passage_id is None]
    print(f"\nParticle-sized objects that were not linked to a neighbouring frame: {len(singles)}")
    for e in singles[:30]:
        print(f"  cube {e.frame[0]} {e.frame[1]:g} nm  area {e.area_px} px at ({e.centroid_xy[0]:.0f}, {e.centroid_xy[1]:.0f})  {e.z:.0f} sigma")
    for kind in (KIND_GLOBAL, KIND_ARTEFACT):
        chosen = [e for e in result.events if e.kind == kind]
        print(f"\n{kind}: {len(chosen)} events in {len({e.frame for e in chosen})} frames")
        for e in chosen[:12]:
            print(f"  cube {e.frame[0]} {e.frame[1]:g} nm  area {e.area_px} px at ({e.centroid_xy[0]:.0f}, {e.centroid_xy[1]:.0f})  {e.z:.0f} sigma")
    if result.failed_frames:
        print(f"\nUnreadable frames: {result.failed_frames[:20]}")

    if args.csv is not None:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["cube", "wavelength_nm", "kind", "area_px", "x", "y", "x0", "y0", "x1", "y1", "depth", "sigma", "passage", "step_px", "frame_noise_pct", "inferred"])
            for e in result.events:
                writer.writerow(
                    [e.frame[0], e.frame[1], e.kind, e.area_px, f"{e.centroid_xy[0]:.1f}", f"{e.centroid_xy[1]:.1f}", *e.bbox_xyxy,
                     f"{e.depth:.3f}", f"{e.z:.1f}", e.passage_id or "", f"{e.step_px:.1f}" if e.step_px else "", f"{e.frame_sigma * 100:.2f}", int(e.inferred)]
                )
        print(f"\nWrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
