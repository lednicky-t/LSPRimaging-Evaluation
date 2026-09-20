"""Version marker for the rewrite-preview entry point (``app_rewrite.py``).

Deliberately separate from ``version.py``'s ``APP_VERSION`` - that number
describes the stable, shipped LSPRi Evaluation app (unchanged by this
rewrite work so far), while this one describes progress on the from-scratch
rewrite itself (see ``AGENTS.md`` and ``docs/rewrite_architecture_sketch_
2026-09.md``). Bump this as the rewrite gains real functionality; leave
``version.py`` alone until the rewrite actually replaces the shipped app.
"""

from __future__ import annotations

REWRITE_APP_NAME = "LSPR Imaging (Rewrite Preview)"
APP_VERSION = "0.1.0"


def rewrite_version_string() -> str:
    return f"{REWRITE_APP_NAME} {APP_VERSION}"
