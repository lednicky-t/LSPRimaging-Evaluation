import numpy as np
import pickle
from scipy import ndimage as ndi
from rois import detect
from common import H, W

a, lab, out = detect(600, 0)
# all dark components incl. partial edge disks
lab2, n = ndi.label(
    ndi.binary_opening(
        a < 0.6 * ndi.zoom(ndi.median_filter(a[::4, ::4], size=25), 4, order=1)[:H, :W],
        iterations=2,
    )
)
sizes = ndi.sum(lab2 > 0, lab2, range(1, n + 1))
keep = np.isin(lab2, 1 + np.nonzero(sizes >= 40)[0])
foot = keep  # disk footprint
EXCL = ndi.binary_dilation(
    foot, iterations=6
)  # same ~1.35 r margin as the app's ROI exclusion
near_disk = ndi.binary_dilation(foot, iterations=2)
full = [o for o in out if not o[3]]
yy, xx = np.mgrid[:H, :W]
rois = []
for cx, cy, r, edge, i in full:
    d = np.hypot(xx - cx, yy - cy)
    samp = d < 0.6 * r
    ring = (d >= 1.25 * r) & (d <= 1.6 * r) & ~near_disk
    full_ring = ((d >= 1.25 * r) & (d <= 1.6 * r)).sum()
    if ring.sum() < 0.6 * full_ring:
        continue
    rois.append(
        dict(x=cx, y=cy, r=r, samp=np.flatnonzero(samp), ring=np.flatnonzero(ring))
    )
print("rois used", len(rois), "of", len(full), "excl fraction %.3f" % EXCL.mean())
pickle.dump(dict(EXCL=EXCL, rois=rois), open("setup.pkl", "wb"))
