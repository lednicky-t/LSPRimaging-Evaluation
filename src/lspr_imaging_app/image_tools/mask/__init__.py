"""Mask sub-module (sketch §7 "Image Tools" > Mask, §10)."""

from .io import read_mask_image, write_mask_image
from .model import MaskSettings

__all__ = ["MaskSettings", "MaskModule", "read_mask_image", "write_mask_image"]

# Qt classes load lazily (2026-10-19): importing a pure sub-module of this package (settings, geometry maths) must not
# load Qt, so analysis worker processes start fast and the pure files stay testable without Qt. Names stay importable
# from here exactly as before.
_LAZY = {
    "MaskModule": ".module",
}


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
