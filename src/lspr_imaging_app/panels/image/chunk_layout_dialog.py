"""Find best: the chunk-layout suggestions popup of the Export section (2026-10-20).

Screen purpose: pick a chunk layout that suits the ROI layout, before exporting.
Main actions: read the table, select a row, choose lossless or lossy, Apply (sets the Export section's chunk width,
height and grid position) or Close.
Layout: one status line; one table row per suggestion (`dataset.chunk_plan.suggest_layouts`), recommended first and
marked with a star: layout name, chunk W x H, chunks read per ROI / per group / per plane, size on disk lossless and
lossy (with the share of the image a lossy export drops), notes (warnings); below it the Lossless / Lossy choice and
Apply / Close.
States: computing (status line, Apply off), ready (recommended row selected), failed (message in the status line),
no lattice (only square suggestions; the status line says so).

The suggestions and the size estimate (compressing tiles of a few real planes with the export's own codec) run on an
`AnalysisWorker` - a plain thread, because loading planes touches the dataset/zarr layer (CLAUDE.md: never a
QThreadPool there). The dialog only displays results; it computes nothing on the GUI thread.

Chunk counts are exact. Sizes are estimates (compression sampled on a few planes: within ~2 % on the 2026-10-19
check). There are no read-time predictions on purpose: a fitted time model was 2-3x off
(`docs/zarr_chunk_planner_2026-10-19.md`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme

from ...analysis.worker import AnalysisWorker
from ...dataset import chunk_plan as cp
from ...progress import Cancelled
from .export_roi_layout import ExportRoiLayout

logger = logging.getLogger(__name__)

_JOIN_SECONDS = 2.0
COLUMNS = ("", "Layout", "Chunk W x H", "Chunks / ROI", "Chunks / group", "Chunks / plane", "Lossless", "Lossy", "Notes")
_HEADER_TIPS = (
    "",
    "",
    "Chunk width x height in pixels.",
    "How many chunks reading one ROI touches (mean over ROIs). Not a time: fewer and smaller chunks = faster.",
    "How many chunks reading one ROI group touches (ROI-table groups, or array columns when there are none).",
    "How many chunks one whole image has. Matters for whole-image reads: many small chunks make them slow.",
    "Estimated size on disk, every pixel kept (the edge is padded).",
    "Estimated size on disk, only the part around the ROIs kept; how much of the image is dropped.",
    "",
)


def _gb(n_bytes: int) -> str:
    if n_bytes >= 1e8:
        return f"{n_bytes / 1e9:.1f} GB"
    return f"{n_bytes / 1e6:.0f} MB" if n_bytes >= 1e6 else f"{n_bytes / 1e3:.0f} kB"


class ChunkLayoutDialog(QDialog):
    """Non-modal; emits `layout_chosen(suggestion, lossless)` on Apply."""

    layout_chosen = pyqtSignal(object, bool)
    _computed = pyqtSignal(object)  # list[(Suggestion, ratio | None)] or an Exception; emitted from the worker thread

    def __init__(
        self,
        roi_layout: ExportRoiLayout,
        load_sample_planes: Callable[[], list[np.ndarray]],
        *,
        planes: int,
        bytes_per_pixel: int,
        compression: bool,
        lossless: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find best chunk layout")
        self._planes = int(planes)
        self._compression = bool(compression)
        self._rows: list[tuple[cp.Suggestion, float | None]] = []

        self._status = QLabel("Computing suggestions...", self)
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, len(COLUMNS), self)
        self._table.setHorizontalHeaderLabels(COLUMNS)
        for col, tip in enumerate(_HEADER_TIPS):
            if tip:
                self._table.horizontalHeaderItem(col).setToolTip(tip)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._apply())

        self._lossless_radio = QRadioButton("Lossless (pad the edge, keep every pixel)", self)
        self._lossy_radio = QRadioButton("Lossy (keep only the part around the ROIs)", self)
        (self._lossless_radio if lossless else self._lossy_radio).setChecked(True)
        hint = QLabel("Counts are chunks touched per read (exact), not times: fewer and smaller chunks read faster. "
                      "Sizes are estimates. A group is a ROI-table group, or an array column when there are none.", self)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {get_active_theme().text_muted}; font-size: 11px;")

        self._apply_button = QPushButton("Apply", self)
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(self._lossless_radio)
        buttons.addWidget(self._lossy_radio)
        buttons.addStretch(1)
        buttons.addWidget(self._apply_button)
        buttons.addWidget(close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._table, 1)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        self.resize(900, 320)

        self._worker = AnalysisWorker()
        self._computed.connect(self._on_computed)
        cancel_event = self._worker.cancel_event

        def task() -> None:
            try:
                suggestions = cp.suggest_layouts(
                    roi_layout.boxes, roi_layout.raw_shape, centers_xy=roi_layout.centers,
                    groups=[list(g) for g in roi_layout.groups] or None,
                    bytes_per_pixel=bytes_per_pixel, compression=compression,
                )
                sample = load_sample_planes() if compression and suggestions else []
                rows = []
                for s in suggestions:
                    if cancel_event.is_set():
                        raise Cancelled()
                    ratio = cp.estimate_compression_ratio(sample, s.layout.chunk_w, s.layout.chunk_h) if sample else None
                    rows.append((s, ratio))
                self._computed.emit(rows)
            except Cancelled:
                return
            except Exception as exc:  # noqa: BLE001 - shown in the dialog
                logger.exception("Chunk layout suggestions failed")
                self._computed.emit(exc)

        self._worker.submit(task)

    # -- results ----------------------------------------------------------------

    def _on_computed(self, result: object) -> None:
        if isinstance(result, Exception):
            self._status.setText(f"Could not compute suggestions: {result}")
            return
        self._rows = list(result)
        if not self._rows:
            self._status.setText("No suggestions: place ROIs first - the suggestions are built from the ROI layout.")
            return
        keys = {s.key for s, _r in self._rows}
        self._status.setText(
            "Suggested layouts for this ROI layout (grid aligned to the ROIs)."
            if "columns" in keys else
            "No regular ROI array found: only general-purpose sizes are suggested (grid aligned to the ROIs)."
        )
        self._table.setRowCount(len(self._rows))
        for row, (s, ratio) in enumerate(self._rows):
            single = s.scores.get("single")
            group = s.scores.get("group")
            lossy_drop = 100.0 * s.lossy.discarded_px / max(s.lossless.kept_px, 1)
            cells = (
                "★" if s.recommended else "",
                s.name,
                f"{s.layout.chunk_w} x {s.layout.chunk_h}",
                f"{single.chunks:.2f}" if single else "-",
                f"{group.chunks:.1f}" if group else "-",
                f"{s.lossless.chunks_per_plane}",
                _gb(s.lossless.dataset_bytes(self._planes, ratio)),
                f"{_gb(s.lossy.dataset_bytes(self._planes, ratio))} (-{lossy_drop:.0f}% px)",
                " ".join(s.warnings) if s.warnings else "",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(s.description if col <= 1 else (text if col == len(cells) - 1 else ""))
                if col in (0, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)
        self._table.selectRow(next((i for i, (s, _r) in enumerate(self._rows) if s.recommended), 0))
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self._apply_button.setEnabled(bool(self._rows) and bool(self._table.selectedItems()))

    def _apply(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows or not self._rows:
            return
        suggestion, _ratio = self._rows[rows[0].row()]
        self.layout_chosen.emit(suggestion, self._lossless_radio.isChecked())
        self.close()

    # -- lifetime ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._worker.cancel()
        self._worker.join(_JOIN_SECONDS)
        super().closeEvent(event)
