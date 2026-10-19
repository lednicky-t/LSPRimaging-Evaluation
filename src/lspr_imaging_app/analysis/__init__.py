"""Analysis Engine (sketch §5, §6, §7 "Analysis Engine", §10).

Owns the per-cell-provenance store and the recompute planner. Only ever
computes anything in response to an explicit ``run_analysis(scope)`` call -
never automatically (CLAUDE.md, "What NOT to do without checking in again
first"; sketch §7, "Confirmed 2026-09-20"). Also owns turning masked pixel
values into scalar results (`reduction.py`, moved here from `roi/` on
2026-09-21 - see that file's docstring).

Layers 2-3 of `docs/analysis_pipeline_layers.md` (formula spectrum, fit,
metric) live in `query.py` and Pillar II in `statistics.py` - both pure and
Qt-free; `settings_module.py` owns the settings that drive them.

Imported lazily (2026-10-19): the two Qt classes below load only when asked for, so the pure files of this package
(``frame_reduce``, ``reduction``, ``query``, ...) import without Qt, as CLAUDE.md's testing rule requires, and an analysis
worker process starts without loading Qt.
"""

from typing import TYPE_CHECKING

__all__ = ["AnalysisEngine", "AnalysisSettingsModule"]

if TYPE_CHECKING:
    from .engine import AnalysisEngine
    from .settings_module import AnalysisSettingsModule


def __getattr__(name: str):
    if name == "AnalysisEngine":
        from .engine import AnalysisEngine

        return AnalysisEngine
    if name == "AnalysisSettingsModule":
        from .settings_module import AnalysisSettingsModule

        return AnalysisSettingsModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
