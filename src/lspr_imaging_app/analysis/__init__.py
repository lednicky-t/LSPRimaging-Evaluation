"""Analysis Engine (sketch §5, §6, §7 "Analysis Engine", §10).

Owns the per-cell-provenance store and the recompute planner. Only ever
computes anything in response to an explicit ``run_analysis(scope)`` call -
never automatically (AGENTS.md, "What NOT to do without checking in again
first"; sketch §7, "Confirmed 2026-09-20").
"""

from .engine import AnalysisEngine

__all__ = ["AnalysisEngine"]
