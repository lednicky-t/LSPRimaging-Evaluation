"""Image Tools stage (sketch §7 "Image Tools", §10).

Four independent sub-modules - Geometry, Mask, Chromatic, Background - each
owning its own settings dataclass (``GeometrySettings``/``MaskSettings``/
``ChromaticSettings``/``BackgroundSettings``), matching the maintainer's
"crop/rotate/mask/CC/background all belong to the Image Tools stage, each
independent and well-documented" framing. There is no single
"ImageToolsModule" god class wrapping the four; other modules depend on
whichever sub-module they actually need.

The old app's single ``PreprocessingSettings`` (domain/models.py on
``develop``/``main``) mixed all four concerns' fields into one dataclass;
it was decomposed across these four sub-modules' own ``model.py`` files
during the 2026-09-20 ``preprocess.py`` port - see the rewrite build log's
"preprocess.py scope-check" entry for the field-by-field reasoning.
"""


__all__ = [
    "GeometryModule",
    "MaskModule",
    "ChromaticModule",
    "BackgroundModule",
    "ActiveToolModule",
    "ImageTool",
    "MaskScope",
    "MaskScopeModule",
    "MaskEditTool",
    "MaskEditToolModule",
]

# Qt classes load lazily (2026-10-19): importing a pure sub-module of this package (settings, geometry maths) must not
# load Qt, so analysis worker processes start fast and the pure files stay testable without Qt. Names stay importable
# from here exactly as before.
_LAZY = {
    "ActiveToolModule": ".active_tool",
    "ImageTool": ".active_tool",
    "BackgroundModule": ".background",
    "ChromaticModule": ".chromatic",
    "GeometryModule": ".geometry",
    "MaskModule": ".mask",
    "MaskEditTool": ".mask_edit_tool",
    "MaskEditToolModule": ".mask_edit_tool",
    "MaskScope": ".mask_scope",
    "MaskScopeModule": ".mask_scope",
}


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
