import numpy as np
import pickle
from common import load
from methods import METHODS, make_app

M = {
    k: METHODS[k]
    for k in (
        "none",
        "app s48 b2 (current)",
        "app s24 b2",
        "app s48 b8",
        "cv s48 f8",
        "poly4",
        "block32 s1.5",
    )
}
M["app s12 b2"] = make_app(12, 2)
M["app s32 b2"] = make_app(32, 2)
st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
CUBES = list(range(0, 314, 6))
WLS = [500, 600, 700]
out = {}
for wl in WLS:
    for cu in CUBES:
        img = load(wl, cu)
        for n, fn in M.items():
            B = fn(img, EXCL)
            base = np.median(B[~EXCL])
            for mode, F in (("sub", img - B + base), ("div", img / B * base)):
                Fr = F.ravel()
                out[(n, mode, wl, cu)] = (
                    np.array([Fr[r["samp"]].mean() for r in rois]),
                    np.array([Fr[r["ring"]].mean() for r in rois]),
                )
    print(wl, flush=True)
pickle.dump((out, CUBES, WLS), open("temporal.pkl", "wb"))
