"""Analysis Engine (sketch §5, §6, §7 "Analysis Engine", §10).

Owns the per-cell-provenance store and the recompute planner. Only ever
computes anything in response to an explicit ``run_analysis(scope)`` call -
never automatically (AGENTS.md, "What NOT to do without checking in again
first"; sketch §7, "Confirmed 2026-09-20"). Also owns turning masked pixel
values into scalar results (`reduction.py`, moved here from `roi/` on
2026-09-21 - see that file's docstring).
"""

from .engine import AnalysisEngine

__all__ = ["AnalysisEngine"]
