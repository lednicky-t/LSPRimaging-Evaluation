"""Shared image-filename convention: `WL<wavelength>Frame<spectral_cube_index>`
(e.g. `imLCTFatWL500Frame3.tif`). Used by both `io/dataset.py` (parsing real
TIFF filenames) and `io/legacy_metadata.py` (parsing the same names out of
`measureing_times.csv`'s `Image` column) - split into its own module so
neither has to import the other for it.
"""

from __future__ import annotations

import re

IMAGE_PATTERN = re.compile(r"WL(?P<wl>\d+(?:\.\d+)?)Frame(?P<spectral_cube_index>\d+)", re.IGNORECASE)
