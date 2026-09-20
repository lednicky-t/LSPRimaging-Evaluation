"""Pure background-model estimation (touches real pixels; the "estimate" half
of the estimate/apply split discussed in sketch §7 "Background").

No Qt import allowed in this file (AGENTS.md testing rule). Not yet
implemented.
"""

from __future__ import annotations

import numpy as np


def estimate_background(image: np.ndarray, *args: object, **kwargs: object) -> object:
    """Fit a background model from ``image``. Not yet implemented -
    scaffolding only."""
    raise NotImplementedError
