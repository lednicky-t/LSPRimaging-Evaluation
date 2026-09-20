"""Shared cross-module instrumentation (docs/rewrite_architecture_sketch_2026-09.md §3)."""

from .instrumented import DiagnosticsHub, diagnostics_hub, instrumented

__all__ = ["DiagnosticsHub", "diagnostics_hub", "instrumented"]
