"""The Spectra ribbon's "Spectral correction" group and its "i" icon (2026-11-20). Display only: it reads ``SpectralCorrectionModule`` and reports the user's pick.

**The pick-up** (first group after Live, before Formula): None / Centroid / Full profile (``analysis/spectral_passband.py``). Picking one sets the module's mode; the module tells
the scan results, which hand the display a scan that carries the correction, so every panel redraws and recomputes. A pick that cannot be applied (no table imported yet) is
refused with a sentence in the status bar and the pick-up goes back to what is in force: a control does what it says, or says why not.

**The "i" icon** works like the Image panel's: it does nothing on click, and hovering it shows the explanation: what the correction is, the maths of each choice, what is in force
now (the table, how much of this scan it covers, what the current fit does with the mode) and what it does not know. The text is built from the live state, so it cannot drift from
what is wired (``info_html`` is a plain function of that state, tested without Qt widgets).
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from PyQt6.QtCore import QObject, QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.scan_results import ScanResultsModule
from ...analysis.settings_module import AnalysisSettingsModule
from ...analysis.spectral_passband import MODE_LABELS, MODES, Passbands, describe
from ...selection.spectral_correction_module import SpectralCorrectionModule
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from .pick_menu import PickMenu, correction_picture

CHOICE_TIPS = {
    "none": "The commanded wavelength of each point is its x value, as before.",
    "centroid": "Each point's x value is the centre of gravity of its measured band (where the light really was).",
    "profile": "The fit also models that each point is an average over its band: the curve is the spectrum with that broadening taken out (polynomial fit; other fits read Centroid).",
}


def fit_effect(mode: str, fit_method: str) -> str:
    """One sentence: what the picked mode does with the fit method in force. Plain text; ``info_html`` escapes it."""
    if mode == "none":
        return "No correction: every fit reads the commanded wavelengths."
    method = str(fit_method).strip().lower()
    if mode == "centroid":
        return "Every fit reads the centres of the bands as its x values."
    if method == "polynomial":
        return "Polynomial fit: the full profile is applied. The drawn curve and the maximum / centroid are those of the underlying spectrum; the points are its band averages."
    if method in ("gaussian", "voigt"):
        return f"{method.capitalize()} fit: read as Centroid. The band averaging is built for the polynomial fit only; the {method} fit uses the band centres."
    if method == "template":
        return ("Template shift: read as Centroid. The reference spectrum has the same broadening as the spectrum, so only the x values need the correction.")
    if method == "response":
        return "Response shape: read as Centroid. The shape is learned from the measured changes, which carry the same broadening."
    return "No fit: read as Centroid. With no fitted curve there is nothing to take the broadening out of."


def info_html(
    passbands: Passbands | None,
    source_name: str,
    illumination: str,
    mode: str,
    fit_method: str,
    wavelengths: Sequence[float] | None,
    problem: str = "",
) -> str:
    """The "i" text (rich text). Everything that depends on the session is in the "Now" paragraph; the rest is the explanation."""
    now: list[str] = []
    if problem:
        now.append(f"<span style='color:#d9822b'><b>{escape(problem)}</b></span> No correction is applied meanwhile.")
    elif passbands is None:
        now.append("No table imported: File &rarr; Import &rarr; Spectral Correction&hellip; (the probe's spectral sweep). Nothing is corrected until then.")
    else:
        d = describe(passbands, wavelengths)
        temp = "no temperature given" if d["temperature_c"] is None else f"filter at {d['temperature_c']:.1f}&nbsp;&deg;C"
        now.append(
            f"Table: {escape(source_name)}; light source: {escape(illumination) or 'not given'}; {temp}; {d['usable_rows']} of {d['rows']} bands "
            f"({d['set_min_nm']:g}&ndash;{d['set_max_nm']:g}&nbsp;nm), width (FWHM) {d['fwhm_min_nm']:.1f}&ndash;{d['fwhm_max_nm']:.1f}&nbsp;nm. "
            f"Band centre minus setting: mean {d['shift_mean_nm']:+.2f}&nbsp;nm, scatter {d['shift_sd_nm']:.2f}&nbsp;nm."
        )
        if wavelengths:
            n, k = d["scan_points"], d["scan_covered"]
            tail = "" if k == n else f" The other {n - k} keep their commanded wavelength."
            now.append(f"This scan: {k} of {n} wavelengths have a band.{tail}")
        else:
            now.append("This scan: not loaded yet, so the coverage is not known.")
        now.append(f"<b>{MODE_LABELS.get(mode, mode)}.</b> {escape(fit_effect(mode, fit_method))}")
    return (
        "<table width='560'><tr><td>"
        "<b>Spectral correction</b><br>"
        "Each image is taken through a tunable filter (LCTF). It passes a <i>band</i> (about 4&nbsp;nm wide at 420&nbsp;nm, 15&nbsp;nm at 700&nbsp;nm) whose centre is not exactly "
        "the commanded wavelength. A spectrometer measured every band (the probe's spectral sweep); this uses them. Only how spectra are drawn and fitted changes: the images, the "
        "scan and its stored numbers are never touched."
        "<p><b>Model.</b> Point <i>k</i> is the spectrum <i>f</i> averaged over its band: "
        "<i>y</i><sub>k</sub> = &int; <i>w</i><sub>k</sub>(&lambda;) <i>f</i>(&lambda;) d&lambda;, with <i>w</i><sub>k</sub> the measured band, area 1 "
        "(spectrometer and camera taken as flat).</p>"
        "<p><b>None.</b> x = the commanded wavelength &lambda;<sub>k</sub>.<br>"
        "<b>Centroid.</b> x = the band's centre of gravity "
        "&lambda;&#772;<sub>k</sub> = &int; &lambda; <i>w</i><sub>k</sub> d&lambda;. Expanding around it, "
        "<i>y</i><sub>k</sub> = <i>f</i>(&lambda;&#772;<sub>k</sub>) + &frac12;&sigma;<sub>k</sub>&sup2; <i>f</i>&Prime;(&lambda;&#772;<sub>k</sub>) + &hellip; "
        "(&sigma;<sub>k</sub>: the band's standard deviation, 1.6&ndash;7&nbsp;nm): the first-order error is gone, the curvature term stays.<br>"
        "<b>Full profile.</b> The polynomial <i>P</i> is fitted so that its band averages match the points: "
        "<i>y</i><sub>k</sub> = &Sigma;<sub>m</sub> <i>c</i><sub>m</sub> <i>B</i><sub>km</sub>, with "
        "<i>B</i><sub>km</sub> = &int; <i>w</i><sub>k</sub> <i>t</i><sup>m</sup> d&lambda; (<i>t</i>: the window's scaled wavelength). That is the usual least-squares fit with "
        "the Vandermonde matrix <i>t</i><sub>k</sub><sup>m</sup> replaced by <i>B</i>; <i>B</i> follows from the band's central moments "
        "&mu;<sub>i</sub> = &int; (&lambda; &minus; &lambda;&#772;)<sup>i</sup> <i>w</i> d&lambda;. The curve drawn, and its maximum or centroid, are <i>P</i>: the spectrum with the "
        "band's broadening taken out (simulated: without it a 30&nbsp;nm wide peak at 700&nbsp;nm reads 8&nbsp;nm too wide and 9&nbsp;% too low; a 100&nbsp;nm wide one at 600&nbsp;nm, 1&nbsp;nm and 2&nbsp;%).</p>"
        f"<p><b>Now.</b> {'<br>'.join(now)}</p>"
        "<p><b>What it does not know.</b> The table is right only for the lamp, filter and <b>temperature</b> of the sweep; the filter temperature during your measurement is not "
        "recorded (two sweeps suggest about &minus;0.1&nbsp;nm per &deg;C: not verified). The camera and the spectrometer are taken as flat; a slope <i>s</i> of lamp&times;camera "
        "response moves the true centre by &sigma;&sup2;<i>s</i> (0.17&nbsp;nm at 700&nbsp;nm for a sensitivity falling 0.4&nbsp;%/nm). The polynomial averages the absorbance, not "
        "the transmittance: up to 0.8&nbsp;% of a peak's height at A&nbsp;=&nbsp;0.3 (2.8&nbsp;% at A&nbsp;=&nbsp;1, a 30&nbsp;nm wide peak at 700&nbsp;nm), none for the ratio formula. "
        "Choose a mode and compare: the three can be switched freely.</p>"
        "</td></tr></table>"
    )


class CorrectionControls(QObject):
    """The pick-up and the "i" button, and the wiring that keeps them in step with the modules."""

    def __init__(
        self, spectral: SpectralCorrectionModule, scan_results: ScanResultsModule, settings: AnalysisSettingsModule, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self._spectral = spectral
        self._scan = scan_results
        self._settings = settings
        self._syncing = False

        self.pick = PickMenu(parent)
        names = {"none": "None", "centroid": "Centroid", "profile": "Full profile"}
        for key in MODES:
            self.pick.add_choice(key, names[key], CHOICE_TIPS[key], correction_picture(key))
        self.pick.finish()
        self.pick.setToolTip("Where each point of a spectrum is drawn and fitted: at the commanded wavelength, at the centre of its measured filter band, or with the band's averaging modelled.")

        self.info = QToolButton(parent)
        self.info.setAutoRaise(True)
        self.info.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self.info.setIconSize(QSize(ICON_SIZE, ICON_SIZE))

        self.pick.currentIndexChanged.connect(self._on_pick)
        spectral.changed.connect(self.refresh)
        scan_results.results_changed.connect(self.refresh)  # the coverage of the scan's wavelengths
        settings.settings_changed.connect(lambda _change: self.refresh())  # the fit method decides what a mode does
        self.refresh()
        self.refresh_theme()

    def _on_pick(self, _index: int) -> None:
        if self._syncing:
            return
        key = self.pick.currentData()
        if key is not None and not self._spectral.set_mode(key):
            self.refresh()  # refused (no table): the pick goes back to what is in force

    def refresh(self) -> None:
        self._syncing = True
        try:
            self.pick.setCurrentIndex(max(self.pick.findData(self._spectral.mode()), 0))
        finally:
            self._syncing = False
        table = self._spectral.table()
        stored = self._scan.stored()
        self.info.setToolTip(
            info_html(
                self._spectral.passbands(),
                "" if table is None else table.source_name,
                "" if table is None else table.illumination,
                self._spectral.mode(),
                self._settings.metric_settings().fit_method,
                None if stored is None else stored.wavelength_nm,
                self._spectral.problem(),
            )
        )

    def refresh_theme(self) -> None:
        self.pick.refresh_theme()
        self.info.setIcon(load_tabler_icon("info-circle", color=get_active_theme().text_muted, size=ICON_SIZE * 2))
