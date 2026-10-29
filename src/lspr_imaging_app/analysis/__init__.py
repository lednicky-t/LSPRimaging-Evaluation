"""The analysis package.

The scan (analysis step 1, ``scan*.py``, ``frame_reduce.py``, ``scan_store.py`` = ``scan.h5``) turns pixels into numbers per ROI, cube,
wavelength and side; the Spectra and Sensorgram panels show them. Layers 2-3 of ``docs/analysis_pipeline_layers.md`` (formula spectrum,
fit, metric) live in ``query.py`` and Pillar II in ``statistics.py`` - both pure and Qt-free; ``settings_module.py`` owns the settings
that drive them. ``reduction.py`` turns masked pixel values into scalar results.

``tasks.compute_cell`` (with ``provenance.py``) is the independent, slower **reference implementation** the scan is checked against
(``tools/scan_golden_check.py``, the tests). The old ``AnalysisEngine`` that drove it (``data.h5``, recompute planner) was removed on
2026-10-29: no UI ever started a run (``docs/old_generation_cutover_plan_2026-10-29.md``, phase 2e).

Imported lazily (2026-10-19): the Qt class below loads only when asked for, so the pure files of this package (``frame_reduce``,
``reduction``, ``query``, ...) import without Qt, as CLAUDE.md's testing rule requires, and an analysis worker process starts
without loading Qt.
"""

from typing import TYPE_CHECKING

__all__ = ["AnalysisSettingsModule"]

if TYPE_CHECKING:
    from .settings_module import AnalysisSettingsModule


def __getattr__(name: str):
    if name == "AnalysisSettingsModule":
        from .settings_module import AnalysisSettingsModule

        return AnalysisSettingsModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
