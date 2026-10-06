import numpy as np
import pickle
import time
from common import load
from methods import METHODS

st = pickle.load(open("setup.pkl", "rb"))
EXCL = st["EXCL"]
rois = st["rois"]
WLS = [470, 500, 550, 600, 650, 700, 720]
CUBES = [0, 50, 100, 200, 313]
res = {}
times = {k: [] for k in METHODS}
for wl in WLS:
    for cu in CUBES:
        img = load(wl, cu)
        for name, fn in METHODS.items():
            t = time.perf_counter()
            B = fn(img, EXCL)
            dt = time.perf_counter() - t
            times[name].append(dt)
            base = np.median(B[~EXCL])
            F = img - B + base
            Fr = F.ravel()
            S = np.array([Fr[r["samp"]].mean() for r in rois])
            R = np.array([Fr[r["ring"]].mean() for r in rois])
            res[(name, wl, cu)] = (S, R)
            if name == "app s48 b2 (current)" and wl == 600 and cu == 0:
                np.save("B_ref_600_0.npy", B)
            if name == "poly4" and wl == 600 and cu == 0:
                np.save("B_poly4_600_0.npy", B)
        print(wl, cu, flush=True)
pickle.dump((res, times), open("spatial.pkl", "wb"))
