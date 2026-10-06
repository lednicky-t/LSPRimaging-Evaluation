import numpy as np
import pickle

out, CUBES, WLS = pickle.load(open("temporal.pkl", "rb"))
names = sorted(
    {k[0] for k in out},
    key=lambda n: [
        "none",
        "app s12 b2",
        "app s24 b2",
        "app s32 b2",
        "app s48 b2 (current)",
        "app s48 b8",
        "cv s48 f8",
        "block32 s1.5",
        "poly4",
    ].index(n),
)


def tstd(n, mode, wl, which):
    arr = np.array(
        [
            out[(n, mode, wl, c)][0] / out[(n, mode, wl, c)][1]
            if which == "T"
            else out[(n, mode, wl, c)][1]
            for c in CUBES
        ]
    )  # (t, roi)
    rel = arr / np.nanmedian(
        arr, axis=1, keepdims=True
    )  # remove the common (real) drift at each time
    return np.nanmedian(np.nanstd(rel, axis=0)) * 100


print(
    "temporal scatter of each ROI relative to the across-ROI median (%), median over ROIs, mean over 500/600/700 nm"
)
print(
    "%-22s %10s %10s %10s %10s" % ("method", "T sub", "T div", "ring sub", "ring div")
)
for n in names:
    print(
        "%-22s" % n
        + "".join(
            " %10.3f" % np.mean([tstd(n, m, wl, w) for wl in WLS])
            for w in ("T",)
            for m in ("sub", "div")
        )
        + "".join(
            " %10.3f" % np.mean([tstd(n, m, wl, "R") for wl in WLS])
            for m in ("sub", "div")
        )
    )
