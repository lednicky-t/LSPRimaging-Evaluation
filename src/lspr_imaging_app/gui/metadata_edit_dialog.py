"""Preview/edit popup for the currently loaded `ImagingAcquisitionMetadata`.

Deliberately simple for now (plain QTableWidget grids, no custom delegates
or validation UI) - a first, functional cut per the maintainer's own framing
("we can solve detailed gui later"). `image_timings` (the actual cube-to-time
links) is shown as a read-only summary, not an editable per-row grid - with
potentially thousands of rows, editing them individually here would be
neither usable nor the right tool for the job; camera/illumination settings
and comment events are small enough (one row per wavelength / per real
comment change) to edit directly.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lspr_core import (
    ImagingAcquisitionMetadata,
    ImagingCommentEvent,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)

_CAMERA_COLUMNS = ("Wavelength (nm)", "Exposure (us)", "Gain", "Binning", "Width (px)", "Height (px)")
_ILLUMINATION_COLUMNS = ("Wavelength (nm)", "Settle time (ms)", "Current", "Spectrum source")
_COMMENT_COLUMNS = ("Time (local)", "Comment")


def _fmt(value: object) -> str:
    return "" if value is None else str(value)


def _parse_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def _parse_int(text: str, default: int) -> int:
    text = text.strip()
    try:
        return int(float(text)) if text else default
    except ValueError:
        return default


def _format_relative_ms(elapsed_ms: int) -> str:
    """H:MM:SS.mmm (or M:SS.mmm under an hour) for a millisecond duration -
    used instead of a calendar date when `metadata.started_at_utc` is None
    (a CSV-only legacy import with no absolute acquisition start time, see
    legacy_metadata.import_legacy_imaging_metadata), where a real date would
    be a misleading fabrication rather than merely imprecise."""
    sign = "-" if elapsed_ms < 0 else ""
    total_ms = abs(int(elapsed_ms))
    hours, remainder_ms = divmod(total_ms, 3_600_000)
    minutes, remainder_ms = divmod(remainder_ms, 60_000)
    seconds, millis = divmod(remainder_ms, 1000)
    if hours:
        return f"{sign}{hours:d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{sign}{minutes:d}:{seconds:02d}.{millis:03d}"


def _parse_relative_ms(text: str) -> int | None:
    """Inverse of `_format_relative_ms`: "[+/-]H:MM:SS.mmm" or "M:SS.mmm" -> ms."""
    text = text.strip().lstrip("+")
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    parts = text.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    if len(parts) != 3:
        return None
    hours_text, minutes_text, seconds_text = parts
    try:
        total_ms = (int(hours_text) * 3600 + int(minutes_text) * 60) * 1000 + round(float(seconds_text) * 1000)
    except ValueError:
        return None
    return -total_ms if negative else total_ms


class MetadataEditDialog(QDialog):
    def __init__(self, metadata: ImagingAcquisitionMetadata, *, parent=None) -> None:
        super().__init__(parent)
        self._original = metadata
        # No metaData.txt-style absolute start date (e.g. a CSV-only legacy
        # import, see legacy_metadata.import_legacy_imaging_metadata) means
        # every acquired_at_unix_ms here is an elapsed-since-start offset,
        # not a real Unix timestamp - _format_time/_parse_time below must
        # not render/parse those as calendar dates.
        self._has_absolute_time = bool(metadata.started_at_utc)
        self.setWindowTitle("Acquisition metadata")
        self.resize(640, 640)

        layout = QVBoxLayout(self)

        identity_group = QGroupBox("Identity", self)
        identity_form = QFormLayout(identity_group)
        identity_form.addRow("Source", QLabel(metadata.source_format, self))
        identity_form.addRow("Started (UTC)", QLabel(metadata.started_at_utc or "-", self))
        self._operator_edit = QLineEdit(metadata.operator or "", self)
        identity_form.addRow("Operator", self._operator_edit)
        self._notes_edit = QPlainTextEdit(metadata.notes or "", self)
        self._notes_edit.setFixedHeight(50)
        identity_form.addRow("Notes", self._notes_edit)
        layout.addWidget(identity_group)

        camera_group = QGroupBox("Camera settings", self)
        camera_layout = QVBoxLayout(camera_group)
        self._camera_table = QTableWidget(len(metadata.wavelengths_nm), len(_CAMERA_COLUMNS), self)
        self._camera_table.setHorizontalHeaderLabels(_CAMERA_COLUMNS)
        for row, wavelength_nm in enumerate(metadata.wavelengths_nm):
            settings = metadata.camera_settings_by_wavelength.get(wavelength_nm)
            self._set_row(
                self._camera_table,
                row,
                (
                    f"{wavelength_nm:g}",
                    _fmt(settings.exposure_us if settings else None),
                    _fmt(settings.gain if settings else None),
                    _fmt(settings.binning if settings else None),
                    _fmt(settings.resolution_width_px if settings else None),
                    _fmt(settings.resolution_height_px if settings else None),
                ),
                first_column_editable=False,
            )
        camera_layout.addWidget(self._camera_table)
        layout.addWidget(camera_group)

        illumination_group = QGroupBox("Illumination settings", self)
        illumination_layout = QVBoxLayout(illumination_group)
        self._illumination_table = QTableWidget(len(metadata.wavelengths_nm), len(_ILLUMINATION_COLUMNS), self)
        self._illumination_table.setHorizontalHeaderLabels(_ILLUMINATION_COLUMNS)
        for row, wavelength_nm in enumerate(metadata.wavelengths_nm):
            settings = metadata.illumination_settings_by_wavelength.get(wavelength_nm)
            self._set_row(
                self._illumination_table,
                row,
                (
                    f"{wavelength_nm:g}",
                    _fmt(settings.settle_time_ms if settings else None),
                    _fmt(settings.current if settings else None),
                    _fmt(settings.spectrum_source if settings else "default_file"),
                ),
                first_column_editable=False,
            )
        illumination_layout.addWidget(self._illumination_table)
        layout.addWidget(illumination_group)

        comments_group = QGroupBox("Comment events (pump-plan / experiment notes)", self)
        comments_layout = QVBoxLayout(comments_group)
        self._comment_events = sorted(metadata.comment_events, key=lambda event: event.acquired_at_unix_ms)
        self._comments_table = QTableWidget(len(self._comment_events), len(_COMMENT_COLUMNS), self)
        self._comments_table.setHorizontalHeaderLabels(_COMMENT_COLUMNS)
        for row, event in enumerate(self._comment_events):
            self._set_row(
                self._comments_table,
                row,
                (self._format_time(event.acquired_at_unix_ms), event.comment),
                first_column_editable=True,
            )
        comments_layout.addWidget(self._comments_table)
        comment_row_buttons = QHBoxLayout()
        add_comment_button = QPushButton("Add row", self)
        add_comment_button.clicked.connect(self._add_comment_row)
        remove_comment_button = QPushButton("Remove selected row", self)
        remove_comment_button.clicked.connect(self._remove_selected_comment_row)
        comment_row_buttons.addWidget(add_comment_button)
        comment_row_buttons.addWidget(remove_comment_button)
        comment_row_buttons.addStretch(1)
        comments_layout.addLayout(comment_row_buttons)
        layout.addWidget(comments_group)

        timing_summary = self._format_timing_summary(metadata)
        layout.addWidget(QLabel(timing_summary, self))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _set_row(table: QTableWidget, row: int, values: tuple, *, first_column_editable: bool) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 0 and not first_column_editable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)

    def _format_time(self, acquired_at_unix_ms: int) -> str:
        if not self._has_absolute_time:
            return f"+{_format_relative_ms(acquired_at_unix_ms)}"
        return datetime.fromtimestamp(acquired_at_unix_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _parse_time(self, text: str) -> int | None:
        if not self._has_absolute_time:
            return _parse_relative_ms(text)
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return int(datetime.strptime(text.strip(), fmt).timestamp() * 1000)
            except ValueError:
                continue
        return None

    @staticmethod
    def _format_timing_summary(metadata: ImagingAcquisitionMetadata) -> str:
        if not metadata.image_timings:
            return "No per-image timing data loaded (image timings are read-only here; re-import to change them)."
        timestamps = sorted(t.acquired_at_unix_ms for t in metadata.image_timings)
        if not metadata.started_at_utc:
            first = _format_relative_ms(timestamps[0])
            last = _format_relative_ms(timestamps[-1])
            return (
                f"{len(metadata.image_timings)} timed images, +{first} → +{last} since acquisition start "
                "(no absolute start date loaded; read-only here, re-import to change)."
            )
        first = datetime.fromtimestamp(timestamps[0] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        last = datetime.fromtimestamp(timestamps[-1] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        return f"{len(metadata.image_timings)} timed images, {first} → {last} (read-only here; re-import to change)."

    def _add_comment_row(self) -> None:
        # "now" only makes sense as a default when acquired_at_unix_ms values
        # are real timestamps; for a relative-only import (no absolute start
        # date) there's no meaningful "now" offset, so default to the start.
        default_ms = int(datetime.now().timestamp() * 1000) if self._has_absolute_time else 0
        row = self._comments_table.rowCount()
        self._comments_table.insertRow(row)
        self._set_row(self._comments_table, row, (self._format_time(default_ms), ""), first_column_editable=True)

    def _remove_selected_comment_row(self) -> None:
        rows = sorted({index.row() for index in self._comments_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self._comments_table.removeRow(row)

    def _table_text(self, table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text() if item is not None else ""

    def result_metadata(self) -> ImagingAcquisitionMetadata:
        """Build an updated `ImagingAcquisitionMetadata` from the dialog's
        current field values. Only called after the dialog is accepted."""
        # Built via model_copy(update=...) from the *original* settings object,
        # not constructed fresh - the table only shows/edits a subset of each
        # model's fields (e.g. crop_x_px/crop_y_px/saving_mode aren't in the
        # camera grid), so building fresh objects here would silently drop
        # whatever fields aren't shown, even when the user changed nothing.
        camera_settings_by_wavelength: dict[float, WavelengthCameraSettings] = {}
        for row, wavelength_nm in enumerate(self._original.wavelengths_nm):
            base = self._original.camera_settings_by_wavelength.get(wavelength_nm) or WavelengthCameraSettings(exposure_us=0.0)
            camera_settings_by_wavelength[wavelength_nm] = base.model_copy(
                update={
                    "exposure_us": _parse_float(self._table_text(self._camera_table, row, 1)) or 0.0,
                    "gain": _parse_float(self._table_text(self._camera_table, row, 2)),
                    "binning": _parse_int(self._table_text(self._camera_table, row, 3), 1),
                    "resolution_width_px": _parse_int(self._table_text(self._camera_table, row, 4), 0) or None,
                    "resolution_height_px": _parse_int(self._table_text(self._camera_table, row, 5), 0) or None,
                }
            )

        illumination_settings_by_wavelength: dict[float, WavelengthIlluminationSettings] = {}
        for row, wavelength_nm in enumerate(self._original.wavelengths_nm):
            base = self._original.illumination_settings_by_wavelength.get(wavelength_nm) or WavelengthIlluminationSettings()
            illumination_settings_by_wavelength[wavelength_nm] = base.model_copy(
                update={
                    "settle_time_ms": _parse_float(self._table_text(self._illumination_table, row, 1)),
                    "current": _parse_float(self._table_text(self._illumination_table, row, 2)),
                    "spectrum_source": self._table_text(self._illumination_table, row, 3) or "default_file",
                }
            )

        comment_events: list[ImagingCommentEvent] = []
        for row in range(self._comments_table.rowCount()):
            timestamp_ms = self._parse_time(self._table_text(self._comments_table, row, 0))
            if timestamp_ms is None:
                continue
            comment_events.append(
                ImagingCommentEvent(acquired_at_unix_ms=timestamp_ms, comment=self._table_text(self._comments_table, row, 1))
            )
        comment_events.sort(key=lambda event: event.acquired_at_unix_ms)

        return self._original.model_copy(
            update={
                "operator": self._operator_edit.text().strip() or None,
                "notes": self._notes_edit.toPlainText().strip() or None,
                "camera_settings_by_wavelength": camera_settings_by_wavelength,
                "illumination_settings_by_wavelength": illumination_settings_by_wavelength,
                "comment_events": comment_events,
            }
        )
