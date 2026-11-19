"""Per-step cost of a ROI resize drag (2026-11-19), offscreen, on a 1200 x 1000 image with N ROIs. Standalone: nothing is written into the repo or a session.

Drives the real `RoiGestures` of a real `ImagePanel` (begin / update, as the mouse does) and times each part of a step: the toolbox write, the forced overlay redraw
(`_redraw_now`), the cursor follow, a viewport repaint, and a pass of the event loop. Run it before and after any change to the overlay or the gestures:
`python apps/LSPRi/eva/tools/bench_roi_resize_drag.py 162 1500`. Results and what they mean: `docs/roi_resize_drag_cost_2026-11-19.md`.
"""

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[4]  # apps/LSPRi/eva/tools/ -> the repo root
sys.path.insert(0, str(REPO))
from PyQt6 import QtWidgets  # noqa: E402

APP =QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from tests._paths import ensure_repo_paths  # noqa: E402

ensure_repo_paths()
sys.path.insert(0, str(REPO / "apps" / "LSPRi" / "eva" / "src"))

import numpy as np  # noqa: E402
import tifffile  # noqa: E402
from lspr_imaging_app.dataset import DatasetModule  # noqa: E402
from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.domain.roi_shapes import Rectangle  # noqa: E402
from lspr_imaging_app.image_tools import ActiveToolModule, BackgroundModule, ChromaticModule, GeometryModule, MaskModule, MaskScopeModule  # noqa: E402
from lspr_imaging_app.panels.image import ImagePanel  # noqa: E402
from lspr_imaging_app.roi import RoiToolbox  # noqa: E402
from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule  # noqa: E402
from lspr_imaging_app.undo import undo_manager  # noqa: E402

H, W = 1000, 1200
STEPS = 160


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.005)


def make_dataset(root: Path) -> ImageDataset:
    rng = np.random.default_rng(1)
    records = []
    for wavelength in (500.0, 550.0):
        path = root / f"c0_w{int(wavelength)}.tif"
        tifffile.imwrite(str(path), rng.uniform(100.0, 4000.0, size=(H, W)).astype(np.float32))
        records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def stats(values: list[float]) -> str:
    values = sorted(values)
    return f"median {statistics.median(values):6.2f}  p95 {values[int(0.95 * (len(values) - 1))]:6.2f}  max {values[-1]:6.2f}"


def run(n_rois: int, kind: str) -> None:
    undo_manager.clear()
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    dataset, toolbox, selection = DatasetModule(), RoiToolbox(), SelectionModule()
    panel = ImagePanel(
        dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), toolbox, selection, ActiveToolModule(), ReferenceFrameModule(),
        HighlightRangeModule(), mask_scope=MaskScopeModule(),
    )
    panel.resize(1100, 800)
    panel.show()
    dataset.load_dataset(make_dataset(Path(tmp.name)))
    pump(0.8)
    panel._plot.vb.setRange(xRange=(0.0, float(W)), yRange=(0.0, float(H)), padding=0.0)
    panel._tool_ribbon.set_category("ROIs")
    pump(0.2)
    cols = int(np.ceil(np.sqrt(n_rois * W / H)))
    pitch = W / (cols + 1)
    ids = []
    for i in range(n_rois):
        x, y = pitch * (1 + i % cols), pitch * (1 + i // cols)
        ids.append(toolbox.add_shaped_roi(x, y, Rectangle(14.0, 9.0, 0.0)) if kind == "rectangle" else toolbox.add_roi(x, y, sample_diameter_px=12.0))
    pump(0.5)
    undo_manager.clear()
    target = ids[len(ids) // 2]
    selection.set_roi_selection({target})
    pump(0.2)

    gestures = panel._interaction._roi_gestures
    queries = panel._roi_queries
    index = [r.area_roi_id for r in toolbox.rois()].index(target)
    cx, cy = (float(v) for v in queries.view_centers((queries._current_cube(), queries._current_wavelength()), queries.current_affine())[index])

    timings: dict[str, list[float]] = {"toolbox": [], "overlay": [], "cursor": [], "repaint": [], "update_total": [], "events": []}
    real_overlay = gestures._redraw_overlay

    def timed_overlay() -> None:
        t = time.perf_counter()
        real_overlay()
        timings["overlay"].append((time.perf_counter() - t) * 1000.0)

    gestures._redraw_overlay = timed_overlay
    for name in ("resize_rois", "resize_rectangles"):

        def make(orig):
            def wrapper(*a, **k):
                t = time.perf_counter()
                try:
                    return orig(*a, **k)
                finally:
                    timings["toolbox"].append((time.perf_counter() - t) * 1000.0)

            return wrapper

        setattr(toolbox, name, make(getattr(toolbox, name)))

    assert gestures.begin(cx + (6.0 if kind == "circle" else 7.0), cy, additive=False), "the drag was not claimed"
    interaction = panel._interaction
    x = y = 0.0
    for k in range(STEPS):
        grow = 4.0 * np.sin(k / STEPS * np.pi * 2.0)
        angle = np.radians(k * 3.0)
        x = cx + (6.0 + grow) * np.cos(angle)
        y = cy + (6.0 + grow) * np.sin(angle) * (1.0 if kind == "circle" else 0.0)
        t0 = time.perf_counter()
        gestures.update(x, y)
        t1 = time.perf_counter()
        interaction._follow_held_cursor(x, y)
        t2 = time.perf_counter()
        panel._view.viewport().repaint()
        t3 = time.perf_counter()
        APP.processEvents()
        t4 = time.perf_counter()
        timings["update_total"].append((t1 - t0) * 1000.0)
        timings["cursor"].append((t2 - t1) * 1000.0)
        timings["repaint"].append((t3 - t2) * 1000.0)
        timings["events"].append((t4 - t3) * 1000.0)
    gestures.end(x, y, additive=False)
    print(f"--- {kind}, {n_rois} ROIs on a {W}x{H} image (ms per drag step, {STEPS} steps)")
    for name in ("update_total", "toolbox", "overlay", "cursor", "repaint", "events"):
        print(f"   {name:13s} {stats(timings[name])}")
    panel._renderer.stop()
    panel.close()
    tmp.cleanup()


if __name__ == "__main__":
    counts = [int(a) for a in sys.argv[1:]] or [162, 1500]
    for n in counts:
        run(n, "circle")
    run(counts[0], "rectangle")
