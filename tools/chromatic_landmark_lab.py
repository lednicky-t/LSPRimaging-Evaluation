"""Offline lab for automatic chromatic-correction landmarks (LSPRi rewrite).

Standalone experiment, NOT part of the app: no Qt, no dataset module, no
imports from `lspr_imaging_app`. Goal: find out which landmark strategy is
good enough before any of it is built into the Chromatic Corrections tab.

Human-style workflow it imitates
  1. Reference frame (default 600 nm). Find the particle spots on it.
  2. Pick `nx x ny` evenly spread spots inside a rectangle (default: image
     minus a 5 % border), one landmark each.
  3. Walk outward from the reference, one wavelength at a time (up and down).
     Every step only searches `--max-step` px around the previous position
     (neighbouring wavelengths move very little) and compares *local contrast*
     patches, so a change of overall brightness does not matter.
  4. Per step, a robust similarity model (shift + scale + rotation) is fitted
     to that step's displacements; landmarks that disagree with it are flagged
     (red in the viewer) and excluded from the final fit. Nothing is silently
     replaced: flagged landmarks keep their status.
  5. Final model per wavelength: reference -> wavelength similarity transform,
     with in-sample RMSE and leave-one-out RMSE (honest error estimate).

Output: an HTML viewer (open in a browser) with a wavelength slider, landmark
overlays, displacement magnification and diagnostic plots.

Examples
  python chromatic_landmark_lab.py DATA\\images --grid 5x3
  python chromatic_landmark_lab.py DATA\\images --grid 5x3 --downscale 2 --baseline OUT\\lab_5x3.json

Assumptions: features are darker than the background at EVERY wavelength (no
contrast flipping). Feature size is measured from the reference (or given with
--feature-diameter); shape is never tested - a candidate only has to be
localisable in both x and y. Tracking = patch cross-correlation on contrast
maps. Anchor: prev (template from previous wavelength, can drift) or ref
(template always from reference, no drift, harder far from reference).
Stress options (--downscale/--noise/--contrast) degrade the input on purpose;
--baseline compares the fitted scale-vs-wavelength curve with a normal run.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import tifffile
from scipy import ndimage
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.optimize import linear_sum_assignment

# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def load_frames(folder: Path, frame: int) -> dict[float, np.ndarray]:
    """{wavelength_nm: image} for one frame index; wavelength 0 is skipped."""
    pattern = re.compile(rf"^imLCTFatWL(\d+)Frame{frame}\.tiff?$")
    frames: dict[float, np.ndarray] = {}
    for path in folder.iterdir():
        match = pattern.match(path.name)
        if match and int(match.group(1)) != 0:
            frames[float(match.group(1))] = tifffile.imread(path).astype(np.float32)
    if not frames:
        raise SystemExit(f"No imLCTFatWL*Frame{frame}.tiff files found in {folder}")
    return dict(sorted(frames.items()))


# --------------------------------------------------------------------------
# contrast map + automatic feature size + candidate landmarks
# --------------------------------------------------------------------------
# Physical assumptions (maintainer, 2026-10-04): features are ALWAYS darker
# than the background, at EVERY wavelength (no contrast flipping). Shape,
# size and placement may vary with the sample design, so none of them is
# hard-coded: size is measured, shape is never tested, and a candidate is
# accepted only if it can be localised in both x and y.


def _odd(value: float) -> int:
    n = int(round(value))
    return n if n % 2 else n + 1


def patch_half(radius: float) -> int:
    """Half-size of the matching patch: a bit more than the feature itself so
    its edge and some surround are included."""
    return max(int(np.ceil(1.4 * radius)), 5)


def contrast_map(image: np.ndarray, background_px: int = 101, smooth_sigma: float = 1.5) -> np.ndarray:
    """(background - image) / background: dark features become positive.

    Background = grey closing (removes dark features smaller than
    `background_px`, so keep it larger than the biggest feature), computed
    on a half-resolution copy for speed since the background is smooth. A
    relative contrast makes every wavelength comparable even though overall
    brightness and feature depth change with wavelength."""
    h, w = image.shape
    smooth = ndimage.gaussian_filter(image, smooth_sigma)
    small = cv2.resize(smooth, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    background = ndimage.grey_closing(small, size=(_odd(background_px / 2),) * 2)
    background = ndimage.gaussian_filter(background, 3.0)
    background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR)
    return ((background - smooth) / np.maximum(background, 1.0)).astype(np.float32)


def estimate_feature_radius(contrast: np.ndarray) -> float:
    """Typical feature radius (px) from a scale-space blob search: the
    scale-normalised Laplacian-of-Gaussian of a blob of radius r peaks at
    sigma = r / sqrt(2). Median over the strongest peaks, so a few odd
    objects (debris, scratches) do not matter. No shape assumption beyond
    "roughly blob-sized"."""
    small = cv2.resize(contrast, (contrast.shape[1] // 2, contrast.shape[0] // 2), interpolation=cv2.INTER_AREA)
    sigmas = np.geomspace(1.0, 25.0, 14)
    stack = np.stack([-(s**2) * ndimage.gaussian_laplace(small, s) for s in sigmas])
    best, which = stack.max(axis=0), stack.argmax(axis=0)
    peak = (best == ndimage.maximum_filter(best, size=9)) & (best > 0.3 * best.max())
    ys, xs = np.nonzero(peak)
    top = np.argsort(best[ys, xs])[::-1][:30]
    if len(top) == 0:
        raise SystemExit("Could not estimate feature size - pass --feature-diameter")
    sigma = float(np.median(sigmas[which[ys[top], xs[top]]]))
    return max(sigma * np.sqrt(2.0) * 2.0, 2.0)  # x2: half-resolution map


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 1e-12 else 1.0


def trackability(contrast: np.ndarray, pos, radius: float) -> float:
    """How well a patch can be localised: 1 - (worst-direction correlation of
    the patch with a copy of itself shifted by a small step, 8 directions).
    Round blobs, corners, irregular shapes score well; a stripe scores ~0
    because it can slide along its own length unnoticed. Shape-agnostic."""
    size = 2 * patch_half(radius) + 1
    d = max(1.5, 0.1 * radius)
    base = cv2.getRectSubPix(contrast, (size, size), (float(pos[0]), float(pos[1])))
    worst = -1.0
    for k in range(8):
        a = 2 * np.pi * k / 8
        shifted = cv2.getRectSubPix(contrast, (size, size), (float(pos[0] + d * np.cos(a)), float(pos[1] + d * np.sin(a))))
        worst = max(worst, _ncc(base, shifted))
    return 1.0 - worst


def find_candidates(contrast: np.ndarray, radius: float, max_step: float, strength_frac: float = 0.3):
    """Blob-like dark features at the measured size: [(x, y, strength, trackability)].
    Strength is the scale-normalised blob response; features weaker than
    `strength_frac` x the typical strong one are ignored. Features too close
    to the image edge for a full patch plus search window are dropped."""
    h, w = contrast.shape
    sigma = radius / np.sqrt(2.0)
    response = -(sigma**2) * ndimage.gaussian_laplace(contrast, sigma)
    footprint = _odd(3.0 * radius)
    peak = (response == ndimage.maximum_filter(response, size=footprint)) & (response > 0)
    ys, xs = np.nonzero(peak)
    values = response[ys, xs]
    if len(values) == 0:
        return []
    reference = float(np.median(np.sort(values)[::-1][:20]))
    margin = patch_half(radius) + int(np.ceil(max_step)) + 3
    out = []
    for x, y, v in zip(xs, ys, values):
        if v < strength_frac * reference or not (margin <= x < w - margin and margin <= y < h - margin):
            continue
        dx = _parabolic(response[y, :], int(x)) if 0 < x < w - 1 else 0.0
        dy = _parabolic(response[:, x], int(y)) if 0 < y < h - 1 else 0.0
        pos = (float(x + dx), float(y + dy))
        out.append((pos[0], pos[1], float(v), trackability(contrast, pos, radius)))
    return out


def select_landmarks(candidates, grid: tuple[int, int], bounds, quality_frac: float = 0.3):
    """Evenly spread landmarks: one candidate per node of an nx x ny grid laid
    over `bounds`, assigned optimally (Hungarian) so none is used twice.
    Candidates whose trackability is below `quality_frac` x the median are
    dropped first (stripes, smeared or ambiguous features)."""
    nx, ny = grid
    x0, y0, x1, y1 = bounds
    xs = np.linspace(x0, x1, nx) if nx > 1 else np.array([(x0 + x1) / 2])
    ys = np.linspace(y0, y1, ny) if ny > 1 else np.array([(y0 + y1) / 2])
    nodes = np.array([(x, y) for y in ys for x in xs])
    inside = [c for c in candidates if x0 - 20 <= c[0] <= x1 + 20 and y0 - 20 <= c[1] <= y1 + 20]
    if inside:
        floor = quality_frac * float(np.median([c[3] for c in inside]))
        inside = [c for c in inside if c[3] >= floor]
    if len(inside) < len(nodes):
        raise SystemExit(f"Only {len(inside)} usable features inside bounds, need {len(nodes)}")
    pts = np.array([(c[0], c[1]) for c in inside])
    cost = np.hypot(nodes[:, None, 0] - pts[None, :, 0], nodes[:, None, 1] - pts[None, :, 1])
    rows, cols = linear_sum_assignment(cost)
    return [(float(pts[c][0]), float(pts[c][1])) for r, c in sorted(zip(rows, cols))]


# --------------------------------------------------------------------------
# tracking one step
# --------------------------------------------------------------------------


def _parabolic(values: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(values) - 1:
        return 0.0
    a, b, c = values[i - 1], values[i], values[i + 1]
    denom = a - 2 * b + c
    return 0.0 if abs(denom) < 1e-12 else float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))


def track_ncc(template_map, target_map, template_pos, search_pos, radius, max_step):
    """Cross-correlate a contrast patch around `template_pos` (in
    `template_map`) against `target_map` near `search_pos`.
    Returns (x, y, score, hit_limit)."""
    size = 2 * patch_half(radius) + 1
    template = cv2.getRectSubPix(template_map, (size, size), (float(template_pos[0]), float(template_pos[1])))
    cx, cy = int(round(search_pos[0])), int(round(search_pos[1]))
    s = int(np.ceil(max_step)) + 1
    region = cv2.getRectSubPix(target_map, (size + 2 * s, size + 2 * s), (float(cx), float(cy)))
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    # only offsets within the allowed step radius count
    jj, ii = np.mgrid[0:result.shape[0], 0:result.shape[1]]
    allowed = np.hypot(ii - s, jj - s) <= max_step + 0.5
    masked = np.where(allowed, result, -1.0)
    j, i = np.unravel_index(int(np.argmax(masked)), masked.shape)
    dx = _parabolic(result[j, :], i) if 0 < i < result.shape[1] - 1 else 0.0
    dy = _parabolic(result[:, i], j) if 0 < j < result.shape[0] - 1 else 0.0
    hit_limit = bool(np.hypot(i - s, j - s) >= max_step - 0.5)
    return cx + (i - s) + dx, cy + (j - s) + dy, float(masked[j, i]), hit_limit


# --------------------------------------------------------------------------
# similarity model (complex form: z' = a z + b, a = s e^{i theta})
# --------------------------------------------------------------------------


def fit_similarity(src: np.ndarray, dst: np.ndarray):
    """Least-squares similarity src->dst. Returns (a, b) complex."""
    z = src[:, 0] + 1j * src[:, 1]
    w = dst[:, 0] + 1j * dst[:, 1]
    zc, wc = z.mean(), w.mean()
    dz, dw = z - zc, w - wc
    denom = float((np.abs(dz) ** 2).sum())
    a = (np.conj(dz) * dw).sum() / denom if denom > 0 else 1.0 + 0j
    return a, wc - a * zc


def apply_similarity(a: complex, b: complex, pts: np.ndarray) -> np.ndarray:
    out = a * (pts[:, 0] + 1j * pts[:, 1]) + b
    return np.column_stack([out.real, out.imag])


def robust_similarity(src, dst, floor_px=0.4, k=3.5):
    """Similarity fit with iterative rejection of landmarks whose residual is
    much larger than the typical (MAD-based) one. Returns (a, b, inlier_mask).
    Rejection never goes below 4 inliers."""
    inlier = np.ones(len(src), dtype=bool)
    a, b = fit_similarity(src, dst)
    for _ in range(5):
        res = np.hypot(*(apply_similarity(a, b, src) - dst).T)
        scale = 1.4826 * float(np.median(res[inlier])) if inlier.any() else 0.0
        limit = max(floor_px, k * scale)
        new = res <= limit
        if new.sum() < 4 or np.array_equal(new, inlier):
            break
        inlier = new
        a, b = fit_similarity(src[inlier], dst[inlier])
    return a, b, inlier


# --------------------------------------------------------------------------
# main tracking loop
# --------------------------------------------------------------------------


class Cancelled(Exception):
    """Raised by the pipeline when the caller's `cancelled()` returns True."""


class Timings(dict):
    """Wall-clock seconds per named stage (accumulating)."""

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self[name] = self.get(name, 0.0) + time.perf_counter() - start


# Share of the overall progress bar each stage gets (sums to 1). Set from the
# measured timings so the bar moves at a steady pace; see the benchmark output.
STAGE_WEIGHTS = {"contrast": 0.25, "features": 0.15, "tracking": 0.55, "fit": 0.05}


class StageProgress:
    """Maps (stage, fraction-within-stage) to one overall 0..1 fraction and
    forwards it, with a short message, to `callback(fraction, message)`.
    Also the single place cancellation is checked, so every stage stays
    cancellable. This is the shape a GUI panel consumes (see CLAUDE.md rule
    "Heavy work reports progress")."""

    def __init__(self, callback=None, cancelled=None):
        self._callback = callback
        self._cancelled = cancelled
        self._offsets = {}
        total = 0.0
        for name, weight in STAGE_WEIGHTS.items():
            self._offsets[name] = total
            total += weight

    def __call__(self, stage: str, fraction: float, message: str = "") -> None:
        if self._cancelled is not None and self._cancelled():
            raise Cancelled()
        if self._callback is not None:
            overall = self._offsets[stage] + STAGE_WEIGHTS[stage] * float(np.clip(fraction, 0.0, 1.0))
            self._callback(overall, message or stage)


def track_all(maps, ref_sub, ref_positions, radius, args, gaps, progress):
    """Track landmarks outward from `ref_sub` over `maps` (indexed by sub-list
    position). `gaps[i]` = how many original wavelength steps separate sub-
    list entry i from its neighbour towards the reference (stride > 1 means
    bigger moves, so the search window grows with it)."""
    n = len(ref_positions)
    pos = {ref_sub: np.array(ref_positions, dtype=float)}
    score = {ref_sub: np.ones(n)}
    status = {ref_sub: ["ok"] * n}
    total_steps = max(len(maps) - 1, 1)
    done = 0
    for direction in (+1, -1):
        prev_i = ref_sub
        i = ref_sub + direction
        while 0 <= i < len(maps):
            limit = args.max_step * gaps[i]
            prev_pos = pos[prev_i]
            new_pos = np.zeros((n, 2))
            new_score = np.zeros(n)
            limit_hit = np.zeros(n, dtype=bool)
            t_i = ref_sub if args.anchor == "ref" else prev_i
            for k in range(n):
                x, y, sc, lim = track_ncc(maps[t_i], maps[i], pos[t_i][k], prev_pos[k], radius, limit)
                new_pos[k] = (x, y)
                new_score[k] = sc
                limit_hit[k] = lim
            # step model from landmarks that look fine, then flag disagreeing ones
            good = new_score >= args.min_score
            st = ["ok"] * n
            if good.sum() >= 4:
                a, b, inl = robust_similarity(prev_pos[good], new_pos[good], floor_px=0.4 * gaps[i])
                idx = np.nonzero(good)[0]
                for local, k in enumerate(idx):
                    if not inl[local]:
                        st[k] = "outlier"
                predicted = apply_similarity(a, b, prev_pos)
            else:
                predicted = prev_pos.copy()
            for k in range(n):
                if not good[k]:
                    st[k] = "lowscore"
                elif limit_hit[k] and st[k] == "ok":
                    st[k] = "atlimit"
            # flagged landmarks continue from the step model's prediction (they may recover)
            for k in range(n):
                if st[k] != "ok":
                    new_pos[k] = predicted[k]
            pos[i], score[i], status[i] = new_pos, new_score, st
            prev_i = i
            i += direction
            done += 1
            progress("tracking", done / total_steps, f"tracking {done}/{total_steps}")
    return pos, score, status


def final_models(ref_sub, pos, status):
    """Reference -> wavelength similarity per tracked wavelength, from the
    landmarks still marked ok, with in-sample and leave-one-out RMSE."""
    ref = pos[ref_sub]
    models = {}
    for i in pos:
        used = np.array([s == "ok" for s in status[i]])
        if i == ref_sub or used.sum() < 4:
            models[i] = dict(a=1 + 0j, b=0j, used=used, rmse=0.0, loo=0.0, n=int(used.sum()))
            continue
        src, dst = ref[used], pos[i][used]
        a, b = fit_similarity(src, dst)
        res = np.hypot(*(apply_similarity(a, b, src) - dst).T)
        loo = []
        for j in range(len(src)):
            keep = np.arange(len(src)) != j
            aj, bj = fit_similarity(src[keep], dst[keep])
            loo.append(float(np.hypot(*(apply_similarity(aj, bj, src[j:j + 1])[0] - dst[j]))))
        models[i] = dict(a=a, b=b, used=used, rmse=float(np.sqrt(np.mean(res**2))), loo=float(np.sqrt(np.mean(np.square(loo)))), n=int(used.sum()))
    return models


def tracked_indices(count: int, ref_index: int, stride: int) -> list[int]:
    """Which wavelength indices are tracked: the reference, every `stride`-th
    step outward from it, and always both ends (interpolation cannot
    extrapolate)."""
    chosen = {ref_index, 0, count - 1}
    chosen.update(range(ref_index, count, stride))
    chosen.update(range(ref_index, -1, -stride))
    return sorted(chosen)


def interpolate_models(wls, tracked, sub_models, kind: str):
    """Similarity coefficients (complex a, b) for every wavelength: tracked
    ones as fitted, the rest interpolated over wavelength. `sub_models` is
    keyed by position in `tracked`."""
    w_tr = np.array([wls[i] for i in tracked])
    a_tr = np.array([sub_models[s]["a"] for s in range(len(tracked))])
    b_tr = np.array([sub_models[s]["b"] for s in range(len(tracked))])
    w_all = np.array(wls)

    def interp(values):
        if kind == "linear" or len(tracked) < 4:
            re, im = np.interp(w_all, w_tr, values.real), np.interp(w_all, w_tr, values.imag)
        else:
            cls = CubicSpline if kind == "cubic" else PchipInterpolator
            re, im = cls(w_tr, values.real)(w_all), cls(w_tr, values.imag)(w_all)
        return re + 1j * im

    return interp(a_tr), interp(b_tr)


def run_pipeline(images, wls, ref_index, grid, bounds, args, progress=None, cancelled=None):
    """The whole analysis as one pure function (no Qt, no files, no globals):
    images in, landmarks + per-wavelength transforms out. Reports progress
    through `progress(fraction, message)` and stops with `Cancelled` if
    `cancelled()` becomes True. Returns a dict, including per-stage timings."""
    report = StageProgress(progress, cancelled)
    timings = Timings()
    count = len(wls)
    tracked = tracked_indices(count, ref_index, args.stride)
    ref_sub = tracked.index(ref_index)
    gaps = {}
    for s, i in enumerate(tracked):
        toward = s - 1 if s > ref_sub else s + 1
        gaps[s] = abs(i - tracked[toward]) if s != ref_sub and 0 <= toward < len(tracked) else 1

    with timings.stage("feature size"):
        if args.feature_diameter:
            radius = args.feature_diameter / 2.0
        else:
            radius = estimate_feature_radius(contrast_map(images[ref_index], 101, 1.0))
    background_px = int(np.clip(_odd(6.0 * radius), 31, 251))
    smooth_sigma = float(np.clip(0.1 * radius, 0.8, 2.0))

    maps = []
    with timings.stage("contrast maps"):
        for s, i in enumerate(tracked):
            maps.append(contrast_map(images[i], background_px, smooth_sigma))
            report("contrast", (s + 1) / len(tracked), f"preparing image {s + 1}/{len(tracked)}")

    with timings.stage("feature search"):
        candidates = find_candidates(maps[ref_sub], radius, args.max_step * max(gaps.values()), args.strength_frac)
        ref_positions = select_landmarks(candidates, grid, bounds, args.quality_frac)
        report("features", 1.0, "landmarks selected")

    with timings.stage("tracking"):
        pos, score, status = track_all(maps, ref_sub, ref_positions, radius, args, gaps, report)

    with timings.stage("fit"):
        sub_models = final_models(ref_sub, pos, status)
        a_all, b_all = interpolate_models(wls, tracked, sub_models, args.interp)
        report("fit", 1.0, "done")

    ref_pts = pos[ref_sub]
    models, all_pos, all_score, all_status = {}, {}, {}, {}
    sub_of = {i: s for s, i in enumerate(tracked)}
    for i in range(count):
        if i in sub_of:
            s = sub_of[i]
            m = dict(sub_models[s])
            all_pos[i], all_score[i], all_status[i] = pos[s], score[s], status[s]
        else:
            m = dict(used=np.zeros(len(ref_pts), dtype=bool), rmse=float("nan"), loo=float("nan"), n=0)
            all_status[i] = ["interp"] * len(ref_pts)
            all_score[i] = np.zeros(len(ref_pts))
        m["a"], m["b"] = complex(a_all[i]), complex(b_all[i])
        if i not in sub_of:
            all_pos[i] = apply_similarity(m["a"], m["b"], ref_pts)
        models[i] = m
    return dict(radius=radius, tracked=tracked, ref_positions=ref_pts, pos=all_pos, score=all_score,
                status=all_status, models=models, timings=timings, n_candidates=len(candidates))


# --------------------------------------------------------------------------
# HTML viewer
# --------------------------------------------------------------------------


def write_frames(out_dir: Path, wls, images):
    frames = out_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for wl, img in zip(wls, images):
        lo, hi = np.percentile(img, [1, 99.7])
        v = np.clip((img - lo) / max(hi - lo, 1), 0, 1)
        cv2.imwrite(str(frames / f"wl{int(wl)}.jpg"), (v * 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 88])


def build_payload(wls, ref_index, pos, score, status, models, shape, bounds, args, radius):
    h, w = shape
    cx, cy = w / 2, h / 2
    frames = []
    for i, wl in enumerate(wls):
        m = models[i]
        pred = apply_similarity(m["a"], m["b"], pos[ref_index])
        centre = m["a"] * (cx + 1j * cy) + m["b"] - (cx + 1j * cy)
        frames.append(
            dict(
                wl=wl,
                pos=np.round(pos[i], 3).tolist(),
                pred=np.round(pred, 3).tolist(),
                score=np.round(score[i], 3).tolist(),
                status=status[i],
                scale_ppm=float((abs(m["a"]) - 1) * 1e6),
                rot_mrad=float(np.angle(m["a"]) * 1e3),
                cx=float(centre.real),
                cy=float(centre.imag),
                rmse=m["rmse"],
                loo=m["loo"],
                n=m["n"],
            )
        )
    return dict(
        width=w, height=h, ref_index=ref_index, bounds=bounds, radius=radius,
        title=f"{args.grid} grid, feature d={2 * radius:.0f}px, anchor={args.anchor}, max_step={args.max_step}px",
        frames=frames,
    )


HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Chromatic landmark lab</title>
<style>
body{margin:0;background:#15171a;color:#d8dbe0;font:13px system-ui,sans-serif;display:flex;height:100vh}
#left{flex:1;display:flex;flex-direction:column;min-width:0}
#bar{padding:6px 10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:#1d2024;border-bottom:1px solid #2c3036}
#bar input[type=range]{width:300px} label{white-space:nowrap}
#wrap{flex:1;position:relative;overflow:hidden} canvas#main{position:absolute;inset:0;width:100%;height:100%}
#right{width:430px;overflow:auto;background:#1a1c20;border-left:1px solid #2c3036;padding:8px}
#right canvas{width:100%;height:150px;background:#101215;margin-bottom:6px;border:1px solid #2c3036}
table{border-collapse:collapse;width:100%;font-size:11.5px} td,th{padding:1px 5px;text-align:right} th{color:#8b93a0}
tr.bad td{color:#ff7b72} h4{margin:6px 0 2px;color:#9aa4b2;font-weight:600}
.big{font-size:20px;font-weight:600}
</style></head><body>
<div id="left"><div id="bar">
<span class="big" id="wl"></span>
<input type="range" id="slider" min="0" value="0">
<button id="play">play</button>
<label>displacement ×<input type="range" id="mag" min="1" max="40" value="1" style="width:110px"><b id="magv">1</b></label>
<label><input type="checkbox" id="showRef" checked> reference</label>
<label><input type="checkbox" id="showPred" checked> fit prediction</label>
<label><input type="checkbox" id="showTrail"> trails</label>
<label><input type="checkbox" id="showBounds" checked> bounds</label>
<span id="title" style="color:#8b93a0"></span>
</div><div id="wrap"><canvas id="main"></canvas></div></div>
<div id="right">
<h4>scale vs wavelength (ppm, about reference)</h4><canvas id="p1" width="800" height="300"></canvas>
<h4>image-centre shift (px): x blue, y orange</h4><canvas id="p2" width="800" height="300"></canvas>
<h4>fit error (px): RMSE solid, leave-one-out dashed</h4><canvas id="p3" width="800" height="300"></canvas>
<h4>dx vs x (px) at this wavelength</h4><canvas id="p4" width="800" height="300"></canvas>
<h4>dy vs y (px) at this wavelength</h4><canvas id="p5" width="800" height="300"></canvas>
<h4>landmarks at this wavelength</h4><table id="tbl"></table>
</div>
<script>
const D=__DATA__;
const F=D.frames,N=F[0].pos.length,imgs=F.map(f=>{const i=new Image();i.src=D.frames_dir+'/wl'+Math.round(f.wl)+'.jpg';i.onload=draw;return i});
const $=id=>document.getElementById(id),cv=$('main'),ctx=cv.getContext('2d');
let cur=D.ref_index,zoom=1,panx=0,pany=0,fitted=false;
$('slider').max=F.length-1;$('slider').value=cur;$('title').textContent=D.title;
function resize(){cv.width=cv.clientWidth;cv.height=cv.clientHeight;if(!fitted){zoom=Math.min(cv.width/D.width,cv.height/D.height);panx=(cv.width-D.width*zoom)/2;pany=(cv.height-D.height*zoom)/2;fitted=true}draw()}
addEventListener('resize',resize);
const mag=()=>+$('mag').value;
function P(x,y,k){const r=F[D.ref_index].pos[k];return [panx+(r[0]+(x-r[0])*mag())*zoom,pany+(r[1]+(y-r[1])*mag())*zoom]}
function cross(x,y,s){ctx.beginPath();ctx.moveTo(x-s,y);ctx.lineTo(x+s,y);ctx.moveTo(x,y-s);ctx.lineTo(x,y+s);ctx.stroke()}
function draw(){
 ctx.fillStyle='#0c0d0f';ctx.fillRect(0,0,cv.width,cv.height);
 const im=imgs[cur];if(im.complete&&im.naturalWidth)ctx.drawImage(im,panx,pany,D.width*zoom,D.height*zoom);
 if($('showBounds').checked){const b=D.bounds;ctx.strokeStyle='#58a6ff55';ctx.setLineDash([6,4]);ctx.strokeRect(panx+b[0]*zoom,pany+b[1]*zoom,(b[2]-b[0])*zoom,(b[3]-b[1])*zoom);ctx.setLineDash([])}
 const f=F[cur],ref=F[D.ref_index],rr=Math.max(D.radius*zoom,4);
 if($('showTrail').checked){ctx.lineWidth=1.5;for(let k=0;k<N;k++){ctx.strokeStyle='#d2a8ff';ctx.beginPath();F.forEach((g,i)=>{const [x,y]=P(g.pos[k][0],g.pos[k][1],k);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}}
 for(let k=0;k<N;k++){
  const r=ref.pos[k],rx=panx+r[0]*zoom,ry=pany+r[1]*zoom;
  if($('showRef').checked){ctx.strokeStyle='#ffffff88';ctx.lineWidth=1;ctx.beginPath();ctx.arc(rx,ry,rr,0,7);ctx.stroke()}
  const [x,y]=P(f.pos[k][0],f.pos[k][1],k),ok=f.status[k]=='ok',ip=f.status[k]=='interp';
  ctx.strokeStyle=ok?'#3fb950':ip?'#58a6ff':'#ff5555';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,rr*0.8,0,7);ctx.stroke();cross(x,y,rr*0.5);
  if(mag()>1||cur!=D.ref_index){ctx.strokeStyle='#ffd33d99';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(rx,ry);ctx.lineTo(x,y);ctx.stroke()}
  if($('showPred').checked){const [px,py]=P(f.pred[k][0],f.pred[k][1],k);ctx.strokeStyle='#58a6ff';ctx.lineWidth=1.5;cross(px,py,rr*0.35)}
  ctx.fillStyle='#fff';ctx.font='11px sans-serif';ctx.fillText(k+1,rx+rr*0.5,ry-rr*0.9);
 }
 $('wl').textContent=f.wl+' nm'+(cur==D.ref_index?' (reference)':'');$('magv').textContent=mag();
 plots();table();
}
function plot(id,series,opt){
 const c=$(id),g=c.getContext('2d'),W=c.width,H=c.height,m={l:46,r:10,t:10,b:22};g.clearRect(0,0,W,H);
 let xs=opt.x,ys=series.flatMap(s=>s.y.filter(v=>isFinite(v)));
 let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys,opt.zero?0:1e9),y1=Math.max(...ys,opt.zero?0:-1e9);if(y0==y1){y0-=1;y1+=1}
 const pad=(y1-y0)*.08;y0-=pad;y1+=pad;if(x0==x1)x1=x0+1;
 const X=v=>m.l+(v-x0)/(x1-x0)*(W-m.l-m.r),Y=v=>H-m.b-(v-y0)/(y1-y0)*(H-m.t-m.b);
 g.strokeStyle='#2c3036';g.fillStyle='#8b93a0';g.font='20px sans-serif';g.lineWidth=1;
 for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4,y=Y(v);g.beginPath();g.moveTo(m.l,y);g.lineTo(W-m.r,y);g.stroke();g.fillText(v.toFixed(Math.abs(y1-y0)<5?2:0),2,y+6)}
 g.fillText(x0.toFixed(0),m.l,H-4);g.fillText(x1.toFixed(0),W-m.r-40,H-4);
 if(opt.zero){g.strokeStyle='#555';g.beginPath();g.moveTo(m.l,Y(0));g.lineTo(W-m.r,Y(0));g.stroke()}
 if(opt.vline!==undefined){g.strokeStyle='#ffd33d';g.beginPath();g.moveTo(X(opt.vline),m.t);g.lineTo(X(opt.vline),H-m.b);g.stroke()}
 series.forEach(s=>{g.strokeStyle=s.color;g.fillStyle=s.color;g.lineWidth=2;g.setLineDash(s.dash||[]);
  if(s.points){s.y.forEach((v,i)=>{if(isFinite(v)){g.beginPath();g.arc(X(s.x[i]),Y(v),3.5,0,7);g.fill()}})}
  else{g.beginPath();s.y.forEach((v,i)=>{const px=X(xs[i]),py=Y(v);i?g.lineTo(px,py):g.moveTo(px,py)});g.stroke()}g.setLineDash([])})
}
function plots(){
 const w=F.map(f=>f.wl),v=F[cur].wl;
 plot('p1',[{y:F.map(f=>f.scale_ppm),color:'#3fb950'}],{x:w,zero:1,vline:v});
 plot('p2',[{y:F.map(f=>f.cx),color:'#58a6ff'},{y:F.map(f=>f.cy),color:'#f0883e'}],{x:w,zero:1,vline:v});
 plot('p3',[{y:F.map(f=>f.rmse),color:'#3fb950'},{y:F.map(f=>f.loo),color:'#ff7b72',dash:[8,5]}],{x:w,zero:1,vline:v});
 const f=F[cur],r=F[D.ref_index],xr=r.pos.map(p=>p[0]),yr=r.pos.map(p=>p[1]);
 const dx=f.pos.map((p,k)=>p[0]-r.pos[k][0]),dy=f.pos.map((p,k)=>p[1]-r.pos[k][1]);
 const px=f.pred.map((p,k)=>p[0]-r.pos[k][0]),py=f.pred.map((p,k)=>p[1]-r.pos[k][1]);
 const col=f.status.map(s=>s=='ok'||s=='interp');
 const sc=(a,c)=>({x:a,y:null,points:1,color:c});
 function sp(id,xv,meas,pred){const okx=xv.filter((_,k)=>col[k]),oky=meas.filter((_,k)=>col[k]);
  plot(id,[{x:xv,y:pred,color:'#58a6ff',points:1},{x:okx,y:oky,color:'#3fb950',points:1},{x:xv.filter((_,k)=>!col[k]),y:meas.filter((_,k)=>!col[k]),color:'#ff5555',points:1}],{x:xv,zero:1})}
 sp('p4',xr,dx,px);sp('p5',yr,dy,py);
}
function table(){
 const f=F[cur],r=F[D.ref_index];let h='<tr><th>#</th><th>dx</th><th>dy</th><th>score</th><th>status</th><th>vs fit</th></tr>';
 for(let k=0;k<N;k++){const dx=f.pos[k][0]-r.pos[k][0],dy=f.pos[k][1]-r.pos[k][1],e=Math.hypot(f.pos[k][0]-f.pred[k][0],f.pos[k][1]-f.pred[k][1]);
  h+=`<tr class="${f.status[k]=='ok'||f.status[k]=='interp'?'':'bad'}"><td>${k+1}</td><td>${dx.toFixed(2)}</td><td>${dy.toFixed(2)}</td><td>${f.score[k].toFixed(2)}</td><td>${f.status[k]}</td><td>${e.toFixed(2)}</td></tr>`}
 $('tbl').innerHTML=h+`<tr><td colspan=6 style="text-align:left;color:#8b93a0">used in fit: ${f.n}/${N}  RMSE ${f.rmse.toFixed(3)}px  LOO ${f.loo.toFixed(3)}px</td></tr>`}
$('slider').oninput=e=>{cur=+e.target.value;draw()};
$('mag').oninput=draw;['showRef','showPred','showTrail','showBounds'].forEach(i=>$(i).onchange=draw);
let timer=null;$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null}else timer=setInterval(()=>{cur=(cur+1)%F.length;$('slider').value=cur;draw()},350)};
addEventListener('keydown',e=>{if(e.key=='ArrowRight')cur=Math.min(cur+1,F.length-1);else if(e.key=='ArrowLeft')cur=Math.max(cur-1,0);else return;$('slider').value=cur;draw()});
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,z=Math.exp(-e.deltaY*0.0015);panx=mx-(mx-panx)*z;pany=my-(my-pany)*z;zoom*=z;draw()},{passive:false});
let drag=null;cv.onmousedown=e=>drag=[e.clientX,e.clientY,panx,pany];addEventListener('mouseup',()=>drag=null);
addEventListener('mousemove',e=>{if(drag){panx=drag[2]+e.clientX-drag[0];pany=drag[3]+e.clientY-drag[1];draw()}});
resize();
</script></body></html>"""


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def grid_error_vs_baseline(wls, models, base, shape):
    """Difference (px) between this run's transforms and a baseline run's,
    evaluated on a dense point grid over the whole image, per wavelength.
    Returns (rms_per_wl, max_per_wl)."""
    h, w = shape
    gx, gy = np.meshgrid(np.linspace(0, w, 14), np.linspace(0, h, 10))
    z = gx.ravel() + 1j * gy.ravel()
    rms, worst = [], []
    for i in range(len(wls)):
        mine = models[i]["a"] * z + models[i]["b"]
        theirs = (base["a_re"][i] + 1j * base["a_im"][i]) * z + (base["b_re"][i] + 1j * base["b_im"][i])
        d = np.abs(mine - theirs)
        rms.append(float(np.sqrt(np.mean(d**2))))
        worst.append(float(d.max()))
    return np.array(rms), np.array(worst)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("images", type=Path, help="folder with imLCTFatWL<wl>Frame<n>.tiff")
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--ref-wl", type=float, default=600.0)
    p.add_argument("--grid", default="5x3", help="landmarks as NXxNY, e.g. 5x3, 8x4, 10x6")
    p.add_argument("--border", type=float, default=0.05, help="border fraction excluded from landmark search")
    p.add_argument("--bounds", default=None, help="x0,y0,x1,y1 in px (overrides --border)")
    p.add_argument("--anchor", choices=["prev", "ref"], default="prev")
    p.add_argument("--max-step", type=float, default=5.0, help="max shift (px) between neighbouring wavelengths")
    p.add_argument("--min-score", type=float, default=0.5)
    p.add_argument("--stride", type=int, default=1, help="track every Nth wavelength (from the reference outward), interpolate the rest")
    p.add_argument("--interp", choices=["linear", "pchip", "cubic"], default="pchip", help="how skipped wavelengths are filled in")
    p.add_argument("--feature-diameter", type=float, default=None, help="feature size in px (default: measured from the reference)")
    p.add_argument("--strength-frac", type=float, default=0.3, help="ignore features weaker than this x typical strong one")
    p.add_argument("--quality-frac", type=float, default=0.3, help="drop features less localisable than this x median")
    p.add_argument("--downscale", type=float, default=1.0, help="stress test: shrink images (smaller features)")
    p.add_argument("--noise", type=float, default=0.0, help="stress test: gaussian noise, fraction of image level")
    p.add_argument("--contrast", type=float, default=1.0, help="stress test: scale feature contrast (0.3 = 30 percent)")
    p.add_argument("--baseline", type=Path, default=None, help="summary .json of another run to compare transforms against")
    p.add_argument("--subpixel", type=int, default=0, help="snap results to 1/N px (0 = continuous)")
    p.add_argument("--no-html", action="store_true", help="skip writing the viewer (benchmarks)")
    p.add_argument("--out", type=Path, default=Path("chromatic_lab_out"))
    p.add_argument("--name", default=None, help="html file name (default from options)")
    args = p.parse_args()

    t0 = time.perf_counter()
    frames = load_frames(args.images, args.frame)
    wls = list(frames)
    if args.ref_wl not in frames:
        raise SystemExit(f"Reference {args.ref_wl} nm not in {wls}")
    ref_index = wls.index(args.ref_wl)
    images = [frames[w] for w in wls]
    rng = np.random.default_rng(0)
    for n, img in enumerate(images):
        if args.contrast != 1.0:
            level = float(np.percentile(img, 95))
            img = level - args.contrast * (level - img)
        if args.downscale != 1.0:
            img = cv2.resize(img, None, fx=1 / args.downscale, fy=1 / args.downscale, interpolation=cv2.INTER_AREA)
        if args.noise > 0:
            img = img + rng.normal(0.0, args.noise * float(np.median(img)), img.shape).astype(np.float32)
        images[n] = img.astype(np.float32)
    h, w = images[0].shape
    nx, ny = (int(v) for v in args.grid.lower().split("x"))
    if args.bounds:
        bounds = tuple(float(v) for v in args.bounds.split(","))
    else:
        bounds = (w * args.border, h * args.border, w * (1 - args.border), h * (1 - args.border))
    load_s = time.perf_counter() - t0
    print(f"{len(wls)} wavelengths {wls[0]:.0f}-{wls[-1]:.0f} nm, image {w}x{h}, ref {args.ref_wl:.0f} nm, stride {args.stride}")

    # console stand-in for a GUI progress bar: prints every ~10 %
    last = [-1]

    def show(fraction: float, message: str) -> None:
        step = int(fraction * 10)
        if step != last[0]:
            last[0] = step
            print(f"  [{fraction * 100:3.0f}%] {message}")

    result = run_pipeline(images, wls, ref_index, (nx, ny), bounds, args, progress=show if args.stride >= 0 and not args.no_html else None)
    radius, models, pos, status = result["radius"], result["models"], result["pos"], result["status"]
    if args.subpixel > 0:
        for i in pos:
            pos[i] = np.round(pos[i] * args.subpixel) / args.subpixel
    timings = result["timings"]
    total = sum(timings.values())
    print(f"feature diameter ~{2 * radius:.0f} px, {result['n_candidates']} candidates, {len(result['tracked'])}/{len(wls)} wavelengths tracked")
    print("timing: load " + f"{load_s:.2f}s, " + ", ".join(f"{k} {v:.2f}s ({100 * v / total:.0f}%)" for k, v in timings.items()) + f", pipeline total {total:.2f}s")

    print(f"{'wl':>5} {'ok':>5} {'scale ppm':>10} {'centre dx':>10} {'centre dy':>10} {'RMSE':>7} {'LOO':>7}")
    for i, wl in enumerate(wls):
        m = models[i]
        c = m["a"] * (w / 2 + 1j * h / 2) + m["b"] - (w / 2 + 1j * h / 2)
        tag = "" if i in result["tracked"] else "  (interpolated)"
        print(f"{wl:5.0f} {m['n']:2d}/{len(result['ref_positions']):<2d} {(abs(m['a']) - 1) * 1e6:10.0f} {c.real:10.2f} {c.imag:10.2f} {m['rmse']:7.3f} {m['loo']:7.3f}{tag}")
    nflag = sum(1 for i in status for x in status[i] if x not in ("ok", "interp"))
    loo_all = [models[i]["loo"] for i in result["tracked"] if i != ref_index]
    print(f"flagged landmark-steps: {nflag}; mean LOO {np.mean(loo_all):.3f} px, max LOO {np.max(loo_all):.3f} px")

    name = args.name or f"lab_{args.grid}_s{args.stride}_{args.interp}_d{args.downscale:g}_n{args.noise:g}_c{args.contrast:g}.html"
    summary = dict(
        wls=wls, width=w, tracked=result["tracked"], timings=dict(timings),
        a_re=[models[i]["a"].real for i in range(len(wls))], a_im=[models[i]["a"].imag for i in range(len(wls))],
        b_re=[models[i]["b"].real for i in range(len(wls))], b_im=[models[i]["b"].imag for i in range(len(wls))],
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / (Path(name).stem + ".json")).write_text(json.dumps(summary), encoding="utf-8")
    if args.baseline:
        base = json.loads(args.baseline.read_text(encoding="utf-8"))
        rms, worst = grid_error_vs_baseline(wls, models, base, (h, w))
        skipped = [i for i in range(len(wls)) if i not in result["tracked"]]
        print(f"vs baseline over the whole image: all wavelengths rms {np.sqrt(np.mean(rms**2)):.3f} px, worst point {worst.max():.3f} px", end="")
        if skipped:
            print(f"; interpolated wavelengths only: rms {np.sqrt(np.mean(rms[skipped]**2)):.3f} px, worst point {worst[skipped].max():.3f} px", end="")
        print()
    if not args.no_html:
        write_frames(args.out / Path(name).stem, wls, images)
        payload = build_payload(wls, ref_index, result["pos"], result["score"], result["status"], models, (h, w), list(bounds), args, radius)
        payload["frames_dir"] = Path(name).stem + "/frames"
        (args.out / name).write_text(HTML.replace("__DATA__", json.dumps(payload)), encoding="utf-8")
        print(f"viewer: {args.out / name}")


if __name__ == "__main__":
    main()
