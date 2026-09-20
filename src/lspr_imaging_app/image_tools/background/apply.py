"""Pure background-model application (cheap formula/lookup; the "apply" half
of the estimate/apply split discussed in sketch §7 "Background").

No Qt import allowed in this file (AGENTS.md testing rule). Not yet
implemented.
"""

from __future__ import annotations

import numpy as np


def apply_background(image: np.ndarray, model: object) -> np.ndarray:
    """Apply a fitted background ``model`` to ``image``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
