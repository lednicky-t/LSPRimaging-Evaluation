import numpy as np
import pickle
import time
from scipy.spatial import cKDTree
import scipy.ndimage as ndi
from common import H, W, load
from lspr_imaging_app.image_tools.background import estimate as E
from lspr_imaging_app.roi.model import AreaRoiDetectionSettings

st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
S = AreaRoiDetectionSettings(ignore_marked_pixels=True)
img = load(600, 0)


def T(f, n=7):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        f()
        ts.append(time.perf_counter() - t)
    return np.median(ts) * 1e3


valid = ~E._combined_exclusion_mask(img, mask_settings=S, external_mask=EXCL)
print(
    "fixed parts: mask %.1f ms, apply %.1f ms"
    % (
        T(lambda: E._combined_exclusion_mask(img, mask_settings=S, external_mask=EXCL)),
        T(lambda: E.apply_background(img, img, 50000.0)),
    )
)
print("bin | core est+baseline ms | flatten total ms")
for b in (1, 2, 4, 8, 16, 32):
    kw = dict(sigma_px=48.0, binning=b, mask_settings=S, external_mask=EXCL)
    print(
        "%3d | %6.1f | %6.1f"
        % (
            b,
            T(lambda: E._background_and_baseline(img, valid, 48.0, b)),
            T(lambda: E.flatten_background(img, **kw)),
        )
    )
# quality: displaced reference (as before) + A vs bin 2
P = np.array([[r["x"], r["y"]] for r in rois])
tree = cKDTree(P)
d, _ = tree.query(P, k=2)
sp = np.median(d[:, 1])
pairs = {
    (i, j)
    for i in range(len(P))
    for j in tree.query_ball_point(P[i], 1.25 * sp)
    if j > i
}
mids = np.array([(P[i] + P[j]) / 2 for i, j in pairs])
yy, xx = np.mgrid[:H, :W]
patches = [np.flatnonzero((np.hypot(xx - m[0], yy - m[1]) <= 10) & ~EXCL) for m in mids]
HOLE = np.zeros((H, W), bool)
for p in patches:
    HOLE.ravel()[p] = True
EX = EXCL | ndi.binary_dilation(HOLE, iterations=3)
targets = [40, 120, 240]
assign = {
    t: [int(np.argmin(np.abs(np.hypot(*(mids - P[i]).T) - t))) for i in range(len(P))]
    for t in targets
}
res = {}
for wl in (470, 550, 600, 700):
    for cu in (0, 313):
        im = load(wl, cu)
        fl = im.ravel()
        Ta = np.array([fl[r["samp"]].mean() / fl[r["ring"]].mean() for r in rois])
        for b in (1, 2, 4, 8, 16, 32):
            F = E.flatten_background(
                im, sigma_px=48.0, binning=b, mask_settings=S, external_mask=EX
            ).ravel()
            Fs = np.array([F[r["samp"]].mean() for r in rois])
            for t in targets:
                R = np.array([F[patches[a]].mean() for a in assign[t]])
                res.setdefault((b, t), []).append((Fs / R) / Ta - 1)
print("displaced-reference error RMS %  (sigma 48 px):  bin | 40 px | 120 px | 240 px")
for b in (1, 2, 4, 8, 16, 32):
    print(
        "%3d |" % b
        + " |".join(
            " %.3f" % (np.sqrt(np.mean(np.concatenate(res[(b, t)]) ** 2)) * 100)
            for t in targets
        )
    )
