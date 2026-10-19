"""Geometry sub-module (sketch §7 "Image Tools" > Geometry, §10)."""

from .model import CropDefinition, GeometrySettings

__all__ = ["CropDefinition", "GeometrySettings", "GeometryModule"]

# Qt classes load lazily (2026-10-19): importing a pure sub-module of this package (settings, geometry maths) must not
# load Qt, so analysis worker processes start fast and the pure files stay testable without Qt. Names stay importable
# from here exactly as before.
_LAZY = {
    "GeometryModule": ".module",
}


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
