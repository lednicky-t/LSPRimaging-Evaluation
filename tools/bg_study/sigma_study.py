import numpy as np
import pickle
from scipy.spatial import cKDTree
import scipy.ndimage as ndi
from common import H, W, load
from lspr_imaging_app.image_tools.background import estimate as E
from lspr_imaging_app.roi.model import AreaRoiDetectionSettings

st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
S = AreaRoiDetectionSettings(ignore_marked_pixels=True)
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
keep = [k for k, p in enumerate(patches) if len(p) >= 250]
mids = mids[keep]
patches = [patches[k] for k in keep]
rng = np.random.default_rng(2)
fold = rng.integers(0, 2, len(mids))


def disk(m, r):
    return np.hypot(xx - m[0], yy - m[1]) <= r


HOLE = {}
for rad in (24.5, 40.0):
    for f in (0, 1):
        h = np.zeros((H, W), bool)
        for k in np.flatnonzero(fold == f):
            h |= disk(mids[k], rad)
        HOLE[(rad, f)] = EXCL | h
allp = np.zeros((H, W), bool)
for p in patches:
    allp.ravel()[p] = True
EXALL = EXCL | ndi.binary_dilation(allp, iterations=3)
targets = [40, 120, 240]
assign = {
    t: [int(np.argmin(np.abs(np.hypot(*(mids - P[i]).T) - t))) for i in range(len(P))]
    for t in targets
}
SIG = [6, 12, 24, 36, 48, 72, 96, 150, 250]


def autobin(s):
    return max(b for b in (1, 2, 4, 8) if b <= max(s / 6, 1))


res = {}
for wl in (470, 550, 600, 700):
    for cu in (0, 313):
        img = load(wl, cu)
        fl = img.ravel()
        truth = np.array([fl[p].mean() for p in patches])
        Ta = np.array([fl[r["samp"]].mean() / fl[r["ring"]].mean() for r in rois])
        for s in SIG:
            b = autobin(s)
            for rad in (24.5, 40.0):
                err = np.zeros(len(mids))
                for f in (0, 1):
                    B = E.estimate_background_profile(
                        img,
                        sigma_px=s,
                        binning=b,
                        mask_settings=S,
                        external_mask=HOLE[(rad, f)],
                    ).ravel()
                    for k in np.flatnonzero(fold == f):
                        err[k] = B[patches[k]].mean() / truth[k] - 1
                res.setdefault(("hole", rad, s), []).append(err)
            B = E.estimate_background_profile(
                img, sigma_px=s, binning=b, mask_settings=S, external_mask=EXALL
            )
            F = (img / B * np.median(B[~EXALL])).ravel()
            Fs = np.array([F[r["samp"]].mean() for r in rois])
            for t in targets:
                R = np.array([F[patches[a]].mean() for a in assign[t]])
                res.setdefault(("disp", t, s), []).append((Fs / R) / Ta - 1)
        print(wl, cu, flush=True)
pickle.dump(res, open("sigma.pkl", "wb"))


def rms(L):
    return np.sqrt(np.mean(np.concatenate(L) ** 2)) * 100


print(
    "sigma bin | white-level error at a hole centre, RMS % (hole r=24.5 | r=40) | displaced-ref S/R error RMS % (40|120|240 px)"
)
for s in SIG:
    print(
        "%4d %3d |  %.3f  %.3f  |  %.3f %.3f %.3f"
        % (
            s,
            autobin(s),
            rms(res[("hole", 24.5, s)]),
            rms(res[("hole", 40.0, s)]),
            *[rms(res[("disp", t, s)]) for t in targets],
        )
    )
