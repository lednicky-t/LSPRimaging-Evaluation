import numpy as np
import pickle
import time
from common import H, W, load
from methods import METHODS

for k in ("poly2", "app s150 b4", "cv s96 f16", "block32 s3"):
    METHODS.pop(k, None)
st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
yy, xx = np.mgrid[:H, :W]
rng = np.random.default_rng(1)
fold = rng.integers(0, 2, len(rois))
HOLE = [np.zeros((H, W), bool), np.zeros((H, W), bool)]
for r, f in zip(rois, fold):
    HOLE[f] |= np.hypot(xx - r["x"], yy - r["y"]) <= 1.75 * r["r"]
EX = [EXCL | HOLE[0], EXCL | HOLE[1]]
WLS = [470, 500, 550, 600, 650, 700, 720]
CUBES = [0, 50, 100, 200, 313]
res = {}
times = {k: [] for k in METHODS}
for wl in WLS:
    for cu in CUBES:
        img = load(wl, cu)
        for name, fn in METHODS.items():
            S = np.full((2, len(rois)), np.nan)
            R = S.copy()
            S2 = S.copy()
            R2 = S.copy()
            for f in (0, 1):
                t = time.perf_counter()
                B = fn(img, EX[f])
                times[name].append(time.perf_counter() - t)
                base = np.median(B[~EX[f]])
                Fs = (img - B + base).ravel()
                Fd = (img / B * base).ravel()
                for i, r in enumerate(rois):
                    if fold[i] != f:
                        continue
                    S[f, i] = Fs[r["samp"]].mean()
                    R[f, i] = Fs[r["ring"]].mean()
                    S2[f, i] = Fd[r["samp"]].mean()
                    R2[f, i] = Fd[r["ring"]].mean()
            res[(name, wl, cu)] = tuple(np.nansum(a, 0) for a in (S, R, S2, R2))
        print(wl, cu, flush=True)
pickle.dump((res, times), open("holdout.pkl", "wb"))
