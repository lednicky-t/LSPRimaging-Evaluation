"""Spectral correction: what the LCTF passband of each image really looked like (2026-11-20). Pure numpy, no Qt, no files.

**The problem.** The camera takes the image of wavelength ``lambda_k`` through the tunable filter (LCTF). The filter does not pass one wavelength: it passes a
band (FWHM about 4 nm at 420 nm, 10 nm at 600 nm, 15 nm at 700 nm) whose centre is not exactly where it was told to be (measured: 0.3 nm too blue on
average, point-to-point scatter 0.5 nm, repeatable to 0.03 nm). The spectrum the app builds therefore is not ``A(lambda_k)`` but, for each point, the true
spectrum *averaged over that point's passband*. A spectrometer looking at the filter's output on the bench measured every passband
(``spikes/lspri_acq_phase0``, ``illumination_probe.py``, tab 4, "spectral sweep"); this module turns those spectra into numbers the fits can use.

**The model.** For setting ``k`` let ``w_k(lambda)`` be the weight of the light that reaches the camera (lamp x filter band x optics x camera sensitivity),
normalised to an area of 1. The camera signal of a ROI with transmittance ``tau(lambda)`` is ``I_k = integral w_k tau``. The spectrometer is taken as flat
(the maintainer's assumption: calibrated, response flat) and so is the camera: ``w_k`` is the measured profile itself. *This is an assumption, and it
matters at the red end*: a slope ``s`` of the lamp x camera response shifts the true centre of gravity by ``sigma_k^2 s`` (a camera sensitivity falling by
0.4 %/nm gives 0.17 nm at 700 nm, where ``sigma`` is 7 nm). The stored table has a reserved slot for a detector-response curve (not used yet).

The three ways to use it (``MODES``):

``none``      the nominal wavelength ``lambda_k`` as the x value (what the app did before).
``centroid``  the x value is the first moment of the band, ``lambda_bar_k = integral lambda w_k``. Expanding the spectrum around ``lambda_bar_k``::

                  integral w_k tau = tau(lambda_bar_k) + (sigma_k^2 / 2) tau''(lambda_bar_k) + ...

              the first-order term is zero *exactly there*, so this is the right x value to first order; what is left is a curvature term that depends on
              the peak, not on the filter.
``profile``   the fit models what each point sees. A polynomial ``P(t)`` of the scaled wavelength ``t = (lambda - middle) / half`` (the window's own scaling)
              gives the point ``y_k = integral w_k P(t(lambda)) dlambda = sum_m c_m B_km``, with ``B_km = integral w_k t^m``. That is the ordinary least-squares
              problem with the Vandermonde matrix ``t_k^m`` replaced by ``B``: same cost, same solver. ``B`` comes from the central moments
              ``mu_{k,i} = integral (lambda - lambda_bar_k)^i w_k`` (``mu_0 = 1``, ``mu_1 = 0``) by the binomial theorem, with ``t = t_k + (lambda - lambda_bar_k) / half``::

                  B_km = sum_{i=0..m} C(m, i) t_k^(m-i) (1 / half)^i mu_{k,i}

              The curve that is drawn and whose maximum / centroid is read is ``P`` itself: the *underlying* spectrum with the band's broadening taken out
              (an apparent width too large by 1-9 nm and a height too small by 1-10 % in the simulation, ``docs/spectral_correction_2026-11-20.md``).
              The averaging is linear in the formula value (absorbance): the exact average is of ``10^-A``, and the difference is the band-variance term
              ``(ln 10 / 2) Var_w(A)``. Measured on Lorentzian peaks through the real bands (``test_lspri_spectral_passband.py``), as a share of the peak height:
              at most 0.25 % for A = 0.1, 0.76 % for A = 0.3 and 2.8 % for A = 1, always for a 30 nm wide peak at 700 nm (band 15 nm wide); about a third of that for
              a 40-60 nm wide peak in the green. The term is even about the peak, so it does not move it; it is zero for the ratio formula, which is exactly linear.
              A second pass (add the term from the first fit's slope and fit again) would remove it; not built (it would double the cost of the Sensorgram's fits).

**Points with no band** (a scan wavelength that is not in the table, within ``MATCH_TOLERANCE_NM``, or whose row was too weak): the nominal wavelength with
a band of zero width (``mu = (1, 0, 0, ...)``). Never interpolated between set wavelengths: the point-to-point scatter of the centres is real.
``Passbands.coverage`` says how many scan wavelengths were matched; the panel says so.

**What it does not do.** It does not know the filter's temperature (the August data suggest about -0.1 nm / degree C, from two runs; a temperature series is
the next measurement, ``docs/spectral_correction_2026-11-20.md``), nor the lamp that was used in the dataset. The table is only right for the lamp,
filter and temperature it was measured with.

Raw rows are never changed. Everything here is derived from them on demand, so a fix to the derivation (``PASSBAND_ALGORITHM_VERSION``) can never leave
old numbers behind.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from math import comb

import numpy as np

PASSBAND_ALGORITHM_VERSION = 1
"""Bump when a change can alter a derived number (the profile of a row, its centroid, its moments). Part of every cache key."""

MODES: tuple[str, ...] = ("none", "centroid", "profile")
MODE_LABELS = {"none": "None", "centroid": "Centroid", "profile": "Full profile"}

WINDOW_NM = 60.0
"""A row's profile is read within +-this many nm of its set wavelength (the spectrometer sees only that one band: the rest of the row is noise)."""
EDGE_POINTS = 20
"""Pixels at each end of the window that give the baseline (their median) and the noise (their standard deviation)."""
NOISE_SIGMAS = 3.0
"""Profile samples below baseline + this many noise standard deviations are set to zero (they would add noise-weighted tails to the moments)."""
MIN_PEAK_TO_NOISE = 10.0
"""A row whose peak is below this many noise standard deviations has no usable profile."""
MAX_CENTRE_ERROR_NM = 15.0
"""A row whose centre of gravity is further than this from its set wavelength is not a band of that setting (the filter did not tune there): no profile."""
MATCH_TOLERANCE_NM = 0.25
"""A scan wavelength matches a table row when the set wavelengths differ by no more than this."""
MAX_ORDER = 13
"""The highest polynomial order the Spectra panel offers; the central moments are kept up to it."""


class PassbandError(ValueError):
    """A table that cannot be used; the message is a sentence for the user."""


@dataclass(frozen=True, eq=False)
class PassbandTable:
    """The spectrometer rows of one spectral sweep, exactly as imported (counts, not processed). ``eq=False``: compare ``sha256``."""

    pixel_nm: np.ndarray  # float64 [N], the spectrometer's wavelength axis
    set_nm: np.ndarray  # float64 [K], what the filter was told
    counts: np.ndarray  # float64 [K, N]
    confirmed_nm: np.ndarray = field(default_factory=lambda: np.zeros(0))  # float64 [K], the filter's own read-back (NaN: not given)
    settle_ms: np.ndarray = field(default_factory=lambda: np.zeros(0))  # float64 [K] (NaN: not given)
    temperature_c: np.ndarray = field(default_factory=lambda: np.zeros(0))  # float64 [K], the filter's temperature at each row (NaN: not given)
    dark: np.ndarray | None = None  # float64 [N], the lamp-off spectrum measured with the same settings, or None
    source_name: str = ""
    sha256: str = ""
    illumination: str = ""  # free text: which lamp the sweep was measured with
    imported_at: str = ""  # ISO time of the import

    @property
    def n_rows(self) -> int:
        return int(self.set_nm.size)

    @property
    def mean_temperature_c(self) -> float | None:
        """The mean filter temperature over the rows that report one; None when no row does."""
        t = np.asarray(self.temperature_c, dtype=np.float64)
        t = t[np.isfinite(t)]
        return float(t.mean()) if t.size else None

    def validate(self) -> None:
        """Raise ``PassbandError`` for a table the derivation could not use."""
        n_pixel, n_row = self.pixel_nm.size, self.set_nm.size
        if n_pixel < 50:
            raise PassbandError(f"The spectrometer axis has only {n_pixel} wavelengths.")
        if not np.all(np.isfinite(self.pixel_nm)) or not np.all(np.diff(self.pixel_nm) > 0):
            raise PassbandError("The spectrometer's wavelengths are not finite and increasing.")
        if n_row < 3:
            raise PassbandError(f"The sweep has {n_row} settings; at least 3 are needed.")
        if self.counts.shape != (n_row, n_pixel):
            raise PassbandError("The sweep's spectra do not match its wavelength axis.")
        if not np.all(np.isfinite(self.counts)):
            raise PassbandError("The sweep has a spectrum with a missing or non-numeric value.")
        if not np.all(np.isfinite(self.set_nm)):
            raise PassbandError("A set wavelength of the sweep is not a number.")
        if self.dark is not None and (self.dark.shape != (n_pixel,) or not np.all(np.isfinite(self.dark))):
            raise PassbandError("The dark spectrum does not match the sweep's wavelength axis.")
        ordered = np.sort(self.set_nm)
        if np.any(np.diff(ordered) < 2.0 * MATCH_TOLERANCE_NM):
            raise PassbandError("The sweep sets the same wavelength twice (a repeated setting); import one spectrum per setting.")


def file_digest(*parts: bytes) -> str:
    """SHA-256 of the imported file(s), as hex (what the session keeps to notice a changed file)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()


@dataclass(frozen=True, eq=False)
class RowProfile:
    """The derived band of one row: ``weights`` are over the whole pixel axis (zero outside the window), area-normalised in nm."""

    usable: bool
    why: str
    centroid_nm: float
    sigma_nm: float
    fwhm_nm: float
    peak_counts: float
    weights: np.ndarray | None
    moments: np.ndarray  # float64 [MAX_ORDER + 1]: central moments mu_0 .. mu_MAX_ORDER about the centroid


_POINT = np.zeros(MAX_ORDER + 1)
_POINT[0] = 1.0
POINT_MOMENTS = _POINT
"""The band of zero width: moments ``(1, 0, 0, ...)``. A read-only copy is returned by ``point_moments``."""


def point_moments() -> np.ndarray:
    return POINT_MOMENTS.copy()


def row_profile(pixel_nm: np.ndarray, counts: np.ndarray, dark: np.ndarray | None, set_nm: float) -> RowProfile:
    """The band of one row: dark removed, baseline from the window's two ends, samples under ``NOISE_SIGMAS`` noise set to zero, then the area-normalised weights,
    centroid, standard deviation, FWHM and central moments. A row that is too weak, or centred too far from its setting, is not usable (``why`` says why)."""
    pixel_nm = np.asarray(pixel_nm, dtype=np.float64)
    y = np.asarray(counts, dtype=np.float64)
    if dark is not None:
        y = y - np.asarray(dark, dtype=np.float64)
    inside = np.flatnonzero((pixel_nm > set_nm - WINDOW_NM) & (pixel_nm < set_nm + WINDOW_NM))
    if inside.size < 4 * EDGE_POINTS:
        return RowProfile(False, "the spectrometer range does not cover this setting", np.nan, np.nan, np.nan, np.nan, None, point_moments())
    s = y[inside]
    edge = np.concatenate([s[:EDGE_POINTS], s[-EDGE_POINTS:]])
    base, noise = float(np.median(edge)), float(np.std(edge))
    s = s - base
    peak = float(s.max())
    if not noise > 0.0:
        noise = np.finfo(np.float64).tiny
    if peak < MIN_PEAK_TO_NOISE * noise:
        return RowProfile(False, "no clear band (peak under 10 x noise)", np.nan, np.nan, np.nan, peak, None, point_moments())
    s = np.where(s >= NOISE_SIGMAS * noise, s, 0.0)
    lam = pixel_nm[inside]
    d_lam = np.gradient(lam)  # the pixel width in nm: pixels are not exactly evenly spaced
    w = s * d_lam
    total = float(w.sum())
    centroid = float(np.sum(lam * w) / total)
    if abs(centroid - set_nm) > MAX_CENTRE_ERROR_NM:
        return RowProfile(False, f"the band is centred {centroid - set_nm:+.1f} nm from its setting", np.nan, np.nan, np.nan, peak, None, point_moments())
    w = w / total
    dx = lam - centroid
    moments = np.array([float(np.sum(w * dx**i)) for i in range(MAX_ORDER + 1)])
    sigma = float(np.sqrt(max(moments[2], 0.0)))
    weights = np.zeros(pixel_nm.size)
    weights[inside] = w
    return RowProfile(True, "", centroid, sigma, _fwhm(lam, s), peak, weights, moments)


def _fwhm(lam: np.ndarray, s: np.ndarray) -> float:
    """Full width at half maximum by linear interpolation of the two crossings next to the highest sample (NaN when a side never drops to half)."""
    top = int(np.argmax(s))
    half = s[top] / 2.0
    lo = top
    while lo > 0 and s[lo - 1] > half:
        lo -= 1
    hi = top
    while hi < s.size - 1 and s[hi + 1] > half:
        hi += 1
    if lo == 0 or hi == s.size - 1:
        return float("nan")
    left = lam[lo - 1] + (half - s[lo - 1]) / (s[lo] - s[lo - 1]) * (lam[lo] - lam[lo - 1])
    right = lam[hi] + (half - s[hi]) / (s[hi + 1] - s[hi]) * (lam[hi + 1] - lam[hi])
    return float(right - left)


@dataclass(frozen=True, eq=False)
class Passbands:
    """The bands of a table, derived (never stored). One entry per table row, in the table's order."""

    set_nm: np.ndarray  # [K]
    centroid_nm: np.ndarray  # [K], NaN where not usable
    sigma_nm: np.ndarray  # [K]
    fwhm_nm: np.ndarray  # [K]
    moments: np.ndarray  # [K, MAX_ORDER + 1]
    usable: np.ndarray  # [K] bool
    why: tuple[str, ...]  # [K], '' for a usable row
    sha256: str = ""
    temperature_c: float | None = None
    source_name: str = ""
    illumination: str = ""

    def shift_nm(self) -> np.ndarray:
        """``centroid - set`` of the usable rows."""
        return (self.centroid_nm - self.set_nm)[self.usable]


def derive(table: PassbandTable) -> Passbands:
    """The bands of every row of *table* (``row_profile`` per row)."""
    table.validate()
    n = table.n_rows
    centroid = np.full(n, np.nan)
    sigma = np.full(n, np.nan)
    fwhm = np.full(n, np.nan)
    moments = np.tile(POINT_MOMENTS, (n, 1))
    usable = np.zeros(n, dtype=bool)
    why: list[str] = []
    for k in range(n):
        p = row_profile(table.pixel_nm, table.counts[k], table.dark, float(table.set_nm[k]))
        usable[k], centroid[k], sigma[k], fwhm[k] = p.usable, p.centroid_nm, p.sigma_nm, p.fwhm_nm
        if p.usable:
            moments[k] = p.moments
        why.append(p.why)
    return Passbands(
        np.asarray(table.set_nm, dtype=np.float64), centroid, sigma, fwhm, moments, usable, tuple(why), table.sha256, table.mean_temperature_c, table.source_name, table.illumination
    )


@dataclass(frozen=True, eq=False)
class CorrectionAxis:
    """What a scan's wavelength axis becomes under a mode. All arrays are aligned with the scan's wavelengths, in the scan's order.

    ``x_nm`` is the abscissa every fit and metric reads (the nominal wavelength for ``none`` and for a point with no band); ``moments`` is None unless the mode is
    ``profile``; ``covered`` says which points have a band; ``key`` goes into every cache key (it changes with the mode, the table and the algorithm)."""

    mode: str
    x_nm: np.ndarray
    moments: np.ndarray | None
    covered: np.ndarray
    key: str
    info: dict = field(default_factory=dict)
    """What the correction was, as plain data for the record beside an exported table (mode, the table's name, checksum, light source and temperature, how many points have a band)."""

    @property
    def n_covered(self) -> int:
        return int(self.covered.sum())


def nominal_axis(wavelengths_nm) -> CorrectionAxis:
    """The identity: mode ``none``."""
    wl = np.asarray(wavelengths_nm, dtype=np.float64)
    return CorrectionAxis("none", wl, None, np.zeros(wl.size, dtype=bool), "none")


def match_rows(passbands: Passbands, wavelengths_nm) -> np.ndarray:
    """``int [W]``: the usable table row that matches each scan wavelength (set wavelengths within ``MATCH_TOLERANCE_NM``), -1 where there is none."""
    wl = np.asarray(wavelengths_nm, dtype=np.float64)
    out = np.full(wl.size, -1, dtype=np.int64)
    rows = np.flatnonzero(passbands.usable)
    if rows.size == 0 or wl.size == 0:
        return out
    sets = passbands.set_nm[rows]
    for i, w in enumerate(wl):
        if not np.isfinite(w):
            continue
        j = int(np.argmin(np.abs(sets - w)))
        if abs(sets[j] - w) <= MATCH_TOLERANCE_NM:
            out[i] = rows[j]
    return out


def correction_axis(passbands: Passbands | None, wavelengths_nm, mode: str) -> CorrectionAxis:
    """The scan's wavelength axis under *mode*. ``none`` (or no table) gives the nominal axis; a point with no band keeps its nominal wavelength."""
    mode = str(mode).strip().lower()
    if mode not in MODES:
        raise ValueError(f"Unknown spectral correction mode {mode!r}.")
    wl = np.asarray(wavelengths_nm, dtype=np.float64)
    if mode == "none" or passbands is None:
        return nominal_axis(wl)
    rows = match_rows(passbands, wl)
    covered = rows >= 0
    x = wl.copy()
    x[covered] = passbands.centroid_nm[rows[covered]]
    moments = None
    if mode == "profile":
        moments = np.tile(POINT_MOMENTS, (wl.size, 1))
        moments[covered] = passbands.moments[rows[covered]]
    key = f"{mode}:{passbands.sha256[:16]}:v{PASSBAND_ALGORITHM_VERSION}"
    info = {
        "mode": mode, "table": passbands.source_name, "sha256": passbands.sha256, "illumination": passbands.illumination, "filter_temperature_c": passbands.temperature_c,
        "points_with_a_band": int(covered.sum()), "points": int(wl.size), "algorithm_version": PASSBAND_ALGORITHM_VERSION,
    }
    return CorrectionAxis(mode, x, moments, covered, key, info)


def passband_vander(x_nm: np.ndarray, moments: np.ndarray, degree: int, middle: float, half: float) -> np.ndarray:
    """``B[k, m] = integral w_k(lambda) t(lambda)^m dlambda`` for ``m = 0 .. degree``, with ``t = (lambda - middle) / half``: the polynomial design matrix of the
    passband-averaged points (module docstring). *x_nm* are the bands' centroids (``float [n]``), *moments* ``[n, >= degree + 1]``. With every band of zero width this is
    exactly ``numpy.polynomial.polynomial.polyvander((x - middle) / half, degree)``."""
    if degree > moments.shape[1] - 1:
        raise ValueError(f"The passband moments go up to order {moments.shape[1] - 1}; a polynomial of order {degree} needs more.")
    x_nm = np.asarray(x_nm, dtype=np.float64)
    t0 = (x_nm - middle) / half
    inv = 1.0 / half
    out = np.empty((x_nm.size, degree + 1))
    for m in range(degree + 1):
        i = np.arange(m + 1)
        binom = np.array([comb(m, int(j)) for j in i], dtype=np.float64)
        terms = binom[None, :] * (inv**i)[None, :] * moments[:, : m + 1] * t0[:, None] ** (m - i)[None, :]
        out[:, m] = terms.sum(axis=1)
    return out


def describe(passbands: Passbands, wavelengths_nm=None) -> dict[str, object]:
    """Plain numbers about a table (and, with the scan's wavelengths, how much of the scan it covers) for the "i" text and the import report."""
    ok = passbands.usable
    shift = passbands.shift_nm()
    out: dict[str, object] = {
        "rows": int(passbands.set_nm.size),
        "usable_rows": int(ok.sum()),
        "set_min_nm": float(passbands.set_nm.min()),
        "set_max_nm": float(passbands.set_nm.max()),
        "temperature_c": passbands.temperature_c,
        "shift_mean_nm": float(shift.mean()) if shift.size else float("nan"),
        "shift_sd_nm": float(shift.std()) if shift.size else float("nan"),
        "shift_min_nm": float(shift.min()) if shift.size else float("nan"),
        "shift_max_nm": float(shift.max()) if shift.size else float("nan"),
        "fwhm_min_nm": float(np.nanmin(passbands.fwhm_nm[ok])) if ok.any() else float("nan"),
        "fwhm_max_nm": float(np.nanmax(passbands.fwhm_nm[ok])) if ok.any() else float("nan"),
        "unusable": [(float(passbands.set_nm[k]), passbands.why[k]) for k in np.flatnonzero(~ok)],
    }
    if wavelengths_nm is not None:
        rows = match_rows(passbands, wavelengths_nm)
        out["scan_points"] = int(rows.size)
        out["scan_covered"] = int((rows >= 0).sum())
    return out
