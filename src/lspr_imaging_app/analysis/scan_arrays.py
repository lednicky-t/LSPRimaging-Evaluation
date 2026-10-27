"""The numbers of a scan in the shape the result file will have (plan doc section 15): ``[cube, roi, wavelength, side]``.

``scan_cubes`` yields one :class:`~.scan.CubeResult` per cube, in completion order, with arrays shaped ``(roi, frame of this cube, side)``.
:class:`ScanArrays` collects them: ``place`` puts a cube's frames on the wavelength axis of the plan (a frame a cube does not have stays
NaN / 0), so the order in which cubes finish cannot change the arrays. No Qt.

``side`` is 0 = sample, 1 = reference (``frame_reduce.SAMPLE`` / ``REFERENCE``). ``done[cube]`` is True once that cube was placed: a
cube that was never placed is "not computed", not "all NaN".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .scan import CubeResult
from .scan_jobs import ScanPlan


@dataclass
class ScanArrays:
    cube_indices: tuple[int, ...]
    roi_ids: tuple[int, ...]
    wavelength_nm: tuple[float, ...]
    values: dict[str, np.ndarray]  # method -> float64 [cube, roi, wavelength, side], NaN = no value
    std: np.ndarray  # float64, spread of the valid pixels
    n_valid: np.ndarray  # uint32, pixels that had a value
    n_nominal: np.ndarray  # uint32, pixels in the aperture after the exclusions
    n_masked: np.ndarray  # uint32, aperture pixels the ignore mask removed (0 = the mask did not touch this ROI side)
    done: np.ndarray  # bool [cube]

    @property
    def n_done(self) -> int:
        return int(self.done.sum())


def new_arrays(plan: ScanPlan) -> ScanArrays:
    shape = (len(plan.cube_indices), len(plan.roi_ids), len(plan.wavelength_axis_nm), 2)
    return ScanArrays(
        cube_indices=plan.cube_indices,
        roi_ids=plan.roi_ids,
        wavelength_nm=plan.wavelength_axis_nm,
        values={m: np.full(shape, np.nan) for m in plan.settings.methods},
        std=np.full(shape, np.nan),
        n_valid=np.zeros(shape, dtype=np.uint32),
        n_nominal=np.zeros(shape, dtype=np.uint32),
        n_masked=np.zeros(shape, dtype=np.uint32),
        done=np.zeros(len(plan.cube_indices), dtype=bool),
    )


def place(arrays: ScanArrays, plan: ScanPlan, result: CubeResult) -> None:
    """Put one finished cube into ``arrays``. A cube is placed once; placing it again is a bug and raises.

    A result with a ``roi_subset`` fills only those ROI rows of the cube (the other rows stay NaN / 0 here: they were not computed in this run,
    and the file keeps what it holds for them); ``done[cube]`` then means "this cube was processed", not "every ROI is in the arrays"."""
    pos = result.cube_pos
    if arrays.done[pos]:
        raise ValueError(f"cube position {pos} (cube {plan.cube_indices[pos]}) was placed twice")
    columns = list(plan.axis_positions[pos])
    if result.roi_subset is None:
        where = (slice(None), columns, slice(None))
    else:
        where = (np.asarray(result.roi_subset, dtype=np.intp)[:, None], np.asarray(columns, dtype=np.intp)[None, :], slice(None))
    for method, values in result.values.items():
        arrays.values[method][pos][where] = values
    arrays.std[pos][where] = result.std
    arrays.n_valid[pos][where] = result.n_valid
    arrays.n_nominal[pos][where] = result.n_nominal
    arrays.n_masked[pos][where] = result.n_masked
    arrays.done[pos] = True
