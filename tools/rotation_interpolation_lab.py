"""Rotation interpolation lab (2026-10-20): how much rotating raw frames changes pixel values and ROI results, per method.
Record: docs/rotated_zarr_export_interpolation_2026-10-20.md. Paths below point at the maintainer's local dataset.

Fixes: fractional (area-weighted) ROI masks; spline round trips with replicate border; pixel noise from a
gain-normalised, high-passed two-frame difference; an FFT (sinc, three-shear) rotation at the app's exact geometry
as the band-limited reference for single-pass pixel errors.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import scipy.fft as sfft
import tifffile
from scipy import ndimage

sys.path.insert(0, r"C:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva\src")
from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings  # noqa: E402
from lspr_imaging_app.image_tools.geometry.transform import _combined_export_transform  # noqa: E402

ROOT = Path(r"C:\Users\Admin\Desktop\Data_PyTest\04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2\images")
pre = json.loads((ROOT / "analysis" / "preprocessing.json").read_text())
c = pre["crop"]
ANGLE = float(pre["rotation_angle_deg"])
SETTINGS = GeometrySettings(True, ANGLE, crop=CropDefinition(c["x"], c["y"], c["width"], c["height"], c["enabled"]))
ROIS = json.loads((ROOT / "analysis" / "roi_table.json").read_text())["area_rois"]
B = 40  # interior border
SS = 8  # supersampling for fractional masks


def load(wl, frame):
    return tifffile.imread(str(ROOT / f"imLCTFatWL{wl}Frame{frame}.tiff")).astype(np.float64)


def cv2_warp(img, M, off, shape, flag, border=cv2.BORDER_CONSTANT):
    m = np.array([[M[1, 1], M[1, 0], off[1]], [M[0, 1], M[0, 0], off[0]]])
    return cv2.warpAffine(img.astype(np.float32), m, (shape[1], shape[0]), flags=flag | cv2.WARP_INVERSE_MAP,
                          borderMode=border, borderValue=float("nan")).astype(np.float64)


def scipy_warp(img, M, off, shape, order, mode="constant"):
    return ndimage.affine_transform(img, M, off, output_shape=shape, order=order, mode=mode, cval=np.nan,
                                    prefilter=order > 1)


def _shift_rows(arr, shifts):
    """Shift each row i by shifts[i] px along the columns (positive = content moves right), FFT phase ramp."""
    n = arr.shape[1]
    f = sfft.rfft(arr, axis=1, workers=-1)
    freqs = sfft.rfftfreq(n)
    return sfft.irfft(f * np.exp(-2j * np.pi * freqs[None, :] * shifts[:, None]), n=n, axis=1, workers=-1)


def fft_affine(img, M, off, shape, pad=96, nan_outside=True):
    """output[q] = img[M q + off] for a pure rotation M, by three FFT shears about the padded centre plus a
    sub-pixel FFT translation, then the integer crop. Band-limited (sinc) interpolation."""
    p = np.pad(img, pad, mode="reflect")
    cen = (np.array(p.shape) - 1) / 2
    # rotation of the padded canvas about its centre: R[p] = P[M (p - cen) + cen]
    s, co = M[0, 1], M[0, 0]  # M = [[cos, sin], [-sin, cos]]
    t = np.arctan2(s, co)
    a, b = np.tan(t / 2), np.sin(t)
    rows = np.arange(p.shape[0]) - cen[0]
    cols = np.arange(p.shape[1]) - cen[1]
    # Paeth: [[1, 0], [a?]] ... use row/col shears so that src = M @ dst. Each shear: dst value at (r, c) = src at
    # (r, c + k r) (x-shear) or (r + k c, c) (y-shear). Composition x(a) y(b) x(a) with a = tan(t/2), b = -sin(t)
    # gives [[cos, sin],[-sin, cos]] in (row, col) when applied in the right order; checked numerically below.
    # M = Sx(al) Sy(s) Sx(al), al = -tan(t/2); W_A[u] = I[A u] = shift of the content by -(sampling offset).
    q = _shift_rows(p, a * rows)             # samples x - a y
    q = _shift_rows(q.T, -b * cols).T        # samples y + s x
    q = _shift_rows(q, a * rows)
    # R[p] = P[M (p - cen) + cen]; we need out[q] = img[M q + off] = P[M q + off + pad]
    # => p = q + M^T (off + pad - cen) + cen
    tvec = M.T @ (off + pad - cen) + cen
    ti = np.floor(tvec).astype(int)
    tf = tvec - ti
    q = _shift_rows(q, -np.full(q.shape[0], tf[1]))
    q = _shift_rows(q.T, -np.full(q.shape[1], tf[0])).T
    out = np.full(shape, np.nan)
    r0, c0 = ti
    sub = q[r0:r0 + shape[0], c0:c0 + shape[1]]
    out[:sub.shape[0], :sub.shape[1]] = sub
    # NaN where the source position is outside the raw image (as the app does)
    rr = np.arange(shape[0])[:, None]
    cc = np.arange(shape[1])[None, :]
    sy = M[0, 0] * rr + M[0, 1] * cc + off[0]
    sx = M[1, 0] * rr + M[1, 1] * cc + off[1]
    if nan_outside:
        out[(sy < 0) | (sy > img.shape[0] - 1) | (sx < 0) | (sx > img.shape[1] - 1)] = np.nan
    return out


METHODS = {
    "cv2 nearest": lambda i, M, o, s, **k: cv2_warp(i, M, o, s, cv2.INTER_NEAREST, **k),
    "cv2 linear (app now)": lambda i, M, o, s, **k: cv2_warp(i, M, o, s, cv2.INTER_LINEAR, **k),
    "cv2 cubic": lambda i, M, o, s, **k: cv2_warp(i, M, o, s, cv2.INTER_CUBIC, **k),
    "cv2 lanczos4": lambda i, M, o, s, **k: cv2_warp(i, M, o, s, cv2.INTER_LANCZOS4, **k),
    "scipy linear": lambda i, M, o, s, **k: scipy_warp(i, M, o, s, 1, **k),
    "scipy cubic spline": lambda i, M, o, s, **k: scipy_warp(i, M, o, s, 3, **k),
    "scipy quintic spline": lambda i, M, o, s, **k: scipy_warp(i, M, o, s, 5, **k),
    "fft sinc (3-shear)": lambda i, M, o, s, **k: fft_affine(i, M, o, s, **k),
}
RT_KW = {  # round trip: border that does not spread NaN into the interior
    "cv2": {"border": cv2.BORDER_REPLICATE}, "scipy": {"mode": "nearest"}, "fft": {"nan_outside": False},
}


def disc_weights(shape_hw, cx, cy, r):
    """Fraction of each pixel inside the circle (SSxSS supersampling)."""
    h, w = shape_hw
    o = (np.arange(SS) + 0.5) / SS - 0.5
    yy = np.arange(h)[:, None, None, None] + o[None, None, :, None]
    xx = np.arange(w)[None, :, None, None] + o[None, None, None, :]
    return (((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r).mean(axis=(2, 3))


def roi_values(img, centres, radii):
    out = []
    for (cx, cy), r in zip(centres, radii):
        R = 2.0 * r + 2
        x0, y0 = int(np.floor(cx - R)), int(np.floor(cy - R))
        x1, y1 = int(np.ceil(cx + R)) + 1, int(np.ceil(cy + R)) + 1
        sub = img[y0:y1, x0:x1]
        ws = disc_weights(sub.shape, cx - x0, cy - y0, r)
        wa = disc_weights(sub.shape, cx - x0, cy - y0, 2.0 * r) - disc_weights(sub.shape, cx - x0, cy - y0, 1.4 * r)
        ok = np.isfinite(sub)
        s = (sub[ok] * ws[ok]).sum() / ws[ok].sum()
        a = (sub[ok] * wa[ok]).sum() / wa[ok].sum()
        out.append((s, s / a))
    return np.array(out)


def full_canvas(shape, angle):
    t = np.deg2rad(angle)
    M = np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]])
    cen = (np.array(shape) - 1) / 2
    return M, cen - M @ cen


def main():
    a0 = load(500, 0)
    M, off, out_shape = _combined_export_transform(a0.shape, SETTINGS)
    # sanity: fft reference vs scipy quintic at the app geometry
    ref = fft_affine(a0, M, off, out_shape)
    q5 = scipy_warp(a0, M, off, out_shape, 5)
    inner = (slice(B, -B), slice(B, -B))
    d = (ref - q5)[inner]
    print(f"check fft vs quintic: RMS {np.sqrt(np.nanmean(d**2)):.2f} counts (should be small vs the image level {np.nanmedian(a0):.0f})")

    proc_c = np.array([[r["center_x"], r["center_y"]] for r in ROIS])
    radii = np.array([r["sample_radius_px"] for r in ROIS])
    rc = M @ np.stack([proc_c[:, 1], proc_c[:, 0]]) + off[:, None]
    raw_c = np.stack([rc[1], rc[0]], axis=1)
    valid = ((raw_c[:, 0] - 2.2 * radii > 0) & (raw_c[:, 1] - 2.2 * radii > 0) & (raw_c[:, 0] + 2.2 * radii < a0.shape[1] - 1)
             & (raw_c[:, 1] + 2.2 * radii < a0.shape[0] - 1) & (proc_c[:, 0] - 2.2 * radii > 0) & (proc_c[:, 1] - 2.2 * radii > 0)
             & (proc_c[:, 0] + 2.2 * radii < out_shape[1] - 1) & (proc_c[:, 1] + 2.2 * radii < out_shape[0] - 1))
    proc_c, raw_c, radii = proc_c[valid], raw_c[valid], radii[valid]
    print(f"ROIs fully inside both grids: {valid.sum()} of {len(valid)}; NaN px per frame with this crop: "
          f"{int(np.isnan(q5).sum())} ({100*np.isnan(q5).mean():.2f}%)")

    names = list(METHODS)
    acc = {n: {k: [] for k in ("t", "rt", "single", "keep", "kmin", "kmax", "mean", "ratio", "perroi")} for n in names}
    sig, nmean, nratio, floor = [], [], [], []
    for wl in (500, 600, 700):
        for fr in (0, 156, 312):
            a, b = load(wl, fr), load(wl, fr + 1)
            g = a[inner].mean() / b[inner].mean()
            dd = a - g * b
            hp = dd - ndimage.gaussian_filter(dd, 4)
            sigma = hp[inner].std() / np.sqrt(2) / np.sqrt(1 - 1 / (4 * np.pi * 16))  # high-pass removes a sliver of white noise
            sig.append(sigma)
            truth = roi_values(a, raw_c, radii)
            nxt = roi_values(g * b, raw_c, radii)
            nmean.append(np.std(nxt[:, 0] / truth[:, 0] - 1) / np.sqrt(2) * 1e6)
            nratio.append(np.std(nxt[:, 1] / truth[:, 1] - 1) / np.sqrt(2) * 1e6)
            ref = fft_affine(a, M, off, out_shape)
            Mp, op = full_canvas(a.shape, ANGLE)
            Mm, om = full_canvas(a.shape, -ANGLE)
            base_hp = hp[inner].std()
            for n in names:
                fn = METHODS[n]
                t0 = time.perf_counter()
                rot = fn(a, M, off, out_shape)
                acc[n]["t"].append((time.perf_counter() - t0) * 1e3)
                e = (rot - ref)[inner]
                acc[n]["single"].append(np.sqrt(np.nanmean(e ** 2)) / sigma)
                v = roi_values(rot, proc_c, radii)
                acc[n]["mean"].append(np.sqrt(np.mean((v[:, 0] / truth[:, 0] - 1) ** 2)) * 1e6)
                acc[n]["ratio"].append(np.sqrt(np.mean((v[:, 1] / truth[:, 1] - 1) ** 2)) * 1e6)
                acc[n]["perroi"].append(v[:, 1] / truth[:, 1] - 1)
                hr = fn(hp, M, off, out_shape)[inner]
                acc[n]["keep"].append(np.nanstd(hr) / base_hp)
                blocks = np.array([np.nanstd(hr[i:i + 32, j:j + 32]) for i in range(0, hr.shape[0] - 32, 32)
                                   for j in range(0, hr.shape[1] - 32, 32)]) / base_hp
                acc[n]["kmin"].append(np.nanpercentile(blocks, 2))
                acc[n]["kmax"].append(np.nanpercentile(blocks, 98))
                kw = RT_KW[n.split()[0]]
                back = fn(fn(a, Mp, op, a.shape, **kw), Mm, om, a.shape, **kw)
                acc[n]["rt"].append(np.sqrt(np.nanmean(((back - a)[inner]) ** 2)) / sigma)
            floor.append(np.sqrt(np.mean((roi_values(ref, proc_c, radii)[:, 1] / truth[:, 1] - 1) ** 2)) * 1e6)
        print(f"  wl {wl}: sigma {np.mean(sig[-3:]):.0f} counts")

    print(f"\npixel noise sigma {np.mean(sig):.0f} counts (level ~5e4); ROI frame-to-frame noise: sample mean "
          f"{np.mean(nmean):.0f} ppm, sample/ref ratio {np.mean(nratio):.0f} ppm")
    print(f"{'method':22s} {'ms':>6s} {'1-pass err/sigma':>16s} {'roundtrip/sigma':>15s} {'noise kept':>10s} {'blocks 2-98%':>13s} {'ROI mean':>9s} {'ROI ratio':>9s} {'varying':>8s}")
    for n in names:
        v = acc[n]
        print(f"{n:22s} {np.mean(v['t']):6.1f} {np.mean(v['single']):16.3f} {np.mean(v['rt']):15.3f} {np.mean(v['keep']):10.3f} "
              f"{np.mean(v['kmin']):6.3f}-{np.mean(v['kmax']):.3f} {np.mean(v['mean']):6.0f}ppm {np.mean(v['ratio']):6.0f}ppm {np.mean(np.std(np.array(v['perroi']), axis=0))*1e6:5.0f}ppm")


if __name__ == "__main__":
    main()
