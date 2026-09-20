"""Session persistence - settings JSON + ROI JSON read/write (sketch §10:
"ports storage/workspace.py mostly as-is").

Not yet ported. Current implementation lives in
``lspr_imaging_app/storage/workspace.py`` (this branch's own copy, kept
functional until this file replaces it) - not touched by this scaffolding
pass. See this session's commit message / the build log entry for why
``measurement_export.py`` (also named in sketch §10) was deliberately *not*
added here: that filename already exists in this package (the current,
still-in-use exporter) and creating a colliding stub would have silently
shadowed working code - adapting it for the §5 per-cell provenance design
is real design/port work for later, not scaffolding.
"""

from __future__ import annotations

from pathlib import Path


def load_session(path: Path) -> object:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError


def save_session(path: Path, session: object) -> None:
    """Not yet implemented - scaffolding only."""
    raise NotImplementedError
