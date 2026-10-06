import numpy as np
from scipy import ndimage as ndi
from common import load


def detect(wl=600, cube=0):
    a = load(wl, cube)
    bg = ndi.median_filter(a[::4, ::4], size=25)
    bg = ndi.zoom(bg, 4, order=1)[: a.shape[0], : a.shape[1]]
    dark = a < 0.6 * bg
    dark = ndi.binary_opening(dark, iterations=2)
    lab, n = ndi.label(dark)
    out = []
    for i in range(1, n + 1):
        m = lab == i
        area = m.sum()
        if area < 300:
            continue
        ys, xs = np.nonzero(m)
        edge = (
            ys.min() == 0
            or xs.min() == 0
            or ys.max() == a.shape[0] - 1
            or xs.max() == a.shape[1] - 1
        )
        out.append((xs.mean(), ys.mean(), np.sqrt(area / np.pi), edge, i))
    return a, lab, out


if __name__ == "__main__":
    a, lab, out = detect()
    r = np.array([o[2] for o in out])
    e = np.array([o[3] for o in out])
    print(
        len(out),
        "edge",
        e.sum(),
        "radius full: med %.1f min %.1f max %.1f"
        % (np.median(r[~e]), r[~e].min(), r[~e].max()),
    )
    print("dark fraction %.3f" % ((lab > 0).mean()))
    for wl in (470, 720):
        _, _, o2 = detect(wl, 0)
        print(wl, len(o2))
