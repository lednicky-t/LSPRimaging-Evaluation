"""Where does the ROI / Group table spend its time? Headless (offscreen Qt, no window on screen), 160 ROIs in 16 groups with links, rectangles and maps.

Usage (from the repository root, on a quiet machine: a test run in the background makes every number 2-3 times larger)::

    python apps/LSPRi/eva/tools/roi_table_timing.py [N_ROIS] [--profile "operation name"]
    STEPS=8 python apps/LSPRi/eva/tools/roi_table_timing.py        # with 8 step columns of made-up numbers
    PROFSORT=tottime PROFN=30 python ... --profile "paint visible rows (grab)"
    LOG_DEBUG=1 python ...      # the app's DEBUG stage records are on (as in the log file); the last of each kind is printed at the end

Each operation is run several times; the minimum and median in ms are printed. With ``--profile`` a cProfile of that operation (functions by cumulative time, or
``PROFSORT``). The first lines are **hashes of the table's pixels in seven states**: a change that only makes the table faster must not change them (compare the lines before and
after; a rebuild that keeps Qt's current row differs from a reset one in that row's faint focus tint, so the check clears it first). Record and numbers:
``docs/roi_table_performance_2026-11-19.md``. Made 2026-11-19; the ROI table is unseen by eye, so the hashes are how a speed-up is shown to look the same.
"""
# ruff: noqa: E402  (the offscreen platform is chosen, and the application made, before the app's modules are imported)
import cProfile
import logging
import os
import pstats
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # apps/LSPRi/eva/tools/ -> the repository root
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps" / "LSPRi" / "eva" / "src"))

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelectionModel, QModelIndex

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from tests._qt_fonts import load_system_fonts

load_system_fonts()

import numpy as np

from lspr_imaging_app.domain.roi_model import RoiMask
from lspr_imaging_app.domain.roi_shapes import Circle, MaskShape, Rectangle
from lspr_imaging_app.image_tools import GeometryModule
from lspr_imaging_app.panels.roi_table.panel import RoiTablePanel
from lspr_imaging_app.panels.roi_table.rows import COLUMN_ID, COLUMN_SAMPLE, COLUMN_SHAPE, COLUMN_X
from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.selection import SelectionModule
from lspr_imaging_app.undo import undo_manager

N = 160
profile_op = None
args = sys.argv[1:]
if args and args[0].isdigit():
    N = int(args.pop(0))
if args and args[0] == "--profile":
    profile_op = args[1]

undo_manager.clear()
toolbox = RoiToolbox()
selection = SelectionModule()
toolbox.roi_ids_renumbered.connect(selection.remap_roi_ids)
n_ref = max(4, N // 10)
for i in range(N - n_ref):
    x, y = 40.0 + (i % 16) * 30.0, 40.0 + (i // 16) * 30.0
    if i % 17 == 5:
        toolbox.add_shaped_roi(x, y, Rectangle(14.0, 9.0, 20.0))
    elif i % 23 == 7:
        toolbox.add_shaped_roi(x, y, MaskShape(RoiMask(int(x) - 4, int(y) - 4, np.ones((8, 8), dtype=bool))))
    else:
        toolbox.add_roi(x, y, sample_diameter_px=10.0)
refs = [toolbox.add_shaped_roi(40.0 + j * 20.0, 600.0, Circle(8.0), role="reference") for j in range(n_ref)]
samples = [r.area_roi_id for r in toolbox.rois() if r.role == "sample"]
for k, ref in enumerate(refs):
    toolbox.link_references(samples[k * (len(samples) // n_ref):(k + 1) * (len(samples) // n_ref)], [ref])
ids = [r.area_roi_id for r in toolbox.rois()]
for g in range(16):
    toolbox.group_rois(ids[g * 10:(g + 1) * 10], f"Group {g + 1}")

geometry = GeometryModule()
panel = RoiTablePanel(toolbox, selection, geometry)
panel.resize(560, 700)
panel.show()
app.processEvents()
panel.refresh_now()
app.processEvents()
tree, model = panel._tree, panel._model

STEPS = int(os.environ.get("STEPS", "0"))
if STEPS:
    from lspr_imaging_app.panels.roi_table.model import StepCells
    from lspr_imaging_app.selection.step_table_module import CellValue, StepColumn

    class FakeCells(StepCells):
        def roi_cell(self, roi_id, ref):
            return CellValue(n=12, mean=590.0 + roi_id * 0.0137 + ref[1], sd=0.05 + (roi_id % 7) * 0.003, current=True, sem=0.014)

        def group_cell(self, key, members, ref):
            return CellValue(n=12, mean=590.0 + len(members) * 0.0137 + ref[1], sd=0.06, current=True, sem=0.017)

    model.set_step_columns([StepColumn((1, i), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(STEPS)], FakeCells())
    panel.refresh_now()
    app.processEvents()
    print(f"step columns on: {STEPS}")

import hashlib


def image_hash(label, clear=True):
    if clear:
        tree.selectionModel().setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)  # (a reset forgets the current row, an in-place update keeps it, with its faint focus tint: same state for both)
    app.processEvents()
    image = tree.grab().toImage()
    digest = hashlib.md5(image.bits().asstring(image.sizeInBytes())).hexdigest()[:12]
    print(f"  pixels[{label}] {digest}  ({image.width()}x{image.height()})")


print("pixel hashes of the table in several states:")
tree.verticalScrollBar().setValue(0)
image_hash("fresh")
selection.set_roi_selection(set(ids[10:40]))
image_hash("30 selected")
tree.verticalScrollBar().setValue(300)
image_hash("30 selected, scrolled")
tree.verticalScrollBar().setValue(0)
selection.set_roi_selection({ids[3]})
image_hash("1 selected")
model.set_sort(COLUMN_X, True)
panel.refresh_now()
image_hash("sorted by x descending")
model.set_sort(COLUMN_ID, False)
panel.refresh_now()
selection.set_roi_selection(set())
image_hash("back to #, nothing selected")
tree.setCurrentIndex(model.roi_index(ids[20], COLUMN_X))
tree.set_cell_range((ids[20], ids[21], ids[22]), (COLUMN_X, COLUMN_SAMPLE)) if hasattr(tree, "set_cell_range") else None
image_hash("picked cells", clear=False)
tree.set_cell_range((), ()) if hasattr(tree, "set_cell_range") else None
if os.environ.get("LOG_DEBUG"):
    class _Keep(logging.Handler):
        last: dict = {}

        def emit(self, record):
            event = getattr(record, "lspr_event", None)
            if event and event.startswith("roi_table."):
                self.last[event] = record.getMessage()

    _keep = _Keep(logging.DEBUG)
    _loggers = logging.getLogger("lspr_imaging_app.panels.roi_table")
    _loggers.setLevel(logging.DEBUG)
    _loggers.addHandler(_keep)

results = {}


def run(name, fn, repeat=7, before=None):
    times = []
    prof = cProfile.Profile() if profile_op == name else None
    for _ in range(repeat):
        if before:
            before()
        t0 = time.perf_counter()
        if prof:
            prof.enable()
        fn()
        if prof:
            prof.disable()
        times.append((time.perf_counter() - t0) * 1e3)
        app.processEvents()
    results[name] = (min(times), statistics.median(times))
    print(f"{name:<44} min {min(times):8.1f} ms   median {statistics.median(times):8.1f} ms")
    if prof:
        stats = pstats.Stats(prof)
        stats.sort_stats(os.environ.get("PROFSORT", "cumulative")).print_stats(int(os.environ.get("PROFN", "28")))


def scroll_to(value):
    tree.verticalScrollBar().setValue(value)


print(f"{len(ids)} ROIs, {len(toolbox.groups())} groups, {n_ref} references; rows in model: {len(model.rows())}")
run("refresh_now (rebuild)", panel.refresh_now)
run("paint visible rows (grab)", lambda: tree.grab(), repeat=9)
run("paint after scroll by 400 px (grab)", lambda: (scroll_to(400), tree.grab()), repeat=9, before=lambda: scroll_to(0))
run("select all ROIs (shared selection)", lambda: selection.set_roi_selection(set(ids)), before=lambda: selection.set_roi_selection(set()))
run("select 1 ROI (shared selection)", lambda: selection.set_roi_selection({ids[40]}), before=lambda: selection.set_roi_selection({ids[41]}))
run("click-select 1 row in the view", lambda: tree.selectionModel().select(model.roi_index(ids[60], COLUMN_ID), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows))
run("sort by x, then back", lambda: (model.set_sort(COLUMN_X, False), panel.refresh_now()), repeat=5)
run("sort by Type", lambda: (model.set_sort(COLUMN_SHAPE, False), panel.refresh_now()), repeat=5)
model.set_sort(COLUMN_ID, False)
panel.refresh_now()
run("edit offset of 40 selected ROIs (toolbox)", lambda: toolbox.set_rim(ids[:40], offset_px=float(np.random.randint(1, 9))), repeat=5)
movable = [r.area_roi_id for r in toolbox.rois() if isinstance(r.shape, (Circle, Rectangle)) and r.role == "sample"][:40]
run("edit x of 40 ROIs (toolbox.place_rois)", lambda: toolbox.place_rois(movable, x=float(np.random.randint(1, 500))), repeat=5)
run("rebuild after the edit", panel.refresh_now)
run("set_content only (model reset)", lambda: model.set_content(toolbox.rois_at(0), toolbox.groups(), toolbox.detection_settings(), panel._unit(), None))
from lspr_imaging_app.panels.roi_table.rows import build_roi_rows

run("build_roi_rows only (pure)", lambda: build_roi_rows(toolbox.rois_at(0), toolbox.groups(), toolbox.detection_settings()), repeat=15)
run("expandAll + spans (view part of rebuild)", lambda: (tree.expandAll(), [tree.setFirstColumnSpanned(h.row(), tree.rootIndex(), True) for h in model.group_headers()]))
run("refresh_column_visibility", tree.refresh_column_visibility, repeat=15)
run("fit_if_auto (step columns)", panel._step_controls.fit_if_auto, repeat=15)
if STEPS:
    run("model.step_texts of every step column", lambda: [model.step_texts(i) for i in range(STEPS)], repeat=15)
run("toggle Simple <-> Geometry view", lambda: (panel._view_actions["simple"].trigger(), panel._view_actions["geometry"].trigger()), repeat=5)
panel.close()
if os.environ.get("LOG_DEBUG"):
    print("last record of each kind:")
    for _message in _Keep.last.values():
        print("  " + _message)
