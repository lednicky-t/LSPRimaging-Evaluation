"""Which reference numbers go with which sample (phase 1.2b of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``). Pure, no Qt.

The scan stores, per ROI row, two columns: ``SAMPLE`` = the ROI's own region and ``REFERENCE`` = its built-in reference (the ring; empty for a
reference ROI, which is one region). Which reference a *sample* is measured against is decided **here, afterwards, on the stored numbers**
(``domain/roi_links.py`` has the rules), so linking and unlinking never need a rescan:

1. the sample's **linked** reference ROIs, when it has any: one linked reference is its stored region as it is; several are **pooled by pixel**,
   the mean, standard deviation and pixel count you would get if their pixels were one region, computed from each region's stored mean,
   standard deviation and count (no image is read);
2. else its **built-in** reference, the stored ring column, untouched;
3. else **no reference**: its stored reference column is empty already (no ring), so every reader leaves the cell out and nothing is filled in.

:func:`pair_scan` returns the scan as the display should see it: the same :class:`~.scan_store.StoredScan` shape (``[cube, roi, wavelength, side]``),
**one row per sample ROI**, with the ``REFERENCE`` column replaced by the sample's effective reference. A reference ROI is not an analysis
unit, so it has no row. Every reader that takes ``mean[..., SAMPLE]`` over ``mean[..., REFERENCE]`` then needs no change. A session with no
reference ROI and no link is handed back **as the same object**, with nothing copied.

Only the ``mean`` of several pooled references is exact from stored numbers, and ``mean`` is the only reduction the app scans; any other stored
method has its reference side left empty for a sample with two or more linked references (a median cannot be pooled from medians).

The raw scan file is never changed. Everything here reads the arrays and returns new ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np

from ..domain.roi_links import SOURCE_LINKED, SOURCE_NONE, is_reference, reference_source
from ..domain.roi_model import AreaRoi

if TYPE_CHECKING:  # a type only: importing it would load h5py where this pure module is used
    from .scan_store import StoredScan

SAMPLE, REFERENCE = 0, 1
POOLED_METHOD = "mean"  # the one reduction whose pooled value follows from stored mean / SD / count


@dataclass(frozen=True)
class PairedScan:
    """The display's view of a scan: ``stored`` (one row per sample ROI, reference side = the effective reference) plus, per sample ROI id, where
    its reference came from (``domain.roi_links`` ``SOURCE_*``) and, for a sample whose reference is missing, why (a line for the status bar)."""

    stored: StoredScan
    sources: dict[int, str]
    problems: dict[int, str] = field(default_factory=dict)


def _reference_note(source: str) -> str:
    return "No reference: the ROI has no ring and no linked reference ROI." if source == SOURCE_NONE else ""


def pair_scan(stored: StoredScan, rois: Sequence[AreaRoi]) -> PairedScan:
    """The scan with each sample's reference side replaced by its effective reference (see the module docstring). ``rois`` are the session's
    ROIs as they are now (roles and links); a ROI that is not in the file (added after the scan) is left out and a sample linked to one has no
    reference until the scan is run again. Never changes ``stored``."""
    rois = list(rois)
    sources = {int(r.area_roi_id): reference_source(r) for r in rois if not is_reference(r)}
    problems = {i: _reference_note(s) for i, s in sources.items() if s == SOURCE_NONE}
    if not any(is_reference(r) or r.reference_roi_ids for r in rois):
        return PairedScan(stored, sources, problems)  # nothing to pair: the stored scan is already what the display needs

    file_row: dict[int, int] = {}
    for row, roi_id in enumerate(stored.roi_ids):
        file_row.setdefault(int(roi_id), row)
    samples = [r for r in rois if not is_reference(r) and int(r.area_roi_id) in file_row]
    samples.sort(key=lambda r: file_row[int(r.area_roi_id)])  # the file's row order, as the display always saw it
    keep = np.array([file_row[int(r.area_roi_id)] for r in samples], dtype=np.intp)

    def cut(a: np.ndarray) -> np.ndarray:  # the sample rows of an array whose second axis is the ROI
        return a[:, keep]

    values = {m: cut(v).copy() for m, v in stored.values.items()}
    std, n_valid, n_nominal, n_masked = (cut(a).copy() for a in (stored.std, stored.n_valid, stored.n_nominal, stored.n_masked))
    cell_hash = cut(stored.cell_hash).copy()

    single_at: list[int] = []  # positions (in the paired scan) of samples with exactly one linked reference, and its file row
    single_row: list[int] = []
    pooled: list[tuple[int, list[int]]] = []  # samples with two or more
    for position, roi in enumerate(samples):
        roi_id = int(roi.area_roi_id)
        if sources[roi_id] != SOURCE_LINKED:
            continue
        missing = [int(i) for i in roi.reference_roi_ids if int(i) not in file_row]
        if missing:  # the reference was added after the scan: nothing to measure against until the next scan
            _empty_reference(values, std, n_valid, n_nominal, n_masked, cell_hash, position)
            problems[roi_id] = f"Linked reference ROI {', '.join(map(str, missing))} is not in the scan file: run the scan again."
            continue
        rows = [file_row[int(i)] for i in roi.reference_roi_ids]
        if len(rows) == 1:
            single_at.append(position)
            single_row.append(rows[0])
        else:
            pooled.append((position, rows))

    if single_at:  # one linked reference: its stored region as it is (every method, every number: no arithmetic, so exact)
        at, src = np.array(single_at, dtype=np.intp), np.array(single_row, dtype=np.intp)
        for m, v in values.items():
            v[:, at, :, REFERENCE] = stored.values[m][:, src, :, SAMPLE]
        for out, source in ((std, stored.std), (n_valid, stored.n_valid), (n_nominal, stored.n_nominal), (n_masked, stored.n_masked)):
            out[:, at, :, REFERENCE] = source[:, src, :, SAMPLE]
        ok = stored.cell_hash[:, src] != 0
        cell_hash[:, at] = np.where(ok, cell_hash[:, at], cell_hash.dtype.type(0))
    for position, rows in pooled:
        _pool_into(stored, values, std, n_valid, n_nominal, n_masked, cell_hash, position, rows)

    recipe_id = cut(stored.recipe_id) if stored.recipe_id.ndim == 2 else stored.recipe_id
    layout_id = cut(stored.layout_id) if stored.layout_id.ndim == 2 else stored.layout_id
    paired = replace(
        stored, roi_ids=tuple(int(r.area_roi_id) for r in samples), values=values, std=std, n_valid=n_valid, n_nominal=n_nominal,
        n_masked=n_masked, cell_hash=cell_hash, recipe_id=recipe_id, layout_id=layout_id,
    )
    sources = {int(r.area_roi_id): sources[int(r.area_roi_id)] for r in samples}
    return PairedScan(paired, sources, {i: p for i, p in problems.items() if i in sources})


def _empty_reference(values, std, n_valid, n_nominal, n_masked, cell_hash, position: int) -> None:
    for v in values.values():
        v[:, position, :, REFERENCE] = np.nan
    std[:, position, :, REFERENCE] = np.nan
    for counts in (n_valid, n_nominal, n_masked):
        counts[:, position, :, REFERENCE] = 0
    cell_hash[:, position] = 0  # not complete until the scan includes the reference


def _pool_into(stored, values, std, n_valid, n_nominal, n_masked, cell_hash, position: int, rows: list[int]) -> None:
    """The reference side of sample ``position`` as the pixels of the reference ROIs at file ``rows`` pooled into one region.

    With region ``i`` of ``n_i`` pixels, mean ``m_i`` and standard deviation ``s_i`` (ddof = 1): ``N = sum n_i``, ``M = sum n_i m_i / N`` and
    ``(N - 1) S^2 = sum (n_i - 1) s_i^2 + sum n_i (m_i - M)^2``, the within-region plus the between-region sum of squares: the standard deviation
    of all the pixels together. A region with no valid pixel adds nothing; one with a single pixel adds no within-region part. Per (cube,
    wavelength) cell, in float64."""
    n = np.stack([stored.n_valid[:, r, :, SAMPLE] for r in rows]).astype(np.float64)  # [region, cube, wavelength]
    mean = np.stack([stored.values[POOLED_METHOD][:, r, :, SAMPLE] for r in rows])
    s = np.stack([stored.std[:, r, :, SAMPLE] for r in rows])
    has = (n > 0) & np.isfinite(mean)
    weight = np.where(has, n, 0.0)
    total = weight.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        pooled_mean = np.where(total > 0, (weight * np.where(has, mean, 0.0)).sum(axis=0) / total, np.nan)
        within = np.where(has & (n > 1) & np.isfinite(s), (n - 1.0) * s * s, 0.0).sum(axis=0)
        between = (weight * (np.where(has, mean, 0.0) - np.where(total > 0, pooled_mean, 0.0)) ** 2).sum(axis=0)
        pooled_std = np.where(total > 1, np.sqrt((within + between) / (total - 1.0)), np.nan)
    for m, v in values.items():
        v[:, position, :, REFERENCE] = pooled_mean if m == POOLED_METHOD else np.nan
    std[:, position, :, REFERENCE] = pooled_std
    n_valid[:, position, :, REFERENCE] = total.astype(n_valid.dtype)
    n_nominal[:, position, :, REFERENCE] = sum(stored.n_nominal[:, r, :, SAMPLE].astype(np.float64) for r in rows).astype(n_nominal.dtype)
    n_masked[:, position, :, REFERENCE] = sum(stored.n_masked[:, r, :, SAMPLE].astype(np.float64) for r in rows).astype(n_masked.dtype)
    complete = np.logical_and.reduce([stored.cell_hash[:, r] != 0 for r in rows])
    cell_hash[:, position] = np.where(complete, cell_hash[:, position], cell_hash.dtype.type(0))  # a cube where a reference was never scanned is not complete
