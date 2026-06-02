from __future__ import annotations

import math

import pandas as pd
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from .models import LoadedLog
from .parser import relative_seconds
from .plotting import COLORS


class TelemetryPlotWidget(QWidget):
    index_selected = pyqtSignal(int)
    index_stepped = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.compare: LoadedLog | None = None
        self.columns: list[str] = []
        self.selected_index = 0
        self.show_grid = True
        self.dark = True
        self.setMinimumHeight(420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_plot(
        self,
        log: LoadedLog | None,
        columns: list[str],
        compare: LoadedLog | None = None,
        selected_index: int = 0,
        show_grid: bool = True,
        dark: bool = True,
    ) -> None:
        self.log = log
        self.compare = compare
        self.columns = columns
        self.selected_index = selected_index
        self.show_grid = show_grid
        self.dark = dark
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Arial", 9))
        bg = QColor("#171a20" if self.dark else "#ffffff")
        fg = QColor("#e5e7eb" if self.dark else "#111827")
        muted = QColor("#8b95a5" if self.dark else "#6b7280")
        grid = QColor(255, 255, 255, 28) if self.dark else QColor(0, 0, 0, 24)
        painter.fillRect(self.rect(), bg)

        plot = QRectF(58, 34, max(10, self.width() - 92), max(10, self.height() - 84))
        painter.setPen(QPen(muted, 1))
        painter.drawRect(plot)

        if self.log is None or not self.columns:
            painter.setPen(fg)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open a log and select telemetry parameters.")
            painter.end()
            return

        x_values = relative_seconds(self.log)
        if not x_values:
            painter.setPen(fg)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "The loaded log has no samples.")
            painter.end()
            return

        x_min = min(x_values)
        x_max = max(x_values)
        if math.isclose(x_min, x_max):
            x_max = x_min + 1.0

        if self.show_grid:
            painter.setPen(QPen(grid, 1))
            for i in range(1, 5):
                x = plot.left() + plot.width() * i / 5
                y = plot.top() + plot.height() * i / 5
                painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        legend_x = plot.left()
        legend_y = 16
        fm = QFontMetrics(painter.font())
        for idx, column in enumerate(self.columns):
            color = QColor(COLORS[idx % len(COLORS)])
            painter.setPen(QPen(color, 2))
            painter.drawLine(QPointF(legend_x, legend_y), QPointF(legend_x + 18, legend_y))
            painter.setPen(fg)
            painter.drawText(QPointF(legend_x + 24, legend_y + 4), column)
            legend_x += 34 + fm.horizontalAdvance(column)
            if legend_x > self.width() - 160:
                legend_x = plot.left()
                legend_y += 18

        for idx, column in enumerate(self.columns):
            if column not in self.log.dataframe.columns:
                continue
            color = QColor(COLORS[idx % len(COLORS)])
            self._draw_series(painter, plot, x_values, self.log.dataframe[column], x_min, x_max, color)
            if self.compare is not None and column in self.compare.dataframe.columns:
                self._draw_series(
                    painter,
                    plot,
                    relative_seconds(self.compare),
                    self.compare.dataframe[column],
                    x_min,
                    x_max,
                    color,
                    dashed=True,
                )

        cursor_index = max(0, min(self.selected_index, len(x_values) - 1))
        cursor_x = self._map_x(x_values[cursor_index], plot, x_min, x_max)
        painter.setPen(QPen(QColor("#ffffff" if self.dark else "#111827"), 2))
        painter.drawLine(QPointF(cursor_x, plot.top()), QPointF(cursor_x, plot.bottom()))

        painter.setPen(muted)
        painter.drawText(QPointF(plot.left(), self.height() - 18), f"{x_min:.1f}s")
        painter.drawText(QPointF(plot.right() - 70, self.height() - 18), f"{x_max:.1f}s")
        painter.end()

    def _draw_series(
        self,
        painter: QPainter,
        plot: QRectF,
        x_values: list[float],
        values,
        x_min: float,
        x_max: float,
        color: QColor,
        dashed: bool = False,
    ) -> None:
        series = pd.to_numeric(values, errors="coerce")
        valid = [(x, float(y)) for x, y in zip(x_values, series) if not pd.isna(y)]
        if len(valid) < 2:
            return
        y_values = [point[1] for point in valid]
        y_min = min(y_values)
        y_max = max(y_values)
        if math.isclose(y_min, y_max):
            y_min -= 1.0
            y_max += 1.0

        path = QPainterPath()
        first = True
        step = max(1, len(valid) // max(1, int(plot.width())))
        for x, y in valid[::step]:
            point = QPointF(self._map_x(x, plot, x_min, x_max), self._map_y(y, plot, y_min, y_max))
            if first:
                path.moveTo(point)
                first = False
            else:
                path.lineTo(point)

        pen = QPen(color, 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)

    @staticmethod
    def _map_x(value: float, plot: QRectF, x_min: float, x_max: float) -> float:
        return plot.left() + ((value - x_min) / (x_max - x_min)) * plot.width()

    @staticmethod
    def _map_y(value: float, plot: QRectF, y_min: float, y_max: float) -> float:
        return plot.bottom() - ((value - y_min) / (y_max - y_min)) * plot.height()

    def mousePressEvent(self, event) -> None:
        if self.log is None:
            return
        self.setFocus()
        plot_left = 58.0
        plot_width = max(10.0, self.width() - 92.0)
        ratio = max(0.0, min(1.0, (event.position().x() - plot_left) / plot_width))
        index = round(ratio * max(0, len(self.log.dataframe) - 1))
        self.index_selected.emit(index)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.index_stepped.emit(-1)
        elif event.key() == Qt.Key.Key_Right:
            self.index_stepped.emit(1)
        else:
            super().keyPressEvent(event)


class GpsPathWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.color_column: str | None = None
        self.dark = True
        self.setMinimumHeight(420)

    def set_path(self, log: LoadedLog | None, color_column: str | None = None, dark: bool = True) -> None:
        self.log = log
        self.color_column = color_column
        self.dark = dark
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Arial", 9))
        bg = QColor("#171a20" if self.dark else "#ffffff")
        fg = QColor("#e5e7eb" if self.dark else "#111827")
        grid = QColor(255, 255, 255, 28) if self.dark else QColor(0, 0, 0, 24)
        painter.fillRect(self.rect(), bg)

        plot = QRectF(58, 34, max(10, self.width() - 92), max(10, self.height() - 84))
        painter.setPen(QPen(grid, 1))
        painter.drawRect(plot)

        if self.log is None or self.log.gps_columns is None:
            painter.setPen(fg)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No GPS latitude/longitude columns were detected in this log.",
            )
            painter.end()
            return

        gps = self.log.gps_columns
        lat = pd.to_numeric(self.log.dataframe[gps.latitude], errors="coerce")
        lon = pd.to_numeric(self.log.dataframe[gps.longitude], errors="coerce")
        points = [(float(x), float(y)) for x, y in zip(lon, lat) if not pd.isna(x) and not pd.isna(y)]
        if len(points) < 2:
            painter.setPen(fg)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "GPS columns exist, but there are not enough valid points.")
            painter.end()
            return

        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        if math.isclose(x_min, x_max):
            x_min -= 0.001
            x_max += 0.001
        if math.isclose(y_min, y_max):
            y_min -= 0.001
            y_max += 0.001

        path = QPainterPath()
        for idx, (x, y) in enumerate(points):
            point = QPointF(
                plot.left() + ((x - x_min) / (x_max - x_min)) * plot.width(),
                plot.bottom() - ((y - y_min) / (y_max - y_min)) * plot.height(),
            )
            if idx == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor("#48c774"), 2))
        painter.drawPath(path)
        painter.setPen(fg)
        painter.drawText(QPointF(plot.left(), self.height() - 18), gps.longitude)
        painter.drawText(QPointF(plot.left() + 120, self.height() - 18), gps.latitude)
        painter.end()
