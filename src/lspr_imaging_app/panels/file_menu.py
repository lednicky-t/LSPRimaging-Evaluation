"""The File menu (2026-11-11): everything the retired Workflow panel let you *do* with a dataset.

    File
      Open Dataset...                Ctrl+O
      Show Dataset Folder
      -----
      Session                      >  New Session / (one radio entry per session: the checked one is active)
      -----
      Import                       >  Experiment Plan / Metadata...  /  Refractive Indices... (2026-11-12)
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
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMenu, QMessageBox

from ..dataset import DatasetModule
from ..dataset.io import DatasetLoadChoice, resolve_remembered_dataset_choice, save_dataset_choice, summarize_dataset_candidate
from ..dataset.model import ImageDataset
from ..plan.refractive_index import RiFileError, step_numbers_text
from ..selection.experiment_plan_module import ExperimentPlanModule
from ..shortcuts import bind_action
from ..storage.session_coordinator import SessionCoordinator
from ..storage.session_index import SessionRecord
from .dataset_manager import DatasetManagerDialog
from .dataset_manager.experimental_plan import export_metadata_dialog, import_metadata_dialog
from .dataset_manager.sessions import session_label
from .dataset_manager.summary import format_bytes

logger = logging.getLogger(__name__)

_FORMAT_LABELS = {"image_stack": "TIFF image stack", "ome_zarr": "OME-Zarr"}


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
    ) -> None:
        super().__init__(window)
        self._window = window
        self._dataset = dataset
        self._sessions = sessions
        self._manager = manager
        self._plan = experiment_plan

        self.open_action = menu.addAction("Open Dataset...")
        bind_action(self.open_action, "file.open_dataset")
        self.open_action.triggered.connect(self.open_dataset)
        self.show_folder_action = menu.addAction("Show Dataset Folder")
        self.show_folder_action.setToolTip("Open the dataset folder in File Explorer.")
        self.show_folder_action.triggered.connect(self.show_dataset_folder)
        menu.addSeparator()

        self.session_menu = menu.addMenu("Session")
        self.new_session_action = self.session_menu.addAction("New Session")
        self.new_session_action.setToolTip("Start a new, blank session over this dataset.")
        self.new_session_action.triggered.connect(self._on_new_session)
        self.session_menu.addSeparator()
        self._session_group = QActionGroup(self)
        self._session_group.setExclusive(True)
        self._session_actions: list[QAction] = []
        menu.addSeparator()

        import_menu = menu.addMenu("Import")
        self.import_plan_action = import_menu.addAction("Experiment Plan / Metadata...")
        self.import_plan_action.setToolTip(
            "A measuring-times CSV, metaData.txt, the pump-plan CSV, a native measurement file or a previously exported "
            "metadata file. Several files can be picked at once: each is recognised by its content, not its name."
        )
        self.import_plan_action.triggered.connect(self.import_plan)
        self.import_ri_action = import_menu.addAction("Refractive Indices...")
        self.import_ri_action.setToolTip(
            "A tab-separated RI table (RIs.txt: Sample, RI, Std, Temp, N, Date). One value per solution, given to every step whose note "
            "starts with that solution's name. The values are copied into the session; RIs.txt in the dataset folder is imported "
            "automatically when the session has none."
        )
        self.import_ri_action.triggered.connect(self.import_refractive_indices)
        self.import_ri_action.setVisible(experiment_plan is not None)
        export_menu = menu.addMenu("Export")
        self.export_metadata_action = export_menu.addAction("Acquisition Metadata (JSON)...")
        self.export_metadata_action.triggered.connect(self.export_metadata)
        menu.addSeparator()

        self.manager_action = menu.addAction("Dataset Manager...")
        bind_action(self.manager_action, "file.dataset_manager")
        self.manager_action.triggered.connect(lambda: self._manager.present())
        menu.addSeparator()
        menu.addAction("E&xit", window.close)
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
        for action in (self.show_folder_action, self.import_plan_action, self.import_ri_action, self.export_metadata_action):
            action.setEnabled(loaded)

    @property
    def manager(self) -> DatasetManagerDialog:
        return self._manager
