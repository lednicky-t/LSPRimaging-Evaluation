import numpy as np
import pickle

res, times = pickle.load(open("holdout.pkl", "rb"))


def rcv(x):
    return 1.4826 * np.median(np.abs(x - np.median(x))) / np.median(x) * 100


ref = "app s48 b2 (current)"
print(
    "%-22s %7s | %-24s | %-24s"
    % ("method", "ms", "hold-out ring CV% sub / div", "S/R CV% sub / div")
)
for n in times:
    a = []
    b = []
    c = []
    d = []
    for (m, wl, cu), (S, R, S2, R2) in res.items():
        if m != n:
            continue
        a.append(rcv(R))
        b.append(rcv(R2))
        c.append(rcv(S / R))
        d.append(rcv(S2 / R2))
    print(
        "%-22s %7.0f | %10.3f %10.3f     | %10.3f %10.3f"
        % (
            n,
            np.median(times[n]) * 1000 / 2,
            np.mean(a),
            np.mean(b),
            np.mean(c),
            np.mean(d),
        )
    )
