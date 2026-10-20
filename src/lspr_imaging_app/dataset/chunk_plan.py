"""Chunk layout suggestions for the OME-Zarr export (pure: numpy only, no Qt, no dataset).

Question answered: which chunk layouts suit this ROI layout, and what does each one cost in chunks read and disk size?

Facts this rests on (measured 2026-10-20 on real data; `docs/zarr_chunk_planner_2026-10-19.md`, Benchmark)
- Reading a region from Zarr instead of a whole TIFF is 10-15x faster for one ROI. Chunk sizes 64-256 px and grid
  alignment change that by only ~20-30 %, because each read has a ~2 ms fixed cost. So this module **suggests** a few
  good layouts and reports exact chunk counts; it does not predict milliseconds (a fitted time model was 2-3x off).
- Small chunks make **whole-plane** reads slow (~0.27 ms per chunk through zarr: 32 px chunks = 318 ms per plane vs
  26 ms for a TIFF). Below `SMALL_CHUNK_PX` pixels per chunk a warning is given.
- Blosc bit-shuffle only works when a chunk's pixel count is a multiple of 8; otherwise a small chunk is stored
  uncompressed (79 x 79 and 150 x 150: 100 % of raw; 80 x 80 and 152 x 152: 66 %). Suggested sizes respect this.
- A Zarr chunk grid always starts at stored pixel (0, 0). The grid is moved onto the ROIs by changing where the stored
  array starts: **pad** the left/top with fill (lossless) or **trim** ROI-free border (lossy), `ExportFrame`. Its
  origin is the OME-Zarr ``translation``.

Access patterns scored (one "unit" = one read)
- ``single``: each ROI on its own (sweeps, tests); ``group``: each ROI group (an array column, or ROI-table groups);
  ``all``: the whole layout (the analysis reads the whole plane instead, which is faster than 160 region reads).

Read boxes are half-open ``[x0, x1) x [y0, y1)`` in **export space** (processed space when the export applies the
image tools, raw space when it does not). Grid alignment: for one axis the number of chunks a unit touches only
changes where a grid line crosses a box edge, so only those offsets are evaluated (exact); of the equally good ones the
middle of the widest run is taken, which leaves the most room (``tolerance``) for drift.

Known limits: one box per ROI (base geometry united over the chromatic affines; per-cube `RoiTimeline` edits are not
included); the suggestions were tuned on one dataset (pitch 79 px, 160 ROIs).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "PATTERNS",
    "SMALL_CHUNK_PX",
    "ChunkLayout",
    "ExportFrame",
    "PatternScore",
    "Suggestion",
    "align_grid",
    "chunk_warnings",
    "compresses",
    "estimate_compression_ratio",
    "estimate_pitch",
    "export_frame",
    "lattice_groups",
    "map_boxes",
    "roi_read_boxes",
    "score_layout",
    "suggest_layouts",
]

PATTERNS: tuple[str, ...] = ("single", "group", "all")
SMALL_CHUNK_PX = 100 * 100
"""Fewer pixels per chunk than this: whole-plane reads get slow (measured: 64 x 64 px = 4x slower than a TIFF)."""


@dataclass(frozen=True)
class ChunkLayout:
    chunk_w: int
    chunk_h: int
    offset_x: int = 0
    """Grid lines lie at ``offset_x + k * chunk_w`` in export space (0 <= offset_x < chunk_w)."""
    offset_y: int = 0
    tolerance_x: int = 0
    """How many px the ROI layout can shift left or right without any ROI touching another chunk."""
    tolerance_y: int = 0

    @property
    def square(self) -> bool:
        return self.chunk_w == self.chunk_h


@dataclass(frozen=True)
class PatternScore:
    """Exact chunks read by one access pattern under one layout, averaged over its units (one unit = one read)."""

    units: int
    chunks: float
    """Chunks read per unit (mean)."""
    amplification: float
    """Pixels decoded / pixels the unit's ROI boxes cover (1.0 = nothing wasted)."""


@dataclass(frozen=True)
class ExportFrame:
    """Which part of the source plane the stored array covers, so its chunk grid lands on the layout's grid."""

    lossless: bool
    origin_x: int
    """Source x of stored pixel 0 (negative: that many padded columns). The OME-Zarr ``translation``."""
    origin_y: int
    width: int
    height: int
    chunks_x: int
    chunks_y: int
    stored_bytes_per_plane: int
    """Uncompressed, whole chunks (edge chunks are stored full size)."""
    discarded_px: int
    """Source pixels per plane not in the export (always 0 when lossless)."""
    padded_px: int
    """Stored pixels per plane with no source pixel behind them (fill)."""
    kept_px: int
    """Source pixels per plane in the export."""
    bytes_per_pixel: int

    @property
    def chunks_per_plane(self) -> int:
        return self.chunks_x * self.chunks_y

    def dataset_bytes(self, planes: int, compression_ratio: float | None = None) -> int:
        """Size on disk for ``planes`` planes. ``None``: uncompressed (whole chunks, fill included). Otherwise the
        ratio applies to the real pixels only; fill compresses to almost nothing (checked 2026-10-19 against a real
        export: within 2 % when the ratio is sampled over the wavelengths)."""
        if compression_ratio is None:
            return int(self.stored_bytes_per_plane) * int(planes)
        return int(round(self.kept_px * self.bytes_per_pixel * planes * compression_ratio))


@dataclass(frozen=True)
class Suggestion:
    key: str
    """Stable id: ``columns``, ``rows``, ``square_pitch2``, ``square_256``, ``square_128``, ``per_roi``."""
    name: str
    description: str
    layout: ChunkLayout
    scores: dict[str, PatternScore]
    lossless: ExportFrame
    lossy: ExportFrame
    warnings: tuple[str, ...] = ()
    recommended: bool = False


# -- read boxes and lattice -----------------------------------------------------------------


def roi_read_boxes(
    rois: Sequence,
    image_shape: tuple[int, int],
    *,
    default_inner_diameter_px: float,
    default_outer_diameter_px: float,
    affines: Sequence[np.ndarray] | None = None,
) -> np.ndarray:
    """(N, 4) int64 ``x0, y0, x1, y1`` (half-open, clipped to the image) of the pixels each `AreaRoi` reads:
    sample disk and reference ring (or their free-form masks), united over ``affines`` (chromatic correction, one per
    wavelength; ``None`` = identity). Same reach rule as `analysis.frame_reduce.locate_pixels`: a pixel belongs to a
    circle when its distance from the (transformed) centre is at most radius x the affine's largest stretch.
    A ROI entirely outside the image gives an empty box (x1 <= x0)."""
    from ..roi.rasterize import effective_reference_diameters

    height, width = int(image_shape[0]), int(image_shape[1])
    n = len(rois)
    if n == 0:
        return np.zeros((0, 4), dtype=np.int64)
    mats = [np.eye(2, 3)] if not affines else [np.asarray(a, dtype=np.float64)[:2, :3] for a in affines]
    lo = np.full((n, 2), np.inf)
    hi = np.full((n, 2), -np.inf)
    centres = np.array([(float(r.center_x), float(r.center_y)) for r in rois], dtype=np.float64)
    radius = np.zeros(n)
    rects: list[tuple[int, float, float, float, float]] = []  # (roi, x0, y0, x1, y1) source-space mask rectangles
    for i, roi in enumerate(rois):
        if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
            rects.append((i, *_mask_rect(roi.sample_mask)))
        else:
            radius[i] = max(radius[i], float(roi.sample_diameter_px) / 2.0)
        if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
            rects.append((i, *_mask_rect(roi.reference_mask)))
        else:
            _inner, outer = effective_reference_diameters(roi, default_inner_diameter_px, default_outer_diameter_px)
            radius[i] = max(radius[i], outer / 2.0)
    circular = radius > 0.0
    for m in mats:
        stretch = max(float(np.max(np.linalg.svd(m[:, :2], compute_uv=False))), 1e-6)
        t = centres @ m[:, :2].T + m[:, 2]
        reach = (radius * stretch)[:, None]
        lo[circular] = np.minimum(lo[circular], (t - reach)[circular])
        hi[circular] = np.maximum(hi[circular], (t + reach)[circular])
        for i, x0, y0, x1, y1 in rects:
            corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) @ m[:, :2].T + m[:, 2]
            lo[i] = np.minimum(lo[i], corners.min(axis=0))
            hi[i] = np.maximum(hi[i], corners.max(axis=0))
    out = np.zeros((n, 4), dtype=np.int64)
    ok = np.isfinite(lo).all(axis=1)
    out[ok, 0] = np.clip(np.floor(lo[ok, 0]), 0, width)
    out[ok, 1] = np.clip(np.floor(lo[ok, 1]), 0, height)
    out[ok, 2] = np.clip(np.floor(hi[ok, 0]) + 1, 0, width)
    out[ok, 3] = np.clip(np.floor(hi[ok, 1]) + 1, 0, height)
    return out


def map_boxes(boxes: np.ndarray, affine: np.ndarray, image_shape: tuple[int, int]) -> np.ndarray:
    """Read boxes moved through a 2x3 affine (e.g. processed -> raw space for a raw-pixel export): the axis-aligned
    box around each mapped rectangle, clipped to ``image_shape``. A rotation makes the boxes a little larger (the
    corners of the rotated rectangle); empty boxes stay empty."""
    boxes = np.asarray(boxes, dtype=np.int64).reshape(-1, 4)
    m = np.asarray(affine, dtype=np.float64)[:2, :3]
    height, width = int(image_shape[0]), int(image_shape[1])
    out = np.zeros_like(boxes)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    if not valid.any():
        return out
    b = boxes[valid].astype(np.float64)
    # Pixel edges: the box covers [x0, x1) x [y0, y1); its corners are the outer pixel edges.
    xs = np.stack((b[:, 0], b[:, 2], b[:, 0], b[:, 2]), axis=1)
    ys = np.stack((b[:, 1], b[:, 1], b[:, 3], b[:, 3]), axis=1)
    tx = m[0, 0] * xs + m[0, 1] * ys + m[0, 2]
    ty = m[1, 0] * xs + m[1, 1] * ys + m[1, 2]
    eps = 1e-6  # so floating-point noise (cos 90 deg = 6e-17) never adds a whole pixel
    out[valid, 0] = np.clip(np.floor(tx.min(axis=1) + eps), 0, width)
    out[valid, 1] = np.clip(np.floor(ty.min(axis=1) + eps), 0, height)
    out[valid, 2] = np.clip(np.ceil(tx.max(axis=1) - eps), 0, width)
    out[valid, 3] = np.clip(np.ceil(ty.max(axis=1) - eps), 0, height)
    return out


def _mask_rect(mask) -> tuple[float, float, float, float]:
    h, w = mask.mask.shape[:2]
    return float(mask.x0), float(mask.y0), float(mask.x0 + w - 1), float(mask.y0 + h - 1)


def lattice_groups(centers_xy: np.ndarray, *, axis: str, tolerance_px: float) -> list[list[int]]:
    """Group ROI indices into lattice columns (``axis="columns"``, by x) or rows (``"rows"``, by y): sorted
    coordinates are split wherever the gap exceeds ``tolerance_px`` (use about half the pitch). Groups are ordered
    left to right / top to bottom."""
    if axis not in ("columns", "rows"):
        raise ValueError(f"axis must be 'columns' or 'rows', not {axis!r}")
    pts = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    if len(pts) == 0:
        return []
    v = pts[:, 0] if axis == "columns" else pts[:, 1]
    order = np.argsort(v, kind="stable")
    cuts = np.flatnonzero(np.diff(v[order]) > float(tolerance_px)) + 1
    return [sorted(int(i) for i in part) for part in np.split(order, cuts)]


def estimate_pitch(centers_xy: np.ndarray) -> tuple[float, float] | None:
    """Lattice pitch (x, y) in px from ROI centres: columns and rows are grouped with half the median
    nearest-neighbour distance as tolerance, the pitch is the median step between neighbouring column (row) means.
    ``None`` when there are fewer than two columns or two rows (no lattice)."""
    pts = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    if len(pts) < 4:
        return None
    from scipy.spatial import cKDTree

    nn = float(np.median(cKDTree(pts).query(pts, k=2)[0][:, 1]))
    if nn <= 0:
        return None
    pitch = []
    for axis, dim in (("columns", 0), ("rows", 1)):
        groups = lattice_groups(pts, axis=axis, tolerance_px=nn / 2.0)
        if len(groups) < 2:
            return None
        means = np.array([pts[g, dim].mean() for g in groups])
        pitch.append(float(np.median(np.diff(means))))
    return pitch[0], pitch[1]


# -- grid alignment -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Units:
    """One access pattern's units, as index arrays (sorted by box start for the union count in `_axis_counts`)."""

    single: bool
    members: tuple[np.ndarray, ...]


def _make_units(valid: np.ndarray, pattern: str, groups: Sequence[Sequence[int]] | None) -> _Units | None:
    idx = np.flatnonzero(valid)
    if pattern == "single":
        return _Units(True, tuple(np.array([i]) for i in idx)) if idx.size else None
    if pattern == "all":
        return _Units(False, (idx,)) if idx.size else None
    members = []
    for g in groups or ():
        g = np.array([i for i in g if 0 <= i < len(valid) and valid[i]], dtype=np.int64)
        if g.size:
            members.append(g)
    return _Units(False, tuple(members)) if members else None


def _sorted_units(units: _Units, lo: np.ndarray) -> _Units:
    if units.single:
        return units
    return _Units(False, tuple(m[np.argsort(lo[m], kind="stable")] for m in units.members))


def _axis_counts(lo: np.ndarray, hi: np.ndarray, units: _Units, c: int, offsets: np.ndarray) -> np.ndarray:
    """(len(offsets), n_units): chunk columns each unit touches, for grid lines at ``offset + k*c``."""
    o = offsets[:, None]
    if units.single:
        sel = np.concatenate(units.members)
        return (hi[sel] - 1 - o) // c - (lo[sel] - o) // c + 1
    out = np.empty((len(offsets), len(units.members)), dtype=np.int64)
    for u, members in enumerate(units.members):
        a = (lo[members] - o) // c  # members are sorted by lo, so `a` is non-decreasing along axis 1 for every offset
        b = (hi[members] - 1 - o) // c
        prev = np.maximum.accumulate(b, axis=1)
        prev = np.concatenate((np.full((len(offsets), 1), np.iinfo(np.int64).min // 2), prev[:, :-1]), axis=1)
        out[:, u] = np.clip(b - np.maximum(a, prev + 1) + 1, 0, None).sum(axis=1)  # union of integer intervals
    return out


def _best_offset(lo: np.ndarray, hi: np.ndarray, units: _Units, c: int, tie_units: _Units | None = None) -> tuple[int, int]:
    """Offset (middle of the widest run of best offsets) and its tolerance (px of shift that stays as good).

    ``tie_units`` (normally the single ROIs) decide between offsets that are equally good for ``units``. Without
    it a saturated group count chose blindly: on 2026-10-20 a column of ROIs (87 px wide with the chromatic shift)
    could not fit one 80 px chunk at any offset, every offset tied at 2 chunks per column, and the widest run put
    a grid line through the middle of every column (2.00 chunks per ROI instead of 1.27)."""
    members = np.concatenate(units.members + (tie_units.members if tie_units is not None else ()))
    starts = np.unique(np.concatenate(((lo[members] + 1) % c, hi[members] % c)))  # where any count can change
    total = _axis_counts(lo, hi, units, c, starts).sum(axis=1)
    if tie_units is not None:
        tie = _axis_counts(lo, hi, tie_units, c, starts).sum(axis=1)
        total = total * (int(tie.max()) + 1) + tie  # lexicographic: units first, then tie_units
    widths = np.diff(np.append(starts, starts[0] + c))
    best = np.flatnonzero(total == total.min())
    j = best[np.argmax(widths[best])]
    w = int(widths[j])
    return int((starts[j] + (w - 1) // 2) % c), (w - 1) // 2


def align_grid(
    boxes: np.ndarray,
    chunk_w: int,
    chunk_h: int,
    *,
    pattern: str = "single",
    groups: Sequence[Sequence[int]] | None = None,
) -> ChunkLayout | None:
    """The grid position for a given chunk size that makes ``pattern``'s units touch the fewest chunks (per axis).
    ``None`` when the pattern has no units (no valid box, or ``group`` without groups)."""
    boxes = np.asarray(boxes, dtype=np.int64).reshape(-1, 4)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    units = _make_units(valid, pattern, groups)
    if units is None:
        return None
    tie = None if pattern == "single" else _make_units(valid, "single", None)  # ties go to the fewest chunks per ROI
    ox, tx = _best_offset(boxes[:, 0], boxes[:, 2], _sorted_units(units, boxes[:, 0]), int(chunk_w), tie)
    oy, ty = _best_offset(boxes[:, 1], boxes[:, 3], _sorted_units(units, boxes[:, 1]), int(chunk_h), tie)
    return ChunkLayout(int(chunk_w), int(chunk_h), ox, oy, tx, ty)


# -- scoring --------------------------------------------------------------------------------


def score_layout(boxes: np.ndarray, layout: ChunkLayout, *, groups: Sequence[Sequence[int]] | None = None) -> dict[str, PatternScore]:
    """Exact 2-D chunks read under ``layout`` for every pattern that has units (chunk sets united per unit)."""
    boxes = np.asarray(boxes, dtype=np.int64).reshape(-1, 4)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    cw, ch, ox, oy = layout.chunk_w, layout.chunk_h, layout.offset_x, layout.offset_y
    kx0 = (boxes[:, 0] - ox) // cw
    kx1 = (boxes[:, 2] - 1 - ox) // cw
    ky0 = (boxes[:, 1] - oy) // ch
    ky1 = (boxes[:, 3] - 1 - oy) // ch
    out: dict[str, PatternScore] = {}
    for pattern in PATTERNS:
        units = _make_units(valid, pattern, groups)
        if units is None:
            continue
        chunks = []
        amp = []
        for members in units.members:
            keys = set()
            for i in members:
                xs = range(int(kx0[i]), int(kx1[i]) + 1)
                for ky in range(int(ky0[i]), int(ky1[i]) + 1):
                    keys.update((kx, ky) for kx in xs)
            chunks.append(len(keys))
            amp.append(len(keys) * cw * ch / _union_area(boxes[members]))
        out[pattern] = PatternScore(units=len(units.members), chunks=float(np.mean(chunks)), amplification=float(np.mean(amp)))
    return out


def _union_area(boxes: np.ndarray) -> int:
    if len(boxes) == 1:
        b = boxes[0]
        return int((b[2] - b[0]) * (b[3] - b[1]))
    x0, y0 = int(boxes[:, 0].min()), int(boxes[:, 1].min())
    canvas = np.zeros((int(boxes[:, 3].max()) - y0, int(boxes[:, 2].max()) - x0), dtype=bool)
    for b in boxes:
        canvas[b[1] - y0 : b[3] - y0, b[0] - x0 : b[2] - x0] = True
    return int(canvas.sum())


def compresses(chunk_w: int, chunk_h: int) -> bool:
    """Blosc bit-shuffle needs a pixel count that is a multiple of 8 (else small chunks are stored raw)."""
    return (int(chunk_w) * int(chunk_h)) % 8 == 0


def chunk_warnings(chunk_w: int, chunk_h: int, *, compression: bool = True) -> tuple[str, ...]:
    """Plain-language warnings for a chunk size (also for a size the user typed)."""
    out = []
    if chunk_w * chunk_h < SMALL_CHUNK_PX:
        out.append(f"Small chunks ({chunk_w} x {chunk_h} px): whole-image reads get slow "
                   "(measured: 64 px chunks read a whole plane 4x slower than a TIFF).")
    if compression and not compresses(chunk_w, chunk_h):
        out.append(f"{chunk_w} x {chunk_h} = {chunk_w * chunk_h} px is not a multiple of 8: "
                   "these chunks will not compress (choose a size such as a multiple of 4).")
    return tuple(out)


def _snap_compressible(size: int, other: int, *, minimum: int = 1) -> int:
    """Smallest size >= ``size`` (and >= ``minimum``) whose chunk ``size x other`` has a multiple-of-8 pixel count."""
    s = max(int(size), int(minimum))
    while (s * int(other)) % 8:
        s += 1
    return s


# -- export frame and size ---------------------------------------------------------------


def export_frame(
    layout: ChunkLayout,
    boxes: np.ndarray,
    image_shape: tuple[int, int],
    *,
    lossless: bool,
    bytes_per_pixel: int = 2,
) -> ExportFrame:
    """Where the stored array starts and ends so its chunk grid falls on ``layout``'s grid.

    Lossless: pad the left/top (fewer than one chunk) and keep every source pixel. Lossy: keep only the
    chunk-aligned rectangle around all ``boxes`` (the rest of the plane is not exported)."""
    height, width = int(image_shape[0]), int(image_shape[1])
    boxes = np.asarray(boxes, dtype=np.int64).reshape(-1, 4)
    boxes = boxes[(boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])]

    def axis(size: int, c: int, o: int, lo: int, hi: int) -> tuple[int, int]:
        if lossless or len(boxes) == 0:
            return (o - c if o > 0 else 0), size  # start at the largest grid line <= 0
        return o + math.floor((lo - o) / c) * c, min(size, o + math.ceil((hi - o) / c) * c)

    lo_x, lo_y = (int(boxes[:, 0].min()), int(boxes[:, 1].min())) if len(boxes) else (0, 0)
    hi_x, hi_y = (int(boxes[:, 2].max()), int(boxes[:, 3].max())) if len(boxes) else (width, height)
    x_start, x_end = axis(width, layout.chunk_w, layout.offset_x, lo_x, hi_x)
    y_start, y_end = axis(height, layout.chunk_h, layout.offset_y, lo_y, hi_y)
    w, h = x_end - x_start, y_end - y_start
    nx, ny = math.ceil(w / layout.chunk_w), math.ceil(h / layout.chunk_h)
    kept = (min(x_end, width) - max(x_start, 0)) * (min(y_end, height) - max(y_start, 0))
    return ExportFrame(
        lossless=lossless, origin_x=x_start, origin_y=y_start, width=w, height=h, chunks_x=nx, chunks_y=ny,
        stored_bytes_per_plane=nx * ny * layout.chunk_w * layout.chunk_h * int(bytes_per_pixel),
        discarded_px=width * height - kept, padded_px=w * h - kept, kept_px=kept, bytes_per_pixel=int(bytes_per_pixel),
    )


def estimate_compression_ratio(
    planes: np.ndarray | Sequence[np.ndarray],
    chunk_w: int,
    chunk_h: int,
    *,
    cname: str | None = "lz4",
    max_tiles: int = 64,
) -> float:
    """Compressed / raw bytes of real pixels with the export's codec (Blosc, clevel 1, bit-shuffle, element size from
    the array as `io._zarr_export_worker.write_shard` uses it), averaged over ``planes`` (one 2-D plane or several).
    Up to ``max_tiles`` whole tiles spread over each plane are compressed.

    Pass planes from several wavelengths: on the 2026-10-19 check one dark wavelength compressed much better than the
    rest, so a single plane can be far off. A guide, not a guarantee."""
    if not cname:
        return 1.0
    from numcodecs import Blosc

    codec = Blosc(cname=cname, clevel=1, shuffle=Blosc.BITSHUFFLE)
    planes = [np.asarray(planes)] if np.ndim(planes) == 2 else [np.asarray(p) for p in planes]
    raw = packed = 0
    for plane in planes:
        ny, nx = plane.shape[0] // chunk_h, plane.shape[1] // chunk_w
        if ny == 0 or nx == 0:
            tiles = [plane]
        else:
            pick = np.unique(np.linspace(0, ny * nx - 1, num=min(max_tiles, ny * nx)).round().astype(int))
            tiles = [plane[(k // nx) * chunk_h : (k // nx + 1) * chunk_h, (k % nx) * chunk_w : (k % nx + 1) * chunk_w] for k in pick]
        raw += sum(t.nbytes for t in tiles)
        packed += sum(len(codec.encode(np.ascontiguousarray(t))) for t in tiles)
    return packed / raw if raw else 1.0


# -- suggestions -------------------------------------------------------------------------------


def suggest_layouts(
    boxes: np.ndarray,
    image_shape: tuple[int, int],
    *,
    centers_xy: np.ndarray | None = None,
    groups: Sequence[Sequence[int]] | None = None,
    bytes_per_pixel: int = 2,
    compression: bool = True,
) -> list[Suggestion]:
    """A short list of good layouts for this ROI layout, recommended one first.

    With a lattice (pitch found from ``centers_xy``, or the box centres): columns (pitch wide, whole height), square of
    about two pitches, square 256, rows, one chunk per ROI. Without one: squares 256 and 128. Every grid is aligned to
    the ROIs. ``groups`` (e.g. the ROI-table groups) are scored as the ``group`` pattern; default: lattice columns.
    The recommendation (columns, else square 256) comes from the 2026-10-20 benchmark: the best all-rounder for
    one ROI, one column and whole-plane reads."""
    boxes = np.asarray(boxes, dtype=np.int64).reshape(-1, 4)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    if not valid.any():
        return []
    height, width = int(image_shape[0]), int(image_shape[1])
    if centers_xy is None:
        centers_xy = (boxes[:, :2] + boxes[:, 2:]) / 2.0
    centers_xy = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    pitch = estimate_pitch(centers_xy[valid])
    foot_w = int((boxes[valid, 2] - boxes[valid, 0]).max())
    foot_h = int((boxes[valid, 3] - boxes[valid, 1]).max())
    columns = rows = None
    if pitch is not None:
        columns = lattice_groups(centers_xy, axis="columns", tolerance_px=pitch[0] / 2.0)
        rows = lattice_groups(centers_xy, axis="rows", tolerance_px=pitch[1] / 2.0)
    score_groups = groups if groups else columns

    specs: list[tuple[str, str, str, int, int, str, Sequence[Sequence[int]] | None]] = []
    if pitch is not None:
        px, py = pitch
        cw = _snap_compressible(round(px), height, minimum=foot_w)
        specs.append(("columns", "Columns", "One chunk per ROI column (pitch wide, full height): one ROI, one column and "
                      "whole-image reads are all fast.", cw, height, "group", columns))
        k = max(1, round(160 / max(px, py)))
        c = _snap_compressible(round(k * max(px, py)), 1, minimum=max(foot_w, foot_h))
        c += (-c) % 4  # square: a multiple of 4 gives a multiple-of-8 pixel count
        specs.append(("square_pitch2", f"Square {c} px", f"About {k} x {k} ROIs per chunk.", c, c, "single", None))
        specs.append(("square_256", "Square 256 px", "Simple general-purpose size.", 256, 256, "single", None))
        ch = _snap_compressible(round(py), width, minimum=foot_h)
        specs.append(("rows", "Rows", "One chunk per ROI row (pitch tall, full width).", width, ch, "group", rows))
        c = _snap_compressible(round(max(px, py)), 1, minimum=max(foot_w, foot_h))
        c += (-c) % 4
        specs.append(("per_roi", f"One ROI per chunk ({c} px)", "Fewest pixels per ROI read; whole-image reads are slow.",
                      c, c, "single", None))
    else:
        specs.append(("square_256", "Square 256 px", "Simple general-purpose size.", 256, 256, "single", None))
        specs.append(("square_128", "Square 128 px", "Smaller reads for single ROIs.", 128, 128, "single", None))

    out = []
    for i, (key, name, description, cw, ch, pattern, align_groups) in enumerate(specs):
        layout = align_grid(boxes, cw, ch, pattern=pattern, groups=align_groups) or ChunkLayout(cw, ch)
        if key == "columns":  # a full-height chunk must start at the top, or padding would add a second chunk row
            layout = ChunkLayout(cw, ch, layout.offset_x, 0, layout.tolerance_x, 0)
        elif key == "rows":
            layout = ChunkLayout(cw, ch, 0, layout.offset_y, 0, layout.tolerance_y)
        out.append(Suggestion(
            key=key, name=name, description=description, layout=layout,
            scores=score_layout(boxes, layout, groups=score_groups),
            lossless=export_frame(layout, boxes, image_shape, lossless=True, bytes_per_pixel=bytes_per_pixel),
            lossy=export_frame(layout, boxes, image_shape, lossless=False, bytes_per_pixel=bytes_per_pixel),
            warnings=chunk_warnings(cw, ch, compression=compression),
            recommended=(i == 0),
        ))
    return out
