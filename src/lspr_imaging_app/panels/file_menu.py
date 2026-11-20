"""The File menu (2026-11-11): everything the retired Workflow panel let you *do* with a dataset.

    File
      Open Dataset...                Ctrl+O
      Show Dataset Folder
      -----
      Session                      >  New Session / (one radio entry per session: the checked one is active)
      -----
      Import                       >  Experiment Plan / Metadata...  /  Refractive Indices... (2026-11-12)  /  Spectral Correction... (2026-11-20)
      Export                       >  Acquisition Metadata (JSON)...
      -----
      Dataset Manager...             Ctrl+I
      -----
      Exit

What there is to *look at* (overview, experiment plan, the sessions list) is in the Dataset Manager window
(`panels/dataset_manager/`). Display only, like the panel was: the menu owns no state, it calls
`DatasetModule.load_dataset_from_folder`, `SessionCoordinator.switch_to` / `create_new` and the metadata import /
export functions of `dataset_manager/experimental_plan.py`.

**This is the one listener of `DatasetModule.dataset_choice_needed` and `dataset_load_failed`** (the Workflow panel's
folder row was, until 2026-11-11). Without it a folder holding several datasets would never open and a failed load
would only reach the log, so it must be wired whenever the window is (`app_rewrite.build_main_window`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox

from ..dataset import DatasetModule
from ..dataset.io import DatasetLoadChoice, resolve_remembered_dataset_choice, save_dataset_choice, summarize_dataset_candidate
from ..dataset.model import ImageDataset
from ..plan.refractive_index import RiFileError, step_numbers_text
from ..selection.experiment_plan_module import ExperimentPlanModule
from ..selection.spectral_correction_module import SpectralCorrectionModule, camera_report, import_report
from ..shortcuts import bind_action
from ..storage.session_coordinator import SessionCoordinator
from ..storage.session_index import SessionRecord
from .dataset_manager import DatasetManagerDialog
from .dataset_manager.experimental_plan import export_metadata_dialog, import_metadata_dialog
from .dataset_manager.sessions import session_label
from .dataset_manager.summary import format_bytes
from .menu_icons import give_persistent_icon

logger = logging.getLogger(__name__)

_FORMAT_LABELS = {"image_stack": "TIFF image stack", "ome_zarr": "OME-Zarr"}


def _sweep_file_kind(path: Path) -> str:
    """"sweep" (the probe's spectral sweep: header starts with requested_nm), "dark" (wavelength_nm,intensity) or "" (anything else): read from the header line, not the name."""
    try:
        with open(path, encoding="utf-8-sig") as handle:
            first = handle.readline().split(",", 1)[0].strip().lower()
    except (OSError, UnicodeDecodeError):
        return ""
    return {"requested_nm": "sweep", "wavelength_nm": "dark"}.get(first, "")


def _describe_candidate(candidate: ImageDataset) -> str:
    summary = summarize_dataset_candidate(candidate)
    format_label = _FORMAT_LABELS.get(summary.source_format, summary.source_format)
    return (
        f"{candidate.folder.name}/  ({format_label})\n"
        f"    {summary.image_count} images, {summary.spectral_cube_count} cubes x "
        f"{summary.wavelength_count} wavelengths, {format_bytes(summary.total_bytes)}"
    )


class FileMenu(QObject):
    """Builds the File menu's entries into ``menu`` and keeps them enabled / checked to match the dataset and session.

    ``status_message`` carries short state text ("Loading dataset from ...") for the status bar."""

    status_message = pyqtSignal(str)

    def __init__(
        self,
        menu: QMenu,
        window: QMainWindow,
        dataset: DatasetModule,
        sessions: SessionCoordinator,
        manager: DatasetManagerDialog,
        *,
        experiment_plan: ExperimentPlanModule | None = None,
        spectral: SpectralCorrectionModule | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._dataset = dataset
        self._sessions = sessions
        self._manager = manager
        self._plan = experiment_plan
        self._spectral = spectral

        self.open_action = give_persistent_icon(menu.addAction("Open Dataset..."), "open_dataset")
        bind_action(self.open_action, "file.open_dataset")
        self.open_action.triggered.connect(self.open_dataset)
        self.show_folder_action = give_persistent_icon(menu.addAction("Show Dataset Folder"), "show_folder")
        self.show_folder_action.setToolTip("Open the dataset folder in File Explorer.")
        self.show_folder_action.triggered.connect(self.show_dataset_folder)
        menu.addSeparator()

        self.session_menu = give_persistent_icon(menu.addMenu("Session"), "session")
        self.new_session_action = give_persistent_icon(self.session_menu.addAction("New Session"), "new")
        self.new_session_action.setToolTip("Start a new, blank session over this dataset.")
        self.new_session_action.triggered.connect(self._on_new_session)
        self.session_menu.addSeparator()
        self._session_group = QActionGroup(self)
        self._session_group.setExclusive(True)
        self._session_actions: list[QAction] = []
        menu.addSeparator()

        import_menu = give_persistent_icon(menu.addMenu("Import"), "import")
        self.import_plan_action = give_persistent_icon(import_menu.addAction("Experiment Plan / Metadata..."), "import")
        self.import_plan_action.setToolTip(
            "A measuring-times CSV, metaData.txt, the pump-plan CSV, a native measurement file or a previously exported "
            "metadata file. Several files can be picked at once: each is recognised by its content, not its name."
        )
        self.import_plan_action.triggered.connect(self.import_plan)
        self.import_ri_action = give_persistent_icon(import_menu.addAction("Refractive Indices..."), "import")
        self.import_ri_action.setToolTip(
            "A tab-separated RI table (RIs.txt: Sample, RI, Std, Temp, N, Date). One value per solution, given to every step whose note "
            "starts with that solution's name. The values are copied into the session; RIs.txt in the dataset folder is imported "
            "automatically when the session has none."
        )
        self.import_ri_action.triggered.connect(self.import_refractive_indices)
        self.import_ri_action.setVisible(experiment_plan is not None)
        self.import_spectral_action = give_persistent_icon(import_menu.addAction("Spectral Correction..."), "import_spectral")
        self.import_spectral_action.setToolTip(
            "The measured passbands of the tunable filter: a spectral sweep written by the illumination probe (spectral_sweep_*.csv; its dark-spectrum file can be picked "
            "with it). Copied into the session. Nothing changes until you pick Centroid or Full profile in the Spectra panel."
        )
        self.import_spectral_action.triggered.connect(self.import_spectral_correction)
        self.import_spectral_action.setVisible(spectral is not None)
        self.import_camera_action = give_persistent_icon(import_menu.addAction("Camera Response..."), "import_camera")
        self.import_camera_action.setToolTip(
            "A camera's relative spectral sensitivity (wavelength in nm and response, one pair per line; # lines are comments; quantum efficiency in per cent or a relative response: only the shape "
            "is used), for instance from the manufacturer. It weights the measured filter bands. Copied into the session."
        )
        self.import_camera_action.triggered.connect(self.import_camera_response)
        self.import_camera_action.setVisible(spectral is not None)
        self.remove_camera_action = give_persistent_icon(import_menu.addAction("Remove Camera Response"), "delete")
        self.remove_camera_action.setToolTip("Go back to a flat camera: the bands are not weighted by any response.")
        self.remove_camera_action.triggered.connect(lambda: self._spectral.remove_camera_response() if self._spectral is not None else None)
        self.remove_camera_action.setVisible(spectral is not None)
        if spectral is not None:
            spectral.import_finished.connect(self._on_spectral_imported)
            spectral.response_finished.connect(self._on_camera_response_imported)
            spectral.changed.connect(self._sync_enabled)
        export_menu = give_persistent_icon(menu.addMenu("Export"), "export")
        self.export_metadata_action = give_persistent_icon(export_menu.addAction("Acquisition Metadata (JSON)..."), "export")
        self.export_metadata_action.triggered.connect(self.export_metadata)
        menu.addSeparator()

        self.manager_action = give_persistent_icon(menu.addAction("Dataset Manager..."), "dataset_manager")
        bind_action(self.manager_action, "file.dataset_manager")
        self.manager_action.triggered.connect(lambda: self._manager.present())
        menu.addSeparator()
        give_persistent_icon(menu.addAction("E&xit", window.close), "exit")
        menu.setToolTipsVisible(True)

        dataset.dataset_loaded.connect(lambda _loaded: self._sync_enabled())
        dataset.dataset_cleared.connect(self._sync_enabled)
        dataset.dataset_load_failed.connect(self._on_load_failed)
        dataset.dataset_choice_needed.connect(self._on_choice_needed)
        sessions.sessions_changed.connect(self._on_sessions_changed)
        self._sync_enabled()
        self._on_sessions_changed(sessions.sessions(), sessions.active_session_id())

    # -- dataset ------------------------------------------------------------------------------------

    def open_dataset(self) -> None:
        start = self._dataset.dataset_home()
        folder = QFileDialog.getExistingDirectory(self._window, "Open dataset folder", str(start.parent) if start is not None else "")
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, folder: Path) -> None:
        if self._dataset.is_loading():
            self.status_message.emit("A dataset is already loading - please wait for it to finish.")
            return
        self.status_message.emit(f"Loading dataset from {folder.name}...")
        try:
            self._dataset.load_dataset_from_folder(folder)
        except RuntimeError as exc:
            QMessageBox.warning(self._window, "Cannot open dataset", str(exc))

    def show_dataset_folder(self) -> None:
        folder = self._dataset.dataset_home()
        if folder is None or not folder.is_dir():
            QMessageBox.warning(self._window, "Folder not found", f"The dataset folder does not exist: {folder}")
            return
        try:
            os.startfile(str(folder))  # Windows: opens File Explorer
        except OSError as exc:
            QMessageBox.warning(self._window, "Cannot open folder", f"Could not open the folder in File Explorer: {exc}")

    def _on_load_failed(self, message: str) -> None:
        self.status_message.emit("Dataset load failed.")
        QMessageBox.critical(self._window, "Load failed", message)

    def _on_choice_needed(self, choice: DatasetLoadChoice) -> None:
        dataset = resolve_remembered_dataset_choice(choice)
        if dataset is not None:
            if choice.acquisition_metadata is not None:
                dataset.acquisition_metadata = choice.acquisition_metadata
            dataset.home_folder = choice.parent_folder
        else:
            dataset = self._prompt_candidate(choice)
        if dataset is None:
            self.status_message.emit("Load cancelled.")
            return
        self._dataset.load_dataset(dataset)

    def _prompt_candidate(self, choice: DatasetLoadChoice) -> ImageDataset | None:
        """Ask which dataset to load when more than one TIFF-stack / OME-Zarr candidate was found one level under the
        chosen folder (one button per candidate). The answer is remembered for that folder."""
        lines = [f"Found {len(choice.candidates)} datasets under {choice.parent_folder}:"]
        lines.extend(f"\n{_describe_candidate(candidate)}" for candidate in choice.candidates)
        box = QMessageBox(self._window)
        box.setWindowTitle("Choose a dataset to load")
        box.setText("\n".join(lines) + "\n\nWhich one should be loaded?")
        candidate_by_button = {
            box.addButton(candidate.folder.name, QMessageBox.ButtonRole.AcceptRole): candidate
            for candidate in choice.candidates
        }
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        dataset = candidate_by_button.get(box.clickedButton())
        if dataset is not None:
            if choice.acquisition_metadata is not None:
                dataset.acquisition_metadata = choice.acquisition_metadata
            dataset.home_folder = choice.parent_folder
            save_dataset_choice(choice.parent_folder, dataset.folder.name)
        return dataset

    # -- metadata -----------------------------------------------------------------------------------

    def import_plan(self) -> None:
        paths = import_metadata_dialog(self._window, self._dataset)
        if paths:
            self._manager.plan.show_files(paths)
            self.status_message.emit(f"Imported {', '.join(path.name for path in paths)}.")

    def import_refractive_indices(self) -> None:
        if self._plan is None:
            return
        start = self._dataset.dataset_home()
        path_text, _ = QFileDialog.getOpenFileName(
            self._window, "Import refractive indices", str(start) if start is not None else "", "RI table (*.txt *.tsv);;All files (*)"
        )
        if not path_text:
            return
        current = self._plan.ri_table()
        if current is not None and current.has_edits():  # an import replaces the whole table: the maintainer's rule (2026-11-13) is to say so first
            answer = QMessageBox.question(
                self._window, "Replace the edited refractive indices?",
                f"You have edited the refractive index table ({current.edit_summary()}).\n\nImporting {Path(path_text).name} replaces the whole table and these edits are lost "
                "(Edit -> Undo cannot bring them back).\n\nImport anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            table = self._plan.import_ri_file(Path(path_text))
        except (RiFileError, OSError) as exc:
            QMessageBox.critical(self._window, "Refractive indices not imported", f"{Path(path_text).name}: {exc}")
            return
        report = self._plan.ri_usage()
        lines = [f"{len(table.solutions)} solutions read from {table.source_name}."]
        if report.used:
            lines.append(f"Used in this experiment: {', '.join(row.name for row, _steps in report.used)}.")
        if report.missing:
            lines.append(
                "No RI for: " + "; ".join(f"{name} (step {step_numbers_text(steps)})" for name, steps in report.missing) + "."
            )
        if report.unused:
            lines.append(f"Not used by any step (kept in the table): {', '.join(report.unused)}.")
        if table.wavelength_nm is None:
            lines.append("The refractometer wavelength is not given (Edit -> Preferences -> Refractive indices).")
        QMessageBox.information(self._window, "Refractive indices imported", "\n".join(lines))
        self.status_message.emit(f"Refractive indices imported from {table.source_name}.")

    def import_spectral_correction(self) -> None:
        """Pick the probe's sweep file (and its dark-spectrum file, if it is a separate one), say which lamp it was measured with, and import it (the module reads it on a thread)."""
        if self._spectral is None:
            return
        start = self._dataset.dataset_home()
        path_texts, _ = QFileDialog.getOpenFileNames(
            self._window, "Import spectral correction (spectral sweep, and its dark spectrum if separate)", str(start) if start is not None else "", "Spectral sweep (*.csv);;All files (*)"
        )
        if not path_texts:
            return
        sweep, dark = None, None
        for text in path_texts:
            kind = _sweep_file_kind(Path(text))
            if kind == "sweep" and sweep is None:
                sweep = Path(text)
            elif kind == "dark" and dark is None:
                dark = Path(text)
            else:
                QMessageBox.critical(
                    self._window, "Spectral correction not imported",
                    f"{Path(text).name}: pick one spectral sweep (its header starts with requested_nm) and, if it has no DARK row, its dark-spectrum file (wavelength_nm,intensity). "
                    "One sweep per import: a temperature series is not supported yet.",
                )
                return
        if sweep is None:
            QMessageBox.critical(self._window, "Spectral correction not imported", "None of the files is a spectral sweep (its header should start with requested_nm).")
            return
        previous = self._spectral.table()
        if previous is not None:
            answer = QMessageBox.question(
                self._window, "Replace the spectral-correction table?",
                f"This session already has a spectral-correction table ({previous.source_name}, light source: {previous.illumination or 'not given'}).\n\n"
                f"Importing {sweep.name} replaces it. The old table is kept in the session folder as spectral_correction.previous.h5. The mode you picked stays, so the numbers shown change.\n\nImport anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        lamp, accepted = QInputDialog.getText(
            self._window, "Light source of the sweep",
            "Which light source was this sweep measured with?\n(Kept with the table. The correction is only right for the lamp, filter and temperature it was measured with.)",
            text=previous.illumination if previous is not None else "",
        )
        if not accepted:
            return
        self.status_message.emit(f"Importing {sweep.name} ...")
        self._spectral.import_sweep(sweep, dark, lamp)

    def import_camera_response(self) -> None:
        """Pick a camera response file (the module reads it on a thread; ``_on_camera_response_imported`` reports)."""
        if self._spectral is None:
            return
        start = self._dataset.dataset_home()
        path_text, _ = QFileDialog.getOpenFileName(
            self._window, "Import camera response", str(start) if start is not None else "", "Camera response (*.csv *.txt *.tsv);;All files (*)"
        )
        if not path_text:
            return
        previous = self._spectral.camera_response()
        if previous is not None:
            answer = QMessageBox.question(
                self._window, "Replace the camera response?",
                f"This session already has a camera response ({previous.source_name}).\n\nImporting {Path(path_text).name} replaces it, so the bands and, if a correction is on, the numbers shown change.\n\nImport anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.status_message.emit(f"Importing {Path(path_text).name} ...")
        self._spectral.import_camera_response(Path(path_text))

    def _on_camera_response_imported(self, response: object, error: str) -> None:
        if self._spectral is None:
            return
        if error:
            QMessageBox.critical(self._window, "Camera response not imported", error)
            return
        if response is None:
            self.status_message.emit("Camera response removed: the camera is taken as flat.")
            return
        QMessageBox.information(self._window, "Camera response imported", camera_report(response, self._spectral.passbands(), self._spectral.mode()))
        self.status_message.emit(f"Camera response imported from {response.source_name}.")

    def _on_spectral_imported(self, table: object, error: str) -> None:
        if self._spectral is None:
            return
        if error or table is None:
            QMessageBox.critical(self._window, "Spectral correction not imported", error or "The file could not be read.")
            return
        passbands = self._spectral.passbands()
        if passbands is None:
            return
        QMessageBox.information(
            self._window, "Spectral correction imported", import_report(table, passbands, self._spectral.scan_wavelengths(), self._spectral.mode())
        )
        self.status_message.emit(f"Spectral correction imported from {table.source_name}.")

    def export_metadata(self) -> None:
        path = export_metadata_dialog(self._window, self._dataset)
        if path is not None:
            self._manager.plan.show_files([path])
            self.status_message.emit(f"Acquisition metadata saved to {path}.")

    # -- sessions -----------------------------------------------------------------------------------

    def _on_sessions_changed(self, records: tuple[SessionRecord, ...], active_id: str | None) -> None:
        for action in self._session_actions:
            self._session_group.removeAction(action)
            self.session_menu.removeAction(action)
            action.deleteLater()
        self._session_actions = []
        for record in records:
            action = self.session_menu.addAction(session_label(record))
            action.setCheckable(True)
            action.setChecked(record.session_id == active_id)
            action.triggered.connect(lambda _checked=False, session_id=record.session_id: self._switch_to(session_id))
            self._session_group.addAction(action)
            self._session_actions.append(action)
        self.new_session_action.setEnabled(self._sessions.home() is not None)

    def _switch_to(self, session_id: str) -> None:
        if session_id != self._sessions.active_session_id():
            self._sessions.switch_to(session_id)

    def _on_new_session(self) -> None:
        if self._sessions.home() is not None:
            self._sessions.create_new()

    def _sync_enabled(self) -> None:
        loaded = self._dataset.dataset_home() is not None
        for action in (self.show_folder_action, self.import_plan_action, self.import_ri_action, self.import_spectral_action, self.import_camera_action, self.export_metadata_action):
            action.setEnabled(loaded)
        self.remove_camera_action.setEnabled(loaded and self._spectral is not None and self._spectral.has_response())

    @property
    def manager(self) -> DatasetManagerDialog:
        return self._manager
