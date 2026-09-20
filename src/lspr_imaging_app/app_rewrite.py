"""Entry point for the rewrite-preview build (``lspri-evaluation-rewrite``
console script, ``src/main_rewrite.py``).

**Not the real app.** This exists so the maintainer can open a real window
and watch the rewrite's module/panel skeleton take shape while it's being
built, alongside the still-fully-functional stable app (``app.py`` /
``lspri-evaluation``) - see AGENTS.md and
``docs/rewrite_architecture_sketch_2026-09.md``. Every panel currently
shows an empty placeholder: the modules behind them
(:mod:`lspr_imaging_app.dataset`, :mod:`lspr_imaging_app.image_tools`,
:mod:`lspr_imaging_app.roi`, :mod:`lspr_imaging_app.analysis`,
:mod:`lspr_imaging_app.selection`) construct without error, but every
method that would actually load data, render an image, or compute anything
still raises ``NotImplementedError`` - this window exists to prove the
pieces wire together, not to be used for real evaluation work.

Deliberately skips almost everything ``app.py`` does (startup splash,
dataset restore flow, panel-layout persistence, window-state restore) -
none of that exists yet in the new architecture, and reproducing it here
would just be more code to throw away once the real shell (sketch §7
"Workflow shell") replaces this file.
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from lspr_ui import app_icon, set_active_theme, GRAY_DARK_THEME

from .analysis import AnalysisEngine
from .dataset import DatasetModule
from .gui.app_theme import apply_app_theme
from .image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
from .panels.histogram import HistogramPanel
from .panels.image import ImagePanel
from .panels.roi_table import RoiTablePanel
from .panels.sensorgram import SensorgramPanel
from .panels.spectra import SpectraPanel
from .panels.workflow import WorkflowPanel
from .roi import RoiToolbox
from .selection import SelectionModule
from .version_rewrite import rewrite_version_string


def _not_functional_banner() -> QWidget:
    banner = QLabel(
        "Scaffold preview only - nothing here loads data or computes anything yet.\n"
        "Every panel is real code wired to real modules, but every module method\n"
        "still raises NotImplementedError until it's actually built."
    )
    banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
    banner.setWordWrap(True)
    banner.setStyleSheet("padding: 10px; font-weight: 600;")
    return banner


def build_main_window() -> QMainWindow:
    """Construct every rewrite module and wire the panels to them, per the
    module boundaries in AGENTS.md / sketch §7. No module reaches into
    another's internals here - this function only connects the public,
    already-defined constructor seams."""
    dataset = DatasetModule()
    geometry = GeometryModule()
    mask = MaskModule()
    chromatic = ChromaticModule()
    background = BackgroundModule()
    roi_toolbox = RoiToolbox()
    selection = SelectionModule()
    analysis_engine = AnalysisEngine()

    # SelectionModule holds no RoiToolbox reference of its own (AGENTS.md,
    # "no module reaches into another's internals") - this is the one place
    # that connects RoiToolbox's roi_ids_renumbered signal to Selection's
    # remap_roi_ids(), so a delete-driven ROI renumber never leaves a stale
    # selected id behind (see roi/toolbox.py's module docstring, "Cross-
    # module consequence", and selection/module.py).
    roi_toolbox.roi_ids_renumbered.connect(selection.remap_roi_ids)

    image_panel = ImagePanel(dataset, geometry, mask, chromatic, background, roi_toolbox, selection)
    histogram_panel = HistogramPanel(image_panel)
    roi_table_panel = RoiTablePanel(roi_toolbox)
    spectra_panel = SpectraPanel(analysis_engine, roi_toolbox, selection)
    sensorgram_panel = SensorgramPanel(analysis_engine, roi_toolbox, dataset, selection)

    workflow = WorkflowPanel()
    workflow.addTab(image_panel, "Image")
    workflow.addTab(histogram_panel, "Histogram")
    workflow.addTab(roi_table_panel, "ROI / Groups")
    workflow.addTab(spectra_panel, "Spectra")
    workflow.addTab(sensorgram_panel, "Sensorgram")

    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(_not_functional_banner())
    layout.addWidget(workflow)

    window = QMainWindow()
    window.setWindowTitle(rewrite_version_string())
    window.setCentralWidget(central)
    window.resize(1100, 720)
    return window


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    app = QApplication(sys.argv)
    app.setApplicationName("LSPR Imaging (Rewrite Preview)")
    app.setApplicationVersion(rewrite_version_string())
    app.setWindowIcon(app_icon())
    set_active_theme(GRAY_DARK_THEME)
    apply_app_theme(app)

    window = build_main_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
