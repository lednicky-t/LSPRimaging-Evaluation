"""The info row at the right end of the menu bar (2026-10-22): which dataset is open and what it carries.

Layout (one line, in the menu bar, right of File / Edit / View / Help):

    C:\\Data\\2026-10-20\\chip3\\...\\run_07   OME-Zarr · 12.3 GB · C/WL: 120/8   ✓ Time   ✓ Plan

- **Path**: the folder the dataset was opened from, shortened in the middle with an ellipsis when the window is narrow
  (the full path is the tooltip).
- **Stats**: the same type / size / cubes-per-wavelengths text as the Dataset section's title row.
- **Time** (green ✓ / grey ✗ / amber ✓ when only some images are timed): the images have acquisition times, which is
  what makes Time mode for cubes work.
- **Plan** (green ✓ / grey ✗): the acquisition metadata holds comment events, the pump-plan steps shown in the
  Experimental plan section.

Display only: everything comes from `DatasetModule`, which also re-emits `dataset_loaded` after a metadata import, so
the checks follow an import. Placed by `attach_to_menu_bar`, which keeps the row as wide as the free space right of the
menus so the path can use all of it.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenuBar, QSizePolicy, QWidget

from lspr_ui import get_active_theme

from ..dataset import DatasetModule
from ..dataset.model import ImageDataset
from .workflow.dataset_summary import _format_bytes

_GAP_PX = 12
_RIGHT_MARGIN_PX = 8


class _PathLabel(QLabel):
    """A label that shortens its text in the middle to the width it is given (and may shrink to nothing)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._elide()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width(), 0))
        if shown != self.text():
            self.setText(shown)


class DatasetInfoBar(QWidget):
    def __init__(self, dataset: DatasetModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._loaded: ImageDataset | None = None

        self._path_label = _PathLabel(self)
        self._stats_label = QLabel(self)
        self._time_label = QLabel(self)
        self._plan_label = QLabel(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, _RIGHT_MARGIN_PX, 0)
        layout.setSpacing(_GAP_PX)
        layout.addWidget(self._path_label, 1)
        for label in (self._stats_label, self._time_label, self._plan_label):
            layout.addWidget(label, 0)

        dataset.dataset_loaded.connect(self._on_loaded)  # also fires after a metadata import
        dataset.dataset_cleared.connect(self._on_cleared)
        self.refresh()

    def _on_loaded(self, loaded: ImageDataset) -> None:
        self._loaded = loaded
        self.refresh()

    def _on_cleared(self) -> None:
        self._loaded = None
        self.refresh()

    def refresh(self) -> None:
        theme = get_active_theme()
        loaded = self._loaded
        for label in (self._path_label, self._stats_label, self._time_label, self._plan_label):
            label.setStyleSheet(f"color: {theme.text_dim};")
        if loaded is None:
            self._path_label.set_full_text("No dataset loaded")
            self._path_label.setToolTip("")
            for label in (self._stats_label, self._time_label, self._plan_label):
                label.setVisible(False)
            return

        self._path_label.set_full_text(str(loaded.home))
        data_folder = f"\nData: {loaded.folder}" if loaded.folder != loaded.home else ""
        self._path_label.setToolTip(f"{loaded.home}{data_folder}")
        self._stats_label.setText(self._stats_text(loaded))
        self._stats_label.setToolTip("Type · size · spectral cubes / wavelengths.")

        total = len(loaded.records)
        timed = self._dataset.timed_image_count()
        if timed <= 0:
            self._set_check(self._time_label, "Time", None, "No acquisition times: Time mode and the time axis are unavailable.", theme)
        elif timed < total:
            self._set_check(
                self._time_label, "Time", "partial",
                f"Only {timed} of {total} images have an acquisition time - the metadata import may be incomplete.", theme,
            )
        else:
            self._set_check(self._time_label, "Time", "all", f"Acquisition times recorded for all {total} images.", theme)

        comments = self._dataset.comment_event_count()
        if comments > 0:
            self._set_check(self._plan_label, "Plan", "all", f"Experimental plan loaded: {comments} comment event(s).", theme)
        else:
            self._set_check(self._plan_label, "Plan", None, "No experimental plan (comment events) loaded.", theme)
        for label in (self._stats_label, self._time_label, self._plan_label):
            label.setVisible(True)

    def _stats_text(self, loaded: ImageDataset) -> str:
        stats = self._dataset.dataset_file_stats(loaded)
        size_text = _format_bytes(stats[0] if stats is not None else 0)
        type_text = "OME-Zarr" if loaded.is_ome_zarr else "TIFF Stack"
        return f"{type_text} · {size_text} · C/WL: {len(loaded.spectral_cube_indices)}/{len(loaded.wavelengths_nm)}"

    @staticmethod
    def _set_check(label: QLabel, name: str, state: str | None, tooltip: str, theme) -> None:
        """state: 'all' = green check, 'partial' = amber check, None = grey cross."""
        color = {"all": theme.accent_green, "partial": theme.accent_gold}.get(state, theme.text_dim)
        mark = "\u2713" if state else "\u2717"
        label.setText(f'<span style="color:{color}">{mark}</span> <span style="color:{theme.text_dim}">{name}</span>')
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setToolTip(tooltip)


class _MenuBarFiller(QObject):
    """Places the info row in the free space right of the menus and keeps it there on every resize.

    Not a Qt "corner widget": the menu bar puts those at the right edge using the widget's size hint *at that moment*
    and does not move them when the width changes afterwards, so the row ended up half outside the window."""

    def __init__(self, menu_bar: QMenuBar, bar: DatasetInfoBar) -> None:
        super().__init__(bar)
        self._menu_bar = menu_bar
        self._bar = bar
        menu_bar.installEventFilter(self)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if watched is self._menu_bar and event is not None and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False

    def place(self) -> None:
        actions = self._menu_bar.actions()
        used = self._menu_bar.actionGeometry(actions[-1]).right() + 1 if actions else 0
        left = used + _GAP_PX
        width = max(self._menu_bar.width() - left, 0)
        self._bar.setGeometry(left, 0, width, self._menu_bar.height())
        self._bar.raise_()


def attach_to_menu_bar(menu_bar: QMenuBar, dataset: DatasetModule) -> DatasetInfoBar:
    """Put the info row into the free space right of the menus (call after the menus exist)."""
    bar = DatasetInfoBar(dataset, menu_bar)
    _MenuBarFiller(menu_bar, bar).place()
    return bar
