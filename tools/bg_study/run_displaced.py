import numpy as np
import scipy.ndimage as ndi
import pickle
from scipy.spatial import cKDTree
from common import H, W, load
from methods import make_nconv

st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
P = np.array([[r["x"], r["y"]] for r in rois])
tree = cKDTree(P)
d, _ = tree.query(P, k=2)
sp = np.median(d[:, 1])
pairs = set()
for i in range(len(P)):
    for j in tree.query_ball_point(P[i], 1.25 * sp):
        if j > i:
            pairs.add((i, j))
mids = np.array([(P[i] + P[j]) / 2 for i, j in pairs])
print("spacing %.1f px, gap midpoints %d" % (sp, len(mids)))
yy, xx = np.mgrid[:H, :W]
patches = []
for m in mids:
    p = np.flatnonzero((np.hypot(xx - m[0], yy - m[1]) <= 10) & ~EXCL)
    patches.append(p if len(p) >= 250 else None)
ok = [k for k, p in enumerate(patches) if p is not None]
mids = mids[ok]
patches = [patches[k] for k in ok]
print("clean patches", len(patches))
HOLE = np.zeros((H, W), bool)
for p in patches:
    HOLE.ravel()[p] = True
EX = EXCL | ndi.binary_dilation(HOLE, iterations=3)
# assign per ROI and per target distance the patch whose centre distance is closest to target
targets = [40, 120, 240]
assign = {}
for t in targets:
    assign[t] = [
        int(np.argmin(np.abs(np.hypot(*(mids - P[i]).T) - t))) for i in range(len(P))
    ]
    dd = [np.hypot(*(mids[a] - P[i])) for i, a in enumerate(assign[t])]
    print("target", t, "actual median %.0f" % np.median(dd))
M = {"cv48": make_nconv(8, 48), "cv24": make_nconv(8, 24), "cv12": make_nconv(8, 12)}
res = {}
for wl in (470, 500, 550, 600, 650, 700, 720):
    for cu in (0, 100, 313):
        img = load(wl, cu)
        fl = img.ravel()
        Sraw = np.array([fl[r["samp"]].mean() for r in rois])
        Rring = np.array([fl[r["ring"]].mean() for r in rois])
        Ta = Sraw / Rring
        for mn, fn in M.items():
            B = fn(img, EX)
            base = np.median(B[~EX])
            Bf = B.ravel()
            for mode in ("none", "sub", "div"):
                F = (
                    img
                    if mode == "none"
                    else (img - B + base if mode == "sub" else img / B * base)
                )
                Ff = F.ravel()
                S = np.array([Ff[r["samp"]].mean() for r in rois])
                for t in targets:
                    R = np.array([Ff[patches[a]].mean() for a in assign[t]])
                    res[(mn, mode, t, wl, cu)] = (S / R) / Ta - 1
pickle.dump(res, open("displaced.pkl", "wb"))
print(
    "%-6s %-5s" % ("est", "mode")
    + "".join("   d=%-3d rms%%  bias%%" % t for t in targets)
)
for mn in M:
    for mode in ("none", "sub", "div"):
        row = ""
        for t in targets:
            e = (
                np.concatenate([v for k, v in res.items() if k[:3] == (mn, mode, t)])
                * 100
            )
            row += "   %8.3f %7.3f" % (np.sqrt(np.mean(e**2)), np.mean(e))
        print("%-6s %-5s" % (mn, mode) + row)
