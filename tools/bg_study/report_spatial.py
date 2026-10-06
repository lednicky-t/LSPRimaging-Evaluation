import numpy as np
import pickle

res, times = pickle.load(open("spatial.pkl", "rb"))


def rcv(x):
    x = x[np.isfinite(x)]
    return 1.4826 * np.median(np.abs(x - np.median(x))) / np.median(x) * 100


names = list(times)
ref = "app s48 b2 (current)"
print(
    "%-24s %8s %9s %9s %9s %9s"
    % ("method", "ms/frame", "refCV%", "T_CV%", "refCV@600", "dRef vs cur %")
)
for n in names:
    cvR = []
    cvT = []
    c600 = []
    dd = []
    for (m, wl, cu), (S, R) in res.items():
        if m != n:
            continue
        cvR.append(rcv(R))
        cvT.append(rcv(S / R))
        if wl == 600:
            c600.append(rcv(R))
        Rr = res[(ref, wl, cu)][1]
        dd.append(np.sqrt(np.mean((R / np.median(R) - Rr / np.median(Rr)) ** 2)) * 100)
    print(
        "%-24s %8.0f %9.3f %9.3f %9.3f %9.3f"
        % (
            n,
            np.median(times[n]) * 1000,
            np.mean(cvR),
            np.mean(cvT),
            np.mean(c600),
            np.mean(dd),
        )
    )
