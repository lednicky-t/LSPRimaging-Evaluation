"""``PlotExportButton``: the one export control of every plot area (2026-11-03; design ``docs/results_export_design_2026-10-29.md`` section 10).

An icon button on the plot's ribbon with a menu:

* **Save figure...**: the plot as drawn, PNG (twice the screen resolution) or SVG (vector, in named Inkscape layers: ``svg_layers.py``).
* **Copy figure**: the same picture to the clipboard (paste into a report or a slide).
* **Save data...**: the numbers behind the plot (``analysis/export_tables.py``: export what is drawn) as CSV, with a JSON record beside it
  (``storage/plot_export.py``: format, app version, time, dataset, session, settings, notes such as out-of-date cubes).

The save dialog opens in the session's ``exports`` folder (made on first use) when the app gives one (``set_context``), and a file is only
replaced when the dialog has asked. Out-of-date numbers are written and marked, never hidden, and the message after the export says so. A failed
export says why (never silent). ``save_figure`` / ``copy_figure`` / ``save_data`` do the work without a dialog (tests, scripts).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMenu, QMessageBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..analysis.export_tables import Table
from ..applog import log_event
from ..storage.plot_export import write_plot_export
from .icon_button_style import style_general_icon_button
from .ribbon_icon_size import ICON_SIZE
from .svg_layers import SvgPart, write_layered_svg

logger = logging.getLogger(__name__)
_RENDER_SIZE = ICON_SIZE * 2
FIGURE_SCALE = 2  # a saved PNG has this many pixels per screen pixel


@dataclass
class ExportData:
    """What "Save data" writes for one plot: the tables (the first is the main CSV; the others go beside it as ``<name>_<key>.csv``) and the
    plot's own part of the record (its settings)."""

    tables: dict[str, Table]
    info: dict[str, object] = field(default_factory=dict)


ContextProvider = Callable[[], tuple[Path | None, Mapping[str, object]]]  # (default folder or None, record entries: dataset, session, ...)


class PlotExportButton(QToolButton):
    def __init__(
        self,
        name: str,
        plot_item: Callable[[], pg.PlotItem],
        data: Callable[[], ExportData | None] | None,
        parent: QWidget | None = None,
        style: Callable[[QToolButton], None] | None = None,
        svg_parts: Callable[[], list[SvgPart]] | None = None,
    ) -> None:
        """``name``: the start of the suggested file name and the record's ``plot`` (e.g. "sensorgram"). ``plot_item``: the plot to draw.
        ``data``: the tables of what is drawn now (None: nothing to export); None for a plot with figures only. ``style``: how the button looks
        (default: the ribbons' icon button; the Histogram passes its corner-icon style). ``svg_parts``: the plot's names for its items, for the
        layers of an SVG (``svg_layers.py``; without it every item is named by its kind)."""
        super().__init__(parent)
        self._svg_parts = svg_parts
        self._style = style or style_general_icon_button
        self._name = name
        self._plot_item = plot_item
        self._data = data
        self._context: ContextProvider | None = None
        self._remember: Callable[[Path], None] | None = None
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip("Export this plot: save or copy the figure, save the numbers behind it.")
        menu = QMenu(self)
        self.save_figure_action = QAction("Save figure (PNG or SVG)...", menu)
        self.copy_figure_action = QAction("Copy figure", menu)
        self.save_data_action = QAction("Save data (CSV)...", menu)
        self.save_figure_action.triggered.connect(self._ask_save_figure)
        self.copy_figure_action.triggered.connect(self._copy_with_message)
        self.save_data_action.triggered.connect(self._ask_save_data)
        menu.addAction(self.save_figure_action)
        menu.addAction(self.copy_figure_action)
        if data is not None:
            menu.addSeparator()
            menu.addAction(self.save_data_action)
        self.setMenu(menu)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self._style(self)
        # A button with a menu draws an arrow in its corner; on an icon-sized button it covers the icon (the other ribbon pick-ups have none).
        self.setStyleSheet(self.styleSheet() + " QToolButton::menu-indicator { image: none; width: 0px; }")
        if self._style is style_general_icon_button:
            self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setIcon(load_tabler_icon("file-export", color=get_active_theme().text_primary, size=_RENDER_SIZE, stroke_width=2.0))

    def set_context(self, provider: ContextProvider | None, remember: Callable[[Path], None] | None = None) -> None:
        """Where the dialogs open and what the record says about the dataset and session (the app sets it; without it: the home folder).
        ``remember(folder)`` is told the folder of every successful save (the app keeps it, so the next dialog of any plot opens there)."""
        self._context = provider
        self._remember = remember

    def _saved_in(self, path: Path) -> None:
        if self._remember is not None:
            self._remember(Path(path).parent)

    # -- the work (no dialog) ------------------------------------------------------------------------------------------------------

    def save_figure(self, path: Path) -> Path:
        """The plot as drawn: ``.svg`` as SVG, anything else as PNG at ``FIGURE_SCALE`` times the screen resolution."""
        path = Path(path)
        item = self._plot_item()
        if path.suffix.lower() == ".svg":
            write_layered_svg(item, path, self._svg_parts() if self._svg_parts is not None else (), title=path.stem)
        else:
            if path.suffix.lower() != ".png":
                path = path.with_suffix(".png")
            exporter = pg_exporters.ImageExporter(item)
            exporter.parameters()["width"] = int(max(item.sceneBoundingRect().width(), 1.0) * FIGURE_SCALE)
            exporter.export(str(path))
        log_event(logger, "export.figure", logging.INFO, plot=self._name, file=path.name)
        return path

    def copy_figure(self) -> None:
        exporter = pg_exporters.ImageExporter(self._plot_item())
        exporter.export(copy=True)

    def save_data(self, path: Path, *, overwrite: bool = False) -> tuple[list[Path], list[str]]:
        """Write what is drawn. Returns the files written and the notes (e.g. out-of-date cubes). Raises when there is nothing to export or a file
        exists (``overwrite`` False)."""
        data = self._data() if self._data is not None else None
        if data is None or not data.tables:
            raise ValueError("There is nothing drawn to export.")
        info: dict[str, object] = {"plot": self._name}
        if self._context is not None:
            info.update(self._context()[1])
        info.update(data.info)
        written = write_plot_export(Path(path), data.tables, info, overwrite=overwrite)
        notes = [note for table in data.tables.values() for note in table.notes]
        log_event(logger, "export.data", logging.INFO, plot=self._name, files=len(written), notes=len(notes))
        return written, notes

    # -- the dialogs ---------------------------------------------------------------------------------------------------------------

    def _suggested(self, suffix: str) -> str:
        folder = None if self._context is None else self._context()[0]
        if folder is not None:
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
            except OSError:
                folder = None
        folder = Path(folder) if folder is not None else Path.home()
        return str(folder / f"{self._name}_{datetime.now():%Y-%m-%d_%H%M%S}{suffix}")

    def _message(self, text: str, warn: bool = False) -> None:
        window = self.window()
        if warn:
            QMessageBox.warning(self, "Export", text)
        elif isinstance(window, QMainWindow):
            window.statusBar().showMessage(text, 10000)

    def _fail(self, what: str, exc: BaseException) -> None:
        logger.exception("Export failed: %s", what)
        QMessageBox.critical(self, "Export", f"{what} failed:\n{exc}")

    def _ask_save_figure(self) -> None:
        path, chosen = QFileDialog.getSaveFileName(self, "Save figure", self._suggested(".png"), "PNG image (*.png);;SVG drawing (*.svg)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".svg" if "svg" in chosen.lower() else ".png"
        try:
            saved = self.save_figure(Path(path))
        except Exception as exc:  # noqa: BLE001 - shown to the user and logged
            self._fail("Saving the figure", exc)
            return
        self._saved_in(saved)
        self._message(f"Figure saved: {saved}")

    def _copy_with_message(self) -> None:
        try:
            self.copy_figure()
        except Exception as exc:  # noqa: BLE001 - shown to the user and logged
            self._fail("Copying the figure", exc)
            return
        self._message("Figure copied to the clipboard.")

    def _ask_save_data(self) -> None:
        path, _chosen = QFileDialog.getSaveFileName(self, "Save data", self._suggested(".csv"), "CSV table (*.csv)")
        if not path:
            return
        if Path(path).suffix.lower() != ".csv":
            path += ".csv"
        try:
            written, notes = self.save_data(Path(path), overwrite=True)  # the dialog asked before replacing the main file; its companions go with it
        except Exception as exc:  # noqa: BLE001 - shown to the user and logged
            self._fail("Saving the data", exc)
            return
        self._saved_in(written[0])
        text = f"Data saved: {', '.join(p.name for p in written)} in {written[0].parent}"
        if notes:
            self._message(text + "\n\n" + "\n".join(notes), warn=True)
        else:
            self._message(text)
