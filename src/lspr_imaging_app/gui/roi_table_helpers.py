from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem


@dataclass(slots=True)
class RoiTableRowColors:
    sample: QColor
    reference: QColor


@dataclass(slots=True)
class RoiTableRowData:
    area_roi_id: int
    group_name: str
    sample_color: QColor
    reference_color: QColor
    sample_diameter_text: str
    reference_inner_text: str
    reference_outer_text: str
    x_text: str
    y_text: str


def roi_table_headers(table: QTableWidget) -> None:
    if table.columnCount() < 9:
        return
    table.setHorizontalHeaderItem(2, QTableWidgetItem("C_s"))
    table.setHorizontalHeaderItem(3, QTableWidgetItem("C_r"))
    table.setHorizontalHeaderItem(4, QTableWidgetItem("D_s"))
    table.setHorizontalHeaderItem(5, QTableWidgetItem("d_r"))
    table.setHorizontalHeaderItem(6, QTableWidgetItem("D_r"))
    table.setHorizontalHeaderItem(7, QTableWidgetItem("x"))
    table.setHorizontalHeaderItem(8, QTableWidgetItem("y"))


def make_color_swatch_icon(color: QColor, size: int = 16) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(color)
        painter.setPen(QColor("#111827"))
        painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    finally:
        painter.end()
    return QIcon(pixmap)


def format_xy_value(value: float, display_units: str, scale: float | None = None) -> str:
    if display_units == "um" and scale is not None:
        return f"{value * scale:.1f}"
    return f"{value:.1f}"


def append_roi_table_row(table: QTableWidget, row: RoiTableRowData) -> None:
    index = table.rowCount()
    table.insertRow(index)
    table.setRowHeight(index, 18)

    id_item = QTableWidgetItem(str(row.area_roi_id))
    id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 0, id_item)

    group_item = QTableWidgetItem(row.group_name)
    group_item.setFlags(group_item.flags() | Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 1, group_item)

    sample_item = QTableWidgetItem("")
    sample_item.setFlags(sample_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    sample_item.setIcon(make_color_swatch_icon(row.sample_color))
    table.setItem(index, 2, sample_item)

    reference_item = QTableWidgetItem("")
    reference_item.setFlags(reference_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    reference_item.setIcon(make_color_swatch_icon(row.reference_color))
    table.setItem(index, 3, reference_item)

    sample_diameter_item = QTableWidgetItem(row.sample_diameter_text)
    sample_diameter_item.setFlags(sample_diameter_item.flags() | Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 4, sample_diameter_item)

    reference_inner_item = QTableWidgetItem(row.reference_inner_text)
    reference_inner_item.setFlags(reference_inner_item.flags() | Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 5, reference_inner_item)

    reference_outer_item = QTableWidgetItem(row.reference_outer_text)
    reference_outer_item.setFlags(reference_outer_item.flags() | Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 6, reference_outer_item)

    x_item = QTableWidgetItem(row.x_text)
    x_item.setFlags(x_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 7, x_item)

    y_item = QTableWidgetItem(row.y_text)
    y_item.setFlags(y_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    table.setItem(index, 8, y_item)
