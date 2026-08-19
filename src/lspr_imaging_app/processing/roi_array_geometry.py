from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
from skimage.feature import blob_log
from skimage.transform import resize

# blob_log's runtime scales with image area * number of scales - on a full
# multi-megapixel frame with a wide sigma range it can take well over a
# minute. Only rough positions/radii are needed here (the existing
# semi-automatic pipeline's subpixel contrast-score refinement locks onto
# the real edges afterward), so detection runs on a capped-size working
# copy and results are scaled back to original-image pixel units.
_MAX_WORKING_DIMENSION = 640


@dataclass(frozen=True)
class ArrayGeometryEstimate:
    """Inferred parameters of a periodic circle array, found directly from
    image content - the "fully automatic" counterpart to manually setting
    diameter/rows/cols/spacing before running semi-automatic detection."""

    rows: int
    cols: int
    spacing_px: float
    radius_px: float
    origin_x: float
    origin_y: float
    blob_count: int


def estimate_array_geometry(
    image: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    min_radius_px: float = 5.0,
    max_radius_px: float | None = None,
    num_sigma: int = 12,
    threshold_rel: float = 0.25,
    radius_outlier_fraction: float = 0.35,
    cluster_tolerance_fraction: float = 0.35,
    row_col_spacing_agreement_fraction: float = 0.2,
    min_blobs: int = 4,
    diagnostics: dict | None = None,
) -> ArrayGeometryEstimate | None:
    """Detect a periodic square-ish array of circular ROIs directly from the
    image, with no diameter/rows/cols/spacing given up front.

    Three stages, each independently well-established:

    1. Radius-free blob detection (skimage.feature.blob_log, a Laplacian-of-
       Gaussian scale-space detector): finds circular blobs and their radii
       simultaneously by scanning a range of Gaussian scales, so no diameter
       needs to be assumed. ROIs are assumed darker than background here
       (absorption - see roi_detection.py's mode="dark" default and the
       maintainer's own description of the physics), so the image is
       inverted before detection since blob_log looks for bright blobs. Real
       camera images carry pixel-level sensor noise that blob_log's default
       sensitivity picks up as hundreds of spurious single-pixel "blobs" -
       the image is pre-smoothed (scipy.ndimage.gaussian_filter, sigma tied
       to min_radius_px) before detection to suppress that noise floor while
       leaving genuine blob-scale features intact.

    2. Lattice recovery: the median nearest-neighbor distance between
       detected blob centers (scipy.spatial.cKDTree) gives an initial
       spacing estimate - for an axis-aligned grid, an interior point's
       nearest neighbor is always one lattice step away, never diagonal.
       Blob x- and y-coordinates are then independently clustered into
       columns/rows using that spacing as the gap threshold, which gives
       rows, cols, and a refined spacing from the cluster positions
       themselves. Row spacing and column spacing must agree with each
       other (within row_col_spacing_agreement_fraction) or the result is
       rejected - a real square array should show the same pitch on both
       axes, so persistent disagreement means the detected points likely
       aren't a real grid.

    3. Outlier rejection throughout: blobs with an atypical radius (debris,
       merged/split detections) are dropped via median-absolute-deviation
       before spacing is estimated, and if column/row clustering produces
       far more grid cells than detected blobs, or fewer than 2 rows/cols,
       the whole result is rejected rather than guessing - callers should
       fall back to semi-automatic detection in that case.

    The reference ring around each sample circle is not decided here - see
    estimate_reference_ring_radii, a plain area formula that only needs the
    radius this function returns.

    Returns None if no confident periodic array is found. If `diagnostics`
    is passed, it is populated in place with a "reason" string plus whatever
    intermediate numbers were available when the estimate was rejected (or,
    on success, the stats behind the final answer) - callers that want to
    tell the user *why* nothing was found, not just that nothing was found,
    should read it after the call.
    """
    if diagnostics is None:
        diagnostics = {}

    def fail(reason: str, **extra: object) -> None:
        diagnostics["reason"] = reason
        diagnostics.update(extra)
        return None

    image = np.asarray(image, dtype=np.float64)
    if image.ndim != 2 or image.size == 0:
        return fail("Image is empty or not 2D.")
    height, width = image.shape

    finite = np.isfinite(image)
    if valid_mask is not None and valid_mask.shape == image.shape:
        finite = finite & valid_mask
    if not np.any(finite):
        return fail("No valid (unmasked) pixels to search.")

    lo = float(np.min(image[finite]))
    hi = float(np.max(image[finite]))
    if hi <= lo:
        return fail("Image has no intensity variation (flat).")
    normalized = np.zeros_like(image)
    normalized[finite] = (image[finite] - lo) / (hi - lo)
    # ROIs absorb light, so they are always at or below background intensity
    # - invert so the darker ROIs become the bright blobs blob_log looks for.
    inverted = 1.0 - normalized
    inverted[~finite] = 0.0

    if max_radius_px is None:
        max_radius_px = max(min(min(height, width) / 10.0, 80.0), min_radius_px + 1.0)

    working_scale = min(1.0, _MAX_WORKING_DIMENSION / float(max(height, width)))
    if working_scale < 1.0:
        working_image = resize(
            inverted,
            (max(int(round(height * working_scale)), 1), max(int(round(width * working_scale)), 1)),
            anti_aliasing=True,
            preserve_range=True,
        )
    else:
        working_image = inverted

    min_sigma = max((min_radius_px * working_scale) / np.sqrt(2.0), 1.0)
    max_sigma = max((max_radius_px * working_scale) / np.sqrt(2.0), min_sigma + 1.0)

    # Suppresses sensor-noise-scale false blobs without smearing out real
    # ones - see the docstring's stage-1 note. Tied to min_sigma (the
    # smallest scale being searched) rather than a fixed value, so it scales
    # with min_radius_px instead of over- or under-smoothing for a different
    # choice of it.
    denoised_image = gaussian_filter(working_image, sigma=min_sigma)

    blobs = blob_log(
        denoised_image,
        min_sigma=min_sigma,
        max_sigma=max_sigma,
        num_sigma=num_sigma,
        threshold=None,
        threshold_rel=threshold_rel,
        overlap=0.3,
    )
    diagnostics["raw_blob_count"] = int(blobs.shape[0])
    if blobs.shape[0] < min_blobs:
        return fail(
            f"Only found {blobs.shape[0]} candidate circle(s) (need at least {min_blobs}). "
            "Contrast may be too subtle, or spots may fall outside the searched size range.",
        )

    # Back to original-image pixel units.
    ys = blobs[:, 0] / working_scale
    xs = blobs[:, 1] / working_scale
    radii = (blobs[:, 2] * np.sqrt(2.0)) / working_scale

    if valid_mask is not None and valid_mask.shape == image.shape:
        row_indices = np.clip(np.round(ys).astype(int), 0, height - 1)
        col_indices = np.clip(np.round(xs).astype(int), 0, width - 1)
        keep = valid_mask[row_indices, col_indices]
        ys, xs, radii = ys[keep], xs[keep], radii[keep]
    diagnostics["blob_count_after_mask"] = int(ys.size)
    if ys.size < min_blobs:
        return fail(
            f"Only {ys.size} of {blobs.shape[0]} candidate circles remained after excluding "
            f"masked/rotation-fill pixels (need at least {min_blobs})."
        )

    # Drop atypically sized blobs (debris, merged/split detections) - real
    # ROIs in these arrays are assumed similarly sized.
    median_radius = float(np.median(radii))
    mad = float(np.median(np.abs(radii - median_radius))) or median_radius * 0.1
    radius_tolerance = max(mad * 3.0, median_radius * radius_outlier_fraction)
    radius_keep = np.abs(radii - median_radius) <= radius_tolerance
    ys, xs, radii = ys[radius_keep], xs[radius_keep], radii[radius_keep]
    diagnostics["blob_count_after_radius_filter"] = int(ys.size)
    diagnostics["median_radius_px"] = median_radius
    if ys.size < min_blobs:
        return fail(
            f"Only {ys.size} candidate circles had a consistent size (need at least {min_blobs}); "
            f"the rest differed too much from the median radius ({median_radius:.1f} px)."
        )
    radius_px = float(np.median(radii))

    spacing_estimate = _median_nearest_neighbor_distance(xs, ys)
    diagnostics["spacing_estimate_px"] = spacing_estimate
    if spacing_estimate is None or spacing_estimate <= radius_px:
        return fail("Couldn't establish a consistent spacing between the detected circles.")

    col_clusters = _cluster_1d(xs, spacing_estimate * cluster_tolerance_fraction)
    row_clusters = _cluster_1d(ys, spacing_estimate * cluster_tolerance_fraction)
    if col_clusters is None or row_clusters is None:
        return fail("Couldn't group the detected circles into columns/rows.")
    cols = len(col_clusters)
    rows = len(row_clusters)
    diagnostics["cols_found"] = cols
    diagnostics["rows_found"] = rows
    if cols < 2 or rows < 2:
        # A single spot, or a single row/column of them, has no measurable
        # periodicity - nothing to safely infer rows/cols/spacing from.
        return fail(f"Only found {rows} row(s) x {cols} column(s) - no periodicity to measure spacing from.")
    if cols * rows > ys.size * 4:
        # Far more grid cells than detected blobs means clustering
        # fragmented into spurious small groups, not a real grid.
        return fail(
            f"Column/row grouping implied a {rows}x{cols} grid from only {ys.size} circles - "
            "too inconsistent to trust."
        )

    col_positions = np.array(sorted(col_clusters))
    row_positions = np.array(sorted(row_clusters))
    col_spacing = float(np.median(np.diff(col_positions))) if cols > 1 else spacing_estimate
    row_spacing = float(np.median(np.diff(row_positions))) if rows > 1 else spacing_estimate
    diagnostics["col_spacing_px"] = col_spacing
    diagnostics["row_spacing_px"] = row_spacing
    spacing_disagreement = abs(col_spacing - row_spacing) / max(col_spacing, row_spacing)
    if spacing_disagreement > row_col_spacing_agreement_fraction:
        # Row pitch and column pitch should match for a square array;
        # persistent disagreement means this probably isn't one.
        return fail(
            f"Row spacing ({row_spacing:.0f} px) and column spacing ({col_spacing:.0f} px) don't agree - "
            "this may not be a square array."
        )
    spacing_px = float(0.5 * (col_spacing + row_spacing))
    if spacing_px <= radius_px:
        return fail("Inferred spacing is not larger than the circle radius.")

    # Independently clustering x- and y-coordinates only proves each *axis*
    # has periodic structure - random scatter can do that too (e.g. 4 loose
    # x-clusters and 4 loose y-clusters from 16 unrelated points "explain"
    # 16 grid cells without any of them actually being 2D-grid-aligned).
    # What a real array needs is most row x col *combinations* to actually
    # have a detected blob near them - check that directly.
    occupancy_tolerance = spacing_px * cluster_tolerance_fraction
    points = np.stack([xs, ys], axis=1)
    tree = cKDTree(points)
    occupied_cells = 0
    for row_position in row_positions:
        for col_position in col_positions:
            distance, _ = tree.query([col_position, row_position], k=1)
            if distance <= occupancy_tolerance:
                occupied_cells += 1
    occupancy_fraction = occupied_cells / float(rows * cols)
    diagnostics["occupancy_fraction"] = occupancy_fraction
    if occupancy_fraction < 0.5:
        return fail(
            f"Only {occupancy_fraction * 100.0:.0f}% of the inferred {rows}x{cols} grid's cells actually "
            "have a circle near them - too sparse to trust."
        )

    diagnostics["reason"] = "ok"
    return ArrayGeometryEstimate(
        rows=int(rows),
        cols=int(cols),
        spacing_px=spacing_px,
        radius_px=radius_px,
        origin_x=float(col_positions[0]),
        origin_y=float(row_positions[0]),
        blob_count=int(ys.size),
    )


def estimate_reference_ring_radii(sample_radius_px: float, *, inner_gap_factor: float = 1.4) -> tuple[float, float]:
    """Reference ring radii sized from the sample circle alone: the inner
    radius sits a fixed factor beyond the sample circle (a gap, so scattered
    light right at the sample edge doesn't bleed into the reference), and
    the outer radius is solved so the ring's area equals the sample circle's
    area (pi*(outer^2 - inner^2) = pi*sample_radius_px^2), matching the
    maintainer's stated convention. inner_gap_factor=1.4 matches this app's
    existing manual defaults (reference_inner_radius_px=14 for
    sample_radius_px=10)."""
    inner_radius_px = float(sample_radius_px) * inner_gap_factor
    outer_radius_px = float(np.hypot(inner_radius_px, sample_radius_px))
    return inner_radius_px, outer_radius_px


def _median_nearest_neighbor_distance(xs: np.ndarray, ys: np.ndarray) -> float | None:
    if xs.size < 2:
        return None
    points = np.stack([xs, ys], axis=1)
    tree = cKDTree(points)
    distances, _ = tree.query(points, k=2)
    nearest = distances[:, 1]
    finite = nearest[np.isfinite(nearest) & (nearest > 0.0)]
    if finite.size == 0:
        return None
    return float(np.median(finite))


def _cluster_1d(values: np.ndarray, tolerance: float) -> list[float] | None:
    """Groups nearby 1D values (e.g. blob x-coordinates) into clusters using
    a fixed gap threshold, returning each cluster's mean position. Used to
    turn a scatter of column-ish x-coordinates into a discrete set of column
    positions (and likewise for rows on y)."""
    if values.size == 0 or tolerance <= 0.0:
        return None
    ordered = np.sort(values)
    clusters: list[list[float]] = [[float(ordered[0])]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(float(value))
        else:
            clusters.append([float(value)])
    return [float(np.mean(cluster)) for cluster in clusters]
