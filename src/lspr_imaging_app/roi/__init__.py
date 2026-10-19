"""ROI Toolbox (sketch §7 "ROI Toolbox", §10).

One backend, several front-door UI surfaces - not several independent
implementations of ROI/group logic. Follows ``docs/roi_system_roadmap.md``'s
``Pair`` vocabulary and geometry-type dispatcher; adopt that roadmap, don't
re-derive a new ROI model (CLAUDE.md, "Module boundaries").
"""


__all__ = ["RoiToolbox"]

# Qt classes load lazily (2026-10-19): importing a pure sub-module of this package (settings, geometry maths) must not
# load Qt, so analysis worker processes start fast and the pure files stay testable without Qt. Names stay importable
# from here exactly as before.
_LAZY = {
    "RoiToolbox": ".toolbox",
}


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
