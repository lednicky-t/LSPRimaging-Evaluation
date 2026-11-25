"""Robust fit of a 2-D lattice to a cloud of points (pure: numpy / scipy, no Qt, no image).

Used by the array finder (`array_finder.py`). Input: positions of similar-looking spots (some missing, some that do not belong). Output: two lattice vectors,
the origin and, for every point, whether it sits on a node (within a tolerance that is a fraction of the pitch) and which one.

Why not the old `array_detection._fit_lattice` (kept as it is, so the two finders can be compared): that one takes the two shortest neighbour vectors and a fixed
25 % tolerance, so one wrong neighbour vector ruins the fit and the tolerance cannot be tuned. Here several vector pairs are tried as hypotheses and the one that
explains most points wins (a RANSAC-like choice, but the hypotheses come from measured neighbour vectors, so there is no randomness: the same points give the same fit).

Conventions: positions are pixel-index x, y (x right, y down). ``a1`` is the lattice vector most along x (pointing right), ``a2`` the shortest independent one
that points down. Node ``(i, j)`` (column i, row j) is at ``origin + i * a1 + j * a2``; indices of the result start at 0 (the origin is node (0, 0) of the members).
A cloud whose points all lie on one line is a **one-dimensional** lattice: ``a2`` is then ``a1`` turned by 90 degrees and only ``j = 0`` is used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

__all__ = ["LatticeFit", "fit_lattice", "node_positions"]

EARLY_TOLERANCE = 0.3
"""Tolerance (x pitch) of the first refit passes: the vectors from neighbours are a little off, and a tight tolerance would lose the far nodes before the fit has
grown to them. The caller's tolerance decides the final membership."""
MAX_VECTORS = 8
"""Distinct neighbour vectors tried as lattice vectors (pairs of them are the hypotheses)."""
TIE_FRACTION = 0.95
"""Fits that explain at least this fraction of the best fit's weight count as equally good (the coarser lattice then wins)."""
MIN_SIN = 0.3
"""Two lattice vectors must differ in direction by at least ~17 degrees (sine of the angle between them)."""


@dataclass(frozen=True, slots=True)
class LatticeFit:
    origin: np.ndarray
    """Position of node (0, 0) (``(2,)``): the first column / row any member sits in."""
    a1: np.ndarray
    a2: np.ndarray
    column: np.ndarray
    """``(N,)`` int: node column ``i`` of each input point (meaningful where ``member``)."""
    row: np.ndarray
    member: np.ndarray
    """``(N,)`` bool: the point sits on its node within the tolerance (the best one per node)."""
    residual_px: np.ndarray
    """``(N,)`` distance of each point from its nearest node."""
    one_dimensional: bool

    @property
    def cols(self) -> int:
        return int(self.column[self.member].max()) + 1 if self.member.any() else 0

    @property
    def rows(self) -> int:
        return int(self.row[self.member].max()) + 1 if self.member.any() else 0


def node_positions(origin: np.ndarray, a1: np.ndarray, a2: np.ndarray, column: np.ndarray, row: np.ndarray) -> np.ndarray:
    """``(len, 2)`` positions of nodes ``(column, row)``."""
    return np.asarray(origin) + np.asarray(column, dtype=np.float64)[:, None] * a1 + np.asarray(row, dtype=np.float64)[:, None] * a2


def fit_lattice(
    points: np.ndarray,
    weights: np.ndarray | None = None,
    *,
    tolerance: float = 0.2,
    anchors: np.ndarray | None = None,
    min_members: int = 4,
) -> LatticeFit | None:
    """The lattice that explains the most ``points`` (weighted), or ``None`` when no lattice has ``min_members`` members.

    ``tolerance``: how far from its node a point may be, as a fraction of the shorter lattice vector. ``weights`` (``(N,)``, default 1): how much each point
    counts when hypotheses are compared (the similarity of the spot). ``anchors`` (``(K, 2)``): positions that must be nodes (the user's own ROIs): their
    neighbour vectors are tried first and a lattice that leaves one of them off its nodes loses to one that keeps them on."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    count = len(pts)
    if count < max(min_members, 3):
        return None
    w = np.ones(count) if weights is None else np.asarray(weights, dtype=np.float64).reshape(-1)
    anchor_index = _anchor_points(pts, anchors)

    fits = [fit for a1, a2 in _hypotheses(pts, anchor_index) if (fit := _refine(pts, w, a1, a2, tolerance, anchor_index)) is not None]
    best: tuple[float, np.ndarray, np.ndarray, np.ndarray] | None = None
    if fits:
        most_anchors = max(fit[0] for fit in fits)  # a lattice that keeps the user's own ROIs on its nodes beats one that does not, whatever the rest
        fits = [fit for fit in fits if fit[0] == most_anchors]
        top = max(fit[1] for fit in fits)
        # a finer lattice that contains the true one explains the same spots (and more empty nodes): of the fits that explain (nearly) as much as the best,
        # take the one with the biggest cell
        near_best = [fit for fit in fits if fit[1] >= TIE_FRACTION * top]
        chosen = max(near_best, key=lambda fit: abs(float(fit[3][0] * fit[4][1] - fit[3][1] * fit[4][0])))
        best = chosen[1:]
    one_dimensional = False
    if best is None or _member_count(pts, best, tolerance) < min_members:
        line = _line_fit(pts, w, tolerance, min_members)
        if line is None:
            return None
        best, one_dimensional = line, True
    return _finish(pts, best[1], best[2], best[3], tolerance, one_dimensional, min_members)


# -- hypotheses ---------------------------------------------------------------------------


def _anchor_points(points: np.ndarray, anchors: np.ndarray | None) -> list[int]:
    """Indices (into ``points``) of the points an anchor lies on (the caller adds the anchors to the cloud; the nearest point within 2 px is taken)."""
    if anchors is None or len(anchors) == 0:
        return []
    tree = cKDTree(points)
    found = []
    for anchor in np.asarray(anchors, dtype=np.float64).reshape(-1, 2):
        distance, index = tree.query(anchor)
        if distance <= 2.0:
            found.append(int(index))
    return found


def _canonical(vectors: np.ndarray) -> np.ndarray:
    """v and -v are the same lattice vector: flip to x > 0 (or x == 0, y > 0)."""
    flip = (vectors[:, 0] < 0) | ((vectors[:, 0] == 0) & (vectors[:, 1] < 0))
    out = vectors.copy()
    out[flip] *= -1.0
    return out


def _hypotheses(points: np.ndarray, anchor_index: list[int]):
    """Pairs ``(a1, a2)`` of candidate lattice vectors, the most supported first."""
    count = len(points)
    k = min(7, count - 1)
    _distance, neighbours = cKDTree(points).query(points, k=k + 1)
    vectors = _canonical((points[neighbours[:, 1:]] - points[:, None, :]).reshape(-1, 2))
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    keep = lengths > 1e-9
    vectors, lengths = vectors[keep], lengths[keep]
    if len(vectors) == 0:
        return
    shortest = float(np.percentile(lengths, 10))
    short = lengths <= 2.3 * shortest  # a few nearest-neighbour distances, not distant pairs across the image
    vectors, lengths = vectors[short], lengths[short]
    tree = cKDTree(vectors)
    support = np.array([len(hit) for hit in tree.query_ball_point(vectors, 0.2 * lengths)], dtype=np.float64)
    # vectors between two anchors (and from an anchor to its neighbours) count extra: the user pointed at them
    if anchor_index:
        for a in anchor_index:
            near = np.flatnonzero(np.hypot(*(points[neighbours[a, 1:]] - points[a]).T) > 0)
            for n in near:
                vector = _canonical((points[neighbours[a, 1 + n]] - points[a]).reshape(1, 2))[0]
                support[tree.query_ball_point(vector, 0.2 * float(np.hypot(*vector)))] += 2.0
    order = np.lexsort((lengths, -support))  # most supported first, shorter first on ties
    picked: list[np.ndarray] = []
    for index in order:
        vector = np.median(vectors[tree.query_ball_point(vectors[index], 0.2 * lengths[index])], axis=0)
        if all(np.hypot(*(vector - other)) > 0.2 * float(np.hypot(*vector)) for other in picked):
            picked.append(vector)
        if len(picked) >= MAX_VECTORS:
            break
    for i, first in enumerate(picked):
        for second in picked[i + 1 :]:
            sine = abs(first[0] * second[1] - first[1] * second[0]) / (np.hypot(*first) * np.hypot(*second))
            if sine >= MIN_SIN:
                yield first, second


# -- refinement ---------------------------------------------------------------------------


def _assign(points: np.ndarray, origin: np.ndarray, a1: np.ndarray, a2: np.ndarray, tolerance: float):
    """``(column, row, residual, member)`` of every point; two points on one node: the closer one is the member."""
    basis = np.column_stack([a1, a2])
    coefficients = np.linalg.solve(basis, (points - origin).T).T
    rounded = np.round(coefficients)
    residual = np.hypot(*(points - (origin + rounded @ basis.T)).T)
    limit = tolerance * min(float(np.hypot(*a1)), float(np.hypot(*a2)))
    member = residual <= limit
    candidates = np.flatnonzero(member)
    if len(candidates):  # two points on one node: the closer one is the member
        keys = rounded[candidates, 0].astype(np.int64) * 1_000_003 + rounded[candidates, 1].astype(np.int64)
        by_distance = np.argsort(residual[candidates], kind="stable")
        _unique, first = np.unique(keys[by_distance], return_index=True)
        member[:] = False
        member[candidates[by_distance[first]]] = True
    return rounded[:, 0].astype(np.int64), rounded[:, 1].astype(np.int64), residual, member


def _lstsq(points: np.ndarray, column: np.ndarray, row: np.ndarray, member: np.ndarray):
    """Least-squares origin, a1, a2 from the members, or ``None`` when they do not span two dimensions."""
    design = np.column_stack([np.ones(int(member.sum())), column[member], row[member]]).astype(np.float64)
    if len(design) < 3 or np.linalg.matrix_rank(design) < 3:
        return None
    solution, *_ = np.linalg.lstsq(design, points[member], rcond=None)
    return solution[0], solution[1], solution[2]


def _refine(points, weights, a1, a2, tolerance, anchor_index):
    """Grow the fit from the point nearest the middle (or the first anchor): assign, refit, with a looser tolerance first.
    ``(anchors on nodes, weight of the members, origin, a1, a2)`` or ``None``."""
    origin = points[anchor_index[0]] if anchor_index else points[int(np.argmin(np.hypot(*(points - np.median(points, axis=0)).T)))]
    previous: np.ndarray | None = None
    for tol in (EARLY_TOLERANCE, EARLY_TOLERANCE, EARLY_TOLERANCE, tolerance, tolerance, tolerance):
        tol = max(tol, tolerance)
        try:
            column, row, _residual, member = _assign(points, origin, a1, a2, tol)
        except np.linalg.LinAlgError:
            return None
        if member.sum() < 3:
            return None
        fit = _lstsq(points, column, row, member)
        if fit is None:
            return None
        origin, a1, a2 = fit
        if previous is not None and np.array_equal(previous, member) and tol == tolerance:
            break
        previous = member
    try:
        column, row, _residual, member = _assign(points, origin, a1, a2, tolerance)
    except np.linalg.LinAlgError:
        return None
    return int(member[anchor_index].sum()) if anchor_index else 0, float(weights[member].sum()), origin, a1, a2


def _member_count(points, best, tolerance) -> int:
    _score, origin, a1, a2 = best
    try:
        return int(_assign(points, origin, a1, a2, tolerance)[3].sum())
    except np.linalg.LinAlgError:
        return 0


def _line_fit(points, weights, tolerance, min_members):
    """One row of spots: the points lie on a line with a regular spacing. ``(score, origin, a1, a2)`` or ``None``."""
    centre = points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(points - centre, full_matrices=False)
    direction = vt[0] if vt[0][0] >= 0 else -vt[0]
    along = (points - centre) @ direction
    across = (points - centre) @ np.array([-direction[1], direction[0]])
    order = np.argsort(along)
    gaps = np.diff(along[order])
    gaps = gaps[gaps > 1e-6]
    if len(gaps) < min_members - 1:
        return None
    pitch = float(np.median(gaps))
    if pitch <= 0 or np.abs(across).max() > tolerance * pitch:
        return None
    a1 = direction * pitch
    a2 = np.array([-direction[1], direction[0]]) * pitch
    if a2[1] < 0:
        a2 = -a2
    try:
        column, row, _residual, member = _assign(points, centre + direction * float(along.min()), a1, a2, tolerance)
    except np.linalg.LinAlgError:
        return None
    member &= row == 0
    if member.sum() < min_members:
        return None
    return float(weights[member].sum()), centre + direction * float(along.min()), a1, a2


# -- result ---------------------------------------------------------------------------------


def _reduced(a1: np.ndarray, a2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The usual orientation: ``a1`` the shortest of the vectors most along x pointing right, ``a2`` the shortest independent one pointing down (on a tie, the one
    leaning right, so a triangular lattice has 60 degrees between them)."""
    vectors = [a1, a2]
    for _ in range(4):  # Gauss reduction: replace the longer vector by its shortest shift along the other
        vectors.sort(key=lambda v: float(np.hypot(*v)))
        shortest, longest = vectors
        k = round(float(np.dot(longest, shortest) / np.dot(shortest, shortest)))
        if k == 0:
            break
        vectors = [shortest, longest - k * shortest]
    first, second = sorted(vectors, key=lambda v: float(np.hypot(*v)))
    if abs(first[0]) < abs(second[0]):
        first, second = second, first
    if first[0] < 0:
        first = -first
    if second[1] < 0:
        second = -second
    # a pair at 120 degrees describes the same lattice as the pair at 60: take the one that leans right
    cosine = float(np.dot(first, second) / (np.hypot(*first) * np.hypot(*second)))
    if cosine < -0.35 and abs(np.hypot(*first) - np.hypot(*second)) < 0.05 * np.hypot(*first):  # 120 degrees (cos -0.5), not a slightly oblique rectangle
        second = second + first
    return first, second


def _connected_body(column: np.ndarray, row: np.ndarray, member: np.ndarray) -> np.ndarray:
    """Keep the members that belong to the biggest connected body of nodes (a gap of one empty node does not break a body). A stray point that happens to sit
    on a node far from the array would otherwise stretch the rows and columns to reach it."""
    index = np.flatnonzero(member)
    if len(index) < 2:
        return member
    cells = np.zeros((int(row[index].max() - row[index].min()) + 3, int(column[index].max() - column[index].min()) + 3), dtype=bool)
    r, c = row[index] - row[index].min() + 1, column[index] - column[index].min() + 1
    cells[r, c] = True
    labels, count = ndimage.label(ndimage.binary_dilation(cells, structure=np.ones((3, 3), dtype=bool)), structure=np.ones((3, 3), dtype=bool))
    if count <= 1:
        return member
    biggest = int(np.argmax(np.bincount(labels[r, c], minlength=count + 1)[1:])) + 1
    keep = member.copy()
    keep[index[labels[r, c] != biggest]] = False
    return keep


def _finish(points, origin, a1, a2, tolerance, one_dimensional, min_members) -> LatticeFit | None:
    if not one_dimensional:
        a1, a2 = _reduced(a1, a2)
    raw_column, raw_row, residual, member = _assign(points, origin, a1, a2, tolerance)
    if one_dimensional:
        member &= raw_row == 0
    member = _connected_body(raw_column, raw_row, member)
    if member.sum() < min_members:
        return None
    first_column, first_row = int(raw_column[member].min()), int(raw_row[member].min())
    return LatticeFit(
        origin=np.asarray(origin + first_column * a1 + first_row * a2, dtype=np.float64),  # node (0, 0) = the first column / row a member sits in
        a1=np.asarray(a1, dtype=np.float64), a2=np.asarray(a2, dtype=np.float64),
        column=raw_column - first_column, row=raw_row - first_row, member=member, residual_px=residual, one_dimensional=one_dimensional,
    )
