"""Pure background-model application (cheap formula/lookup; the "apply" half
of the estimate/apply split discussed in sketch §7 "Background").

Still not yet implemented as of the 2026-09-20 estimate.py port:
``estimate.py``'s ``flatten_background`` was ported verbatim from the old
app and still does estimate *and* apply (subtract-then-recenter) in one
call, exactly as it did in the source it was ported from. Splitting that
into a real, reusable "model" object this file could consume is genuine new
design work, not a port - deliberately deferred (see the rewrite build
log's 2026-09-20 "preprocess.py scope-check" entry).

No Qt import allowed in this file (AGENTS.md testing rule). Not yet
implemented.
"""

from __future__ import annotations

import numpy as np


def apply_background(image: np.ndarray, model: object) -> np.ndarray:
    """Apply a fitted background ``model`` to ``image``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
