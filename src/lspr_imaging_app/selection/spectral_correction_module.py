"""``SpectralCorrectionModule``: the measured LCTF passbands of the session and the correction mode the user picked (2026-11-20).

Owns two facts and nothing else (the maths is ``analysis/spectral_passband.py``; the display reads them through ``ScanResultsModule``, which ``app_rewrite`` tells):

* the **table**: the spectrometer rows of a spectral sweep exactly as imported (``passband_store.PassbandTable``), kept in the session folder as
  ``spectral_correction/spectral_correction.h5``, and the bands derived from it (``Passbands``, ~50 ms, never stored);
* the **mode**: ``none`` / ``centroid`` / ``profile`` (``spectral_passband.MODES``), the user's choice, saved with the session. A new session starts at ``none``, so no
  number changes until the user picks one. An import never changes the mode.

Like the refractive indices (``ExperimentPlanModule``), the table is imported by the user (File -> Import -> Spectral Correction...), copied into the session, and a
checksum of the imported file(s) is kept so a changed table is noticed. The session's JSON holds only a small block (``state()``: mode, file name, checksum, source name,
illumination, import time; session schema 1.13); the rows live in the HDF5 file. **Nothing here touches the scan or any raw data.**

**Off the GUI thread.** Reading the CSV, writing the HDF5 file and reading it back at session open run on a plain thread; the result comes back through a queued signal.
A result from before the session changed is dropped (``_generation``).

**A table that cannot be read** (the file is missing, damaged or its checksum is not the one the session recorded) is *said*, never hidden: ``problem()`` has the sentence (the
Spectra panel's "i" text and the status bar show it), the display uses no correction meanwhile, and the session keeps its block unchanged, so the table can still be found
again (a moved folder, a drive that was not connected).

The **camera response** (a manufacturer's relative spectral sensitivity, File -> Import -> Camera Response...) is a second, separate import: it belongs to the camera, not the lamp. It is small
(about 200 points), so it is kept in the session's JSON block itself; it weights every band when the bands are derived (``derive``), so importing or removing one re-derives the bands (~50 ms, on a thread).

Not built yet (``docs/spectral_correction_2026-11-20.md``): a temperature series (one table per filter temperature, interpolated).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..analysis.passband_store import FILE_NAME, read_camera_response, read_sweep, read_table, write_table
from ..analysis.spectral_passband import MODE_LABELS, MODES, CameraResponse, PassbandError, PassbandTable, Passbands, derive, describe
from ..applog.events import log_event

logger = logging.getLogger(__name__)

SUBFOLDER = "spectral_correction"


class SpectralCorrectionModule(QObject):
    changed = pyqtSignal()  # the table or the mode changed: the display recomputes, the session is worth saving
    notice = pyqtSignal(str)  # a line for the status bar
    import_finished = pyqtSignal(object, str)  # PassbandTable | None, error sentence ('' when it worked)
    response_finished = pyqtSignal(object, str)  # CameraResponse | None, error sentence ('' when it worked; None and '' = the response was removed)

    _loaded = pyqtSignal(int, object, object, str, bool)  # generation, table, passbands, error, is it an import (else: a session restore)
    _response_loaded = pyqtSignal(int, object, object, str)  # generation, response (None: removed), passbands (None: no table), error

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._table: PassbandTable | None = None
        self._passbands: Passbands | None = None
        self._mode = "none"
        self._problem = ""
        self._kept: dict | None = None  # the session block of a table that could not be read: written back as it was
        self._response: CameraResponse | None = None
        self._kept_response: object = None  # the block's camera response when it could not be read: written back as it was
        self._generation = 0
        self._wavelengths: Callable[[], Sequence[float] | None] = lambda: None
        self._threads: list[threading.Thread] = []
        self._loaded.connect(self._on_loaded)
        self._response_loaded.connect(self._on_response_loaded)

    # -- query interface -------------------------------------------------------------------------------------------------------

    def table(self) -> PassbandTable | None:
        return self._table

    def passbands(self) -> Passbands | None:
        """The bands of the table, or None (no table, or one that could not be read)."""
        return self._passbands

    def mode(self) -> str:
        return self._mode

    def problem(self) -> str:
        """Why the session's table could not be used ('' when there is no problem)."""
        return self._problem

    def has_table(self) -> bool:
        return self._passbands is not None

    def camera_response(self) -> CameraResponse | None:
        return self._response

    def has_response(self) -> bool:
        return self._response is not None

    def table_path(self) -> Path | None:
        return None if self._root is None else self._root / SUBFOLDER / FILE_NAME

    def set_wavelength_source(self, source: Callable[[], Sequence[float] | None]) -> None:
        """Where the scan's wavelengths come from (``app_rewrite``: the scan results), for the coverage line of the import report."""
        self._wavelengths = source

    def scan_wavelengths(self) -> Sequence[float] | None:
        return self._wavelengths()

    # -- the user's choices ----------------------------------------------------------------------------------------------------

    def set_mode(self, mode: str) -> bool:
        """The correction the display applies. Refused (with a status-bar sentence, returning False) when there is no table to correct with."""
        mode = str(mode).strip().lower()
        if mode not in MODES:
            raise ValueError(f"Unknown spectral correction mode {mode!r}.")
        if mode == self._mode:
            return True
        if mode != "none" and self._passbands is None:
            self.notice.emit(self._problem or "No spectral-correction table: File > Import > Spectral Correction... first.")
            return False
        self._mode = mode
        self.changed.emit()
        return True

    # -- import ----------------------------------------------------------------------------------------------------------------

    def import_sweep(self, sweep: Path, dark: Path | None = None, illumination: str = "") -> None:
        """Read a probe sweep (and its dark-spectrum file, if it came separately), keep a copy in the session, and use it. Returns at once; ``import_finished`` says how it
        went. The mode is left as it is."""
        path = self.table_path()
        if path is None:
            self.import_finished.emit(None, "No session is open: open a dataset first.")
            return
        generation, response = self._generation, self._response
        self._start(lambda: self._import_work(generation, path, Path(sweep), None if dark is None else Path(dark), illumination, response), "spectral-correction-import")

    def _import_work(self, generation: int, path: Path, sweep: Path, dark: Path | None, illumination: str, response: CameraResponse | None) -> None:
        started = time.perf_counter()
        try:
            table = read_sweep(sweep, dark, illumination)
            passbands = derive(table, response)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_table(path, table)
        except (PassbandError, OSError, UnicodeDecodeError, ValueError) as exc:
            self._loaded.emit(generation, None, None, f"{sweep.name}: {exc}", True)
            return
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Importing the spectral sweep failed")
            self._loaded.emit(generation, None, None, f"{sweep.name}: {exc}", True)
            return
        log_event(logger, "spectral_correction.import", logging.INFO, rows=table.n_rows, usable=int(passbands.usable.sum()), ms=round((time.perf_counter() - started) * 1000))
        self._loaded.emit(generation, table, passbands, "", True)

    def import_camera_response(self, path: Path) -> None:
        """Read a camera's relative spectral response (``passband_store.parse_camera_response``) and weight the bands by it. Returns at once; ``response_finished`` says how it went. The table
        and the mode are left as they are; a response is kept with the session, so a session must be open."""
        if self._root is None:
            self.response_finished.emit(None, "No session is open: open a dataset first.")
            return
        generation, table = self._generation, self._table
        self._start(lambda: self._response_work(generation, Path(path), table), "spectral-correction-camera")

    def _response_work(self, generation: int, path: Path, table: PassbandTable | None) -> None:
        try:
            response = read_camera_response(path)
            passbands = None if table is None else derive(table, response)
        except (PassbandError, OSError, UnicodeDecodeError, ValueError) as exc:
            self._response_loaded.emit(generation, None, None, f"{path.name}: {exc}")
            return
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Importing the camera response failed")
            self._response_loaded.emit(generation, None, None, f"{path.name}: {exc}")
            return
        self._response_loaded.emit(generation, response, passbands, "")

    def remove_camera_response(self) -> None:
        """Go back to a flat camera: the bands are derived again without the response."""
        if self._response is None and self._kept_response is None:
            return
        self._kept_response = None
        if self._table is None:
            self._response = None
            self.changed.emit()
            self.response_finished.emit(None, "")
            return
        generation, table = self._generation, self._table
        self._start(lambda: self._rederive_work(generation, table), "spectral-correction-camera")

    def _rederive_work(self, generation: int, table: PassbandTable) -> None:
        try:
            passbands = derive(table)
        except (PassbandError, ValueError) as exc:
            self._response_loaded.emit(generation, None, None, str(exc))
            return
        self._response_loaded.emit(generation, None, passbands, "")

    def _on_response_loaded(self, generation: int, response: object, passbands: object, error: str) -> None:
        if generation != self._generation:
            return  # the session changed meanwhile
        if error:
            self.response_finished.emit(None, error)
            return
        self._response = response if isinstance(response, CameraResponse) else None
        self._kept_response = None
        if passbands is not None:
            self._passbands = passbands
        self.changed.emit()
        self.response_finished.emit(self._response, "")

    # -- the session -----------------------------------------------------------------------------------------------------------

    def set_root(self, root: Path | None) -> None:
        """The session folder in use. A table or load from another session is dropped."""
        self._generation += 1
        self._root = None if root is None else Path(root)

    def state(self) -> dict | None:
        """The session's block (plain data, safe for the autosave thread), or None when there is nothing to keep."""
        block: dict = {}
        if self._table is not None:
            t = self._table
            block = {
                "mode": self._mode,
                "file": f"{SUBFOLDER}/{FILE_NAME}",
                "sha256": t.sha256,
                "source_name": t.source_name,
                "illumination": t.illumination,
                "imported_at": t.imported_at,
            }
        elif self._kept is not None:
            block = dict(self._kept)
        block.pop("camera_response", None)
        if self._response is not None:
            block["camera_response"] = self._response.to_payload()
        elif self._kept_response is not None:
            block["camera_response"] = self._kept_response
        if not block:
            return None
        block.setdefault("mode", self._mode)
        return block

    def restore_state(self, payload: object) -> None:
        """Replace everything with a session's block (None: a blank session) and read its table back on a thread."""
        self._generation += 1
        self._table = self._passbands = None
        self._response = None
        self._kept_response = None
        self._problem = ""
        self._kept = None
        self._mode = "none"
        if isinstance(payload, dict) and payload.get("camera_response") is not None:
            try:
                self._response = CameraResponse.from_payload(payload["camera_response"])
            except PassbandError as exc:
                self._kept_response = payload["camera_response"]  # written back as it was; said, not hidden
                self._problem = f"The session's camera response could not be used: {exc}"
        if isinstance(payload, dict) and payload.get("file") and self._root is not None:
            mode = str(payload.get("mode", "none"))
            self._mode = mode if mode in MODES else "none"
            self._kept = dict(payload)  # kept until the table has been read; a failed read leaves it in place
            generation, root, relative, sha, response = self._generation, self._root, str(payload["file"]), str(payload.get("sha256", "")), self._response
            self._start(lambda: self._read_work(generation, root, relative, sha, response), "spectral-correction-load")
        elif isinstance(payload, dict) and payload.get("file"):
            self._problem = "The session has a spectral-correction table, but its folder is not known."
            self._kept = dict(payload)
        self.changed.emit()

    def _read_work(self, generation: int, root: Path, relative: str, sha256: str, response: CameraResponse | None) -> None:
        try:
            path = (root / relative).resolve()
            if root.resolve() not in path.parents:  # the block comes from a JSON file: it may not point outside the session
                raise PassbandError("The session's spectral-correction file is not inside the session folder.")
            table = read_table(path)
            if sha256 and table.sha256 != sha256:
                raise PassbandError("The spectral-correction file is not the one the session was saved with (its checksum differs).")
            passbands = derive(table, response)
        except (PassbandError, OSError, ValueError) as exc:
            self._loaded.emit(generation, None, None, str(exc), False)
            return
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Reading the spectral-correction table failed")
            self._loaded.emit(generation, None, None, str(exc), False)
            return
        self._loaded.emit(generation, table, passbands, "", False)

    def _on_loaded(self, generation: int, table: object, passbands: object, error: str, imported: bool) -> None:
        if generation != self._generation:
            return  # the session changed meanwhile
        if error:
            if imported:
                self.import_finished.emit(None, error)
                return
            self._problem = f"The session's spectral-correction table could not be used: {error}"
            self._mode = "none"  # nothing is corrected, so the pick-up must not say otherwise; the session's block (with its mode) is kept as it was
            self.notice.emit(self._problem)
            self.changed.emit()
            return
        self._table, self._passbands = table, passbands
        self._problem = ""
        self._kept = None
        self.changed.emit()
        if imported:
            self.import_finished.emit(table, "")

    def _start(self, work: Callable[[], None], name: str) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=work, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def shutdown(self) -> None:
        for thread in self._threads:
            thread.join(timeout=2.0)


def camera_report(response: CameraResponse, passbands: Passbands | None, mode: str) -> str:
    """The sentences the camera-response import shows (what was read, what it does to the bands, what it is not)."""
    peak = float(response.wavelength_nm[int(response.response.argmax())])
    lines = [f"Camera response read from {response.source_name}: {response.wavelength_nm.size} points, {response.wavelength_nm[0]:g}-{response.wavelength_nm[-1]:g} nm, highest at {peak:g} nm."]
    if passbands is None:
        lines.append("There is no spectral-correction table yet: the response is kept with the session and weights the bands once a table is imported.")
    else:
        d = describe(passbands)
        lines.append(
            f"Weighted by it, the band centres move by {d['camera_shift_mean_nm']:+.2f} nm on average (from {d['camera_shift_min_nm']:+.2f} to {d['camera_shift_max_nm']:+.2f} nm; "
            "the red end moves most)."
        )
        lines.append(f"Spectral correction is {MODE_LABELS.get(mode, mode)}: " + ("the shown numbers do not change until you pick Centroid or Full profile." if mode == "none" else "the shown numbers change now."))
    lines.append("It is a relative sensitivity of the sensor only: the lens and the microscope's optics are not in it. File -> Import -> Remove Camera Response goes back to a flat camera.")
    return "\n".join(lines)


def import_report(table: PassbandTable, passbands: Passbands, wavelengths: Sequence[float] | None, mode: str) -> str:
    """The sentences the import shows (what was read, what it covers, what to do next)."""
    d = describe(passbands, wavelengths)
    lines = [
        f"{d['usable_rows']} of {d['rows']} bands read from {table.source_name} ({d['set_min_nm']:g}-{d['set_max_nm']:g} nm).",
        f"Band width (FWHM): {d['fwhm_min_nm']:.1f} nm at the blue end, {d['fwhm_max_nm']:.1f} nm at the red end.",
        f"Band centre minus the setting: mean {d['shift_mean_nm']:+.2f} nm, scatter {d['shift_sd_nm']:.2f} nm (range {d['shift_min_nm']:+.2f} to {d['shift_max_nm']:+.2f} nm).",
    ]
    if d["temperature_c"] is not None:
        lines.append(f"Filter temperature during the sweep: {d['temperature_c']:.1f} °C. The correction is only right at that temperature.")
    else:
        lines.append("The sweep gives no filter temperature.")
    lines.append(f"Light source: {table.illumination or 'not given'}.")
    if d["unusable"]:
        lines.append("No usable band at: " + "; ".join(f"{nm:g} nm ({why})" for nm, why in d["unusable"][:6]) + (" ..." if len(d["unusable"]) > 6 else "") + ". Those settings stay nominal.")
    if wavelengths:
        lines.append(f"The scan has {d['scan_points']} wavelengths; {d['scan_covered']} have a band (the others keep their nominal wavelength).")
    else:
        lines.append("There is no scan yet: the coverage is shown in the Spectra panel once there is one.")
    lines.append(f"Spectral correction is {MODE_LABELS.get(mode, mode)}. Pick Centroid or Full profile in the Spectra panel (first group of the ribbon) to use it.")
    return "\n".join(lines)
