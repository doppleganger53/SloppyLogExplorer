from __future__ import annotations

import html
import json
import math
import re
import time
import traceback
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget
)

from . import __version__
from .analysis import (
    basic_stats,
    calculate_internal_resistance,
    cursor_values,
    find_current_columns,
    find_voltage_columns,
    suggest_display_columns,
)
from .library import group_by_model, scan_library
from .models import GpsGradientOptions, LibraryLogInfo, LoadedLog, SyncCandidate
from .parser import InvalidTelemetryLogError, load_log, relative_seconds
from .qt_plot import GpsPathWidget, TelemetryPlotWidget
from .storage import AppStore
from .sync import copy_candidates, discover_sync_candidates
from .voice import VoiceItem, generate_voice_pack, load_voice_csv, save_voice_csv


def _about_text() -> str:
    return (
        f"Sloppy Log Explorer {__version__}\n\n"
        "GPL-3.0-or-later telemetry log explorer for Ethos and OpenTX CSV logs.\n"
        "Derived from Ethos_LogView concepts with attribution in NOTICE.md."
    )


class RawLogTableModel(QAbstractTableModel):
    def __init__(self, dataframe: pd.DataFrame | None = None) -> None:
        super().__init__()
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame()

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._dataframe

    def set_dataframe(self, dataframe: pd.DataFrame | None) -> None:
        self.beginResetModel()
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._dataframe)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._dataframe.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        if role != Qt.ItemDataRole.DisplayRole or not index.isValid():
            return None
        row = index.row()
        column = index.column()
        if row < 0 or row >= len(self._dataframe) or column < 0 or column >= len(self._dataframe.columns):
            return None
        value = self._dataframe.iat[row, column]
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        return str(value)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._dataframe.columns):
                return str(self._dataframe.columns[section])
            return None
        if orientation == Qt.Orientation.Vertical:
            if 0 <= section < len(self._dataframe):
                return str(section + 1)
            return None
        return None


class _NumericSortableTableItem(QTableWidgetItem):
    """Table item that sorts numerically by its stored raw value."""

    def __init__(self, text: str, sort_value: float | int) -> None:
        super().__init__(text)
        self._sort_value = sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if not isinstance(other, _NumericSortableTableItem):
            return QTableWidgetItem.__lt__(self, other)
        left = self._sort_value
        right = other._sort_value
        if left == right:
            return QTableWidgetItem.__lt__(self, other)
        return left < right


class MainWindow(QMainWindow):
    library_sort_column = 2
    library_sort_order = Qt.SortOrder.DescendingOrder

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sloppy Log Explorer")
        self.resize(1500, 940)
        self.store = AppStore()
        self.library_root: Path | None = None
        self.library_logs: list[LibraryLogInfo] = []
        self.current_log: LoadedLog | None = None
        self.compare_log: LoadedLog | None = None
        self.selected_index = 0
        self.dark_mode = True
        self.telemetry_interaction_mode = "zoom"
        self.telemetry_time_mode = "absolute"
        self.telemetry_visible_elapsed_range: tuple[float, float] | None = None
        self._telemetry_x_range_generation = 0
        self.selected_parameter_columns: set[str] = set()
        self.telemetry_axis_groups: list[tuple[str, ...]] = []
        self.telemetry_axis_ungrouped_columns: set[str] = set()
        self.statistics_excluded_columns: set[str] = set()
        self._deferred_views_dirty: set[str] = set()
        self._gps_view_loaded = False
        self.gps_start_color = GpsGradientOptions.start_color
        self.gps_end_color = GpsGradientOptions.end_color
        self.gps_playback_playing = False
        self.gps_playback_speed = 1.0
        self.gps_playback_last_tick: float | None = None
        self.gps_playback_elapsed_seconds = 0.0
        self.gps_timeline_seconds: list[float] = []
        self.gps_timeline_is_monotonic = True
        self.gps_marker_value_combos: list[QComboBox] = []
        self.sync_candidates: list[SyncCandidate] = []
        self.voice_items: list[VoiceItem] = []
        self.gps_playback_timer = QTimer(self)
        self.gps_playback_timer.setInterval(100)
        self.gps_playback_timer.timeout.connect(self.gps_playback_tick)

        self._build_actions()
        self._build_ui()
        self._apply_style()
        self._restore_state()

    def _build_actions(self) -> None:
        menu_bar = self.menuBar()
        if menu_bar is None:
            raise RuntimeError("Failed to get menu bar")    
        file_menu =  menu_bar.addMenu("File")
        if file_menu is None:
            raise RuntimeError("Failed to create File menu")
        open_log = QAction("Open Log...", self)
        open_log.triggered.connect(self.open_log_dialog)
        file_menu.addAction(open_log)
        open_library = QAction("Open Log Library...", self)
        open_library.triggered.connect(self.open_library_dialog)
        file_menu.addAction(open_library)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        view_menu = menu_bar.addMenu("View")
        if view_menu is None:
            raise RuntimeError("Failed to create View menu")
        self.grid_action = QAction("Show Grid", self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.triggered.connect(self.refresh_plots)
        view_menu.addAction(self.grid_action)
        self.dark_action = QAction("Dark Theme", self)
        self.dark_action.setCheckable(True)
        self.dark_action.setChecked(True)
        self.dark_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.dark_action)
        view_menu.addSeparator()
        view_menu.addAction("Reset Telemetry View", self.reset_telemetry_view)

        tools_menu = menu_bar.addMenu("Tools")
        if tools_menu is None:
            raise RuntimeError("Failed to create Tools menu")
        tools_menu.addAction("Scan Sync Candidates", self.scan_sync)
        tools_menu.addAction("Generate Voice Pack", self.generate_voice_pack)

        help_menu = menu_bar.addMenu("Help")
        if help_menu is None:
            raise RuntimeError("Failed to create Help menu")
        help_menu.addAction("About", self.about)

    def _build_ui(self) -> None:
        root = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(root)

        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)

        open_row = QHBoxLayout()
        open_file_btn = QPushButton("Open Log")
        open_file_btn.clicked.connect(self.open_log_dialog)
        open_library_btn = QPushButton("Open Library")
        open_library_btn.clicked.connect(self.open_library_dialog)
        open_row.addWidget(open_file_btn)
        open_row.addWidget(open_library_btn)
        sidebar_layout.addLayout(open_row)

        self.file_summary = QLabel("No log loaded")
        self.file_summary.setWordWrap(True)
        self.file_summary.setObjectName("summary")
        sidebar_layout.addWidget(self.file_summary)

        self.library_tree = QTreeWidget()
        self.library_tree.setHeaderLabels(["Model / Log", "Logs", "Latest", "Size"])
        # Configure manual sorting before making the header clickable. Qt's
        # setSortingEnabled(False) resets header clickability when called later.
        self.library_tree.setSortingEnabled(False)
        header = self.library_tree.header()
        if header is None:
            raise RuntimeError("Failed to get library tree header")
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.library_header_clicked)
        self.library_tree.itemActivated.connect(self.library_item_activated)
        sidebar_layout.addWidget(QLabel("Log Library"))
        sidebar_layout.addWidget(self.library_tree, 2)

        self.column_filter = QLineEdit()
        self.column_filter.setPlaceholderText("Filter parameters")
        self.column_filter.textChanged.connect(self.populate_columns)
        sidebar_layout.addWidget(self.column_filter)

        selection_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.setToolTip("Select all parameters currently listed in the table.")
        select_all.clicked.connect(lambda: self.set_visible_columns_checked(True))
        select_none = QPushButton("Select None")
        select_none.setToolTip("Clear all parameters currently listed in the table.")
        select_none.clicked.connect(lambda: self.set_visible_columns_checked(False))
        selection_row.addWidget(select_all)
        selection_row.addWidget(select_none)
        sidebar_layout.addLayout(selection_row)

        self.column_table = QTableWidget(0, 2)
        self.column_table.setHorizontalHeaderLabels(["Show", "Parameter"])
        self._horizontal_header(self.column_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.column_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.column_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.column_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.column_table.customContextMenuRequested.connect(self.show_column_context_menu)
        self._configure_sortable_table(self.column_table)
        self.column_table.itemChanged.connect(self.column_changed)
        sidebar_layout.addWidget(self.column_table, 3)

        compare_row = QHBoxLayout()
        self.compare_toggle = QCheckBox("Compare")
        self.compare_toggle.toggled.connect(self.refresh_plots)
        compare_btn = QPushButton("Load Compare")
        compare_btn.clicked.connect(self.open_compare_dialog)
        compare_row.addWidget(self.compare_toggle)
        compare_row.addWidget(compare_btn)
        sidebar_layout.addLayout(compare_row)

        self.compare_label = QLabel("No compare log")
        self.compare_label.setObjectName("summary")
        self.compare_label.setWordWrap(True)
        sidebar_layout.addWidget(self.compare_label)

        root.addWidget(sidebar)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        root.setSizes([390, 1110])

        self._build_graph_tab()
        self._build_statistics_tab()
        self._build_raw_log_tab()
        self._build_gps_tab()
        self._build_flight_tab()
        self._build_battery_tab()
        self._build_sync_tab()
        self._build_alias_tab()
        self._build_voice_tab()
        self.tabs.currentChanged.connect(self.tab_changed)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

    def _build_graph_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Drag mode"))
        self.telemetry_pan_button = QPushButton("Pan")
        self.telemetry_pan_button.setCheckable(True)
        self.telemetry_pan_button.setChecked(False)
        self.telemetry_pan_button.clicked.connect(lambda: self.set_telemetry_interaction_mode("pan"))
        self.telemetry_zoom_button = QPushButton("Zoom")
        self.telemetry_zoom_button.setCheckable(True)
        self.telemetry_zoom_button.setChecked(True)
        self.telemetry_zoom_button.clicked.connect(lambda: self.set_telemetry_interaction_mode("zoom"))
        reset = QPushButton("Reset View")
        reset.clicked.connect(self.reset_telemetry_view)
        controls.addWidget(self.telemetry_pan_button)
        controls.addWidget(self.telemetry_zoom_button)
        controls.addWidget(reset)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Time"))
        self.telemetry_time_combo = QComboBox()
        self.telemetry_time_combo.addItems(["Absolute", "Relative"])
        self.telemetry_time_combo.currentTextChanged.connect(self.telemetry_time_changed)
        controls.addWidget(self.telemetry_time_combo)
        controls.addStretch()
        layout.addLayout(controls)
        self.graph_view = TelemetryPlotWidget()
        self.graph_view.index_selected.connect(self.set_selected_index)
        self.graph_view.index_stepped.connect(self.step_selected_index)
        # WebChannel invokes bridge slots while Chromium/Qt is still unwinding
        # the JavaScript call. Queue the UI work so even targeted Plotly updates
        # cannot re-enter the active callback and destabilize WebEngine.
        self.graph_view.x_range_changed.connect(self.queue_telemetry_visible_x_range)
        layout.addWidget(self.graph_view, 5)
        self.info_panel = QTextEdit()
        self.info_panel.setReadOnly(True)
        self.info_panel.setMaximumHeight(190)
        layout.addWidget(self.info_panel, 1)
        self.tabs.addTab(tab, "Telemetry")
        self._set_empty_graph()

    def _build_statistics_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        self.statistics_empty_label = QLabel("No log loaded")
        self.statistics_empty_label.setObjectName("summary")
        controls.addWidget(self.statistics_empty_label)
        controls.addStretch()
        self.statistics_reset_button = QPushButton("Show All")
        self.statistics_reset_button.clicked.connect(self.reset_statistics_exclusions)
        controls.addWidget(self.statistics_reset_button)
        layout.addLayout(controls)

        self.statistics_table = QTableWidget(0, 7)
        self.statistics_table.setHorizontalHeaderLabels(
            ["Include", "Parameter", "Samples", "Min", "Max", "Mean", "Std Dev"]
        )
        self._horizontal_header(self.statistics_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.statistics_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._configure_sortable_table(self.statistics_table)
        self.statistics_table.itemChanged.connect(self.statistics_item_changed)
        layout.addWidget(self.statistics_table, 1)
        self.tabs.addTab(tab, "Statistics")
        self.statistics_tab = tab
        self.populate_statistics_table()

    def _set_raw_log_dataframe(self, dataframe: pd.DataFrame | None) -> None:
        visible_dataframe = self._source_facing_raw_columns(dataframe)
        self.raw_log_model.set_dataframe(visible_dataframe)
        has_log = dataframe is not None
        self.raw_log_empty.setVisible(not has_log)
        self.raw_log_table.setVisible(has_log)

    def _source_facing_raw_columns(self, dataframe: pd.DataFrame | None) -> pd.DataFrame | None:
        if dataframe is None:
            return None
        visible_columns = [column for column in dataframe.columns if not str(column).startswith("__")]
        if len(visible_columns) == len(dataframe.columns):
            return dataframe
        return dataframe[visible_columns]

    def _build_raw_log_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.raw_log_empty = QLabel("No log loaded")
        self.raw_log_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.raw_log_empty.setObjectName("summary")
        layout.addWidget(self.raw_log_empty)

        self.raw_log_model = RawLogTableModel()
        self.raw_log_table = QTableView()
        self.raw_log_table.setModel(self.raw_log_model)
        self.raw_log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.raw_log_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.raw_log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_log_table.setAlternatingRowColors(True)
        self.raw_log_table.setSortingEnabled(False)
        header = self.raw_log_table.horizontalHeader()
        if header is None:
            raise RuntimeError("QTableView did not provide a horizontal header")
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        layout.addWidget(self.raw_log_table, 1)
        self.tabs.addTab(tab, "Raw Log")
        self.raw_log_tab = tab
        self._set_raw_log_dataframe(None)

    def _build_gps_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        row.addWidget(QLabel("Color by"))
        self.gps_color_combo = QComboBox()
        self.gps_color_combo.currentTextChanged.connect(self.gps_color_changed)
        row.addWidget(self.gps_color_combo)
        row.addWidget(QLabel("Start"))
        self.gps_start_color_button = QPushButton()
        self.gps_start_color_button.clicked.connect(lambda: self.choose_gps_color("start"))
        row.addWidget(self.gps_start_color_button)
        row.addWidget(QLabel("End"))
        self.gps_end_color_button = QPushButton()
        self.gps_end_color_button.clicked.connect(lambda: self.choose_gps_color("end"))
        row.addWidget(self.gps_end_color_button)
        self.gps_reverse_check = QCheckBox("Reverse")
        self.gps_reverse_check.toggled.connect(self.refresh_gps)
        row.addWidget(self.gps_reverse_check)
        row.addStretch()
        layout.addLayout(row)

        range_row = QHBoxLayout()
        self.gps_auto_range_check = QCheckBox("Auto range")
        self.gps_auto_range_check.setChecked(True)
        self.gps_auto_range_check.toggled.connect(self.gps_auto_range_changed)
        range_row.addWidget(self.gps_auto_range_check)
        range_row.addWidget(QLabel("Min"))
        self.gps_min_spin = self._gps_range_spinbox()
        self.gps_min_spin.valueChanged.connect(self.refresh_gps)
        range_row.addWidget(self.gps_min_spin)
        range_row.addWidget(QLabel("Max"))
        self.gps_max_spin = self._gps_range_spinbox()
        self.gps_max_spin.valueChanged.connect(self.refresh_gps)
        range_row.addWidget(self.gps_max_spin)
        self.gps_midpoint_check = QCheckBox("Midpoint")
        self.gps_midpoint_check.toggled.connect(self.gps_midpoint_changed)
        range_row.addWidget(self.gps_midpoint_check)
        self.gps_midpoint_spin = self._gps_range_spinbox()
        self.gps_midpoint_spin.valueChanged.connect(self.refresh_gps)
        range_row.addWidget(self.gps_midpoint_spin)
        range_row.addStretch()
        layout.addLayout(range_row)

        marker_row = QHBoxLayout()
        marker_row.addWidget(QLabel("Marker values"))
        self.gps_marker_value_combos = []
        for index in range(3):
            combo = QComboBox()
            combo.setMinimumWidth(150)
            combo.currentTextChanged.connect(self.gps_marker_values_changed)
            marker_row.addWidget(QLabel(f"{index + 1}"))
            marker_row.addWidget(combo)
            self.gps_marker_value_combos.append(combo)
        marker_row.addStretch()
        layout.addLayout(marker_row)

        self.gps_view = GpsPathWidget()
        self.gps_view.elapsed_seeked.connect(self.seek_gps_elapsed)
        self.gps_view.playing_changed.connect(self.set_gps_playback_playing)
        self.gps_view.speed_changed.connect(self.set_gps_playback_speed)
        layout.addWidget(self.gps_view, 1)
        self.gps_tab = tab
        self.tabs.addTab(tab, "Flight Map")
        self._update_gps_color_buttons()
        self._update_gps_range_enabled()
        self.populate_gps_value_combos()

    def _build_flight_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.flight_notes = QTextEdit()
        self.flight_notes.setPlaceholderText("Flight notes")
        self.flight_video = QLineEdit()
        browse = QPushButton("Browse Video")
        browse.clicked.connect(self.select_video)
        video_row = QHBoxLayout()
        video_row.addWidget(self.flight_video)
        video_row.addWidget(browse)
        save = QPushButton("Save Flight Notes")
        save.clicked.connect(self.save_flight_notes)
        open_video = QPushButton("Open Linked Video")
        open_video.clicked.connect(self.open_linked_video)
        layout.addRow("Notes", self.flight_notes)
        layout.addRow("Video", video_row)
        layout.addRow(save, open_video)
        self.tabs.addTab(tab, "Flight Notes")
        self.flight_notes_tab = tab

    def _build_battery_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        add_row = QHBoxLayout()
        self.battery_name = QLineEdit()
        self.battery_name.setPlaceholderText("Battery ID / name")
        self.battery_cells = QSpinBox()
        self.battery_cells.setRange(1, 14)
        self.battery_cells.setValue(4)
        add_btn = QPushButton("Add Battery")
        add_btn.clicked.connect(self.add_battery)
        add_row.addWidget(self.battery_name)
        add_row.addWidget(QLabel("Cells"))
        add_row.addWidget(self.battery_cells)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        analyze_row = QHBoxLayout()
        self.battery_select = QComboBox()
        self.voltage_combo = QComboBox()
        self.current_combo = QComboBox()
        analyze_btn = QPushButton("Calculate IR From Current Log")
        analyze_btn.clicked.connect(self.calculate_battery_ir)
        analyze_row.addWidget(self.battery_select)
        analyze_row.addWidget(self.voltage_combo)
        analyze_row.addWidget(self.current_combo)
        analyze_row.addWidget(analyze_btn)
        layout.addLayout(analyze_row)

        self.battery_table = QTableWidget(0, 4)
        self.battery_table.setHorizontalHeaderLabels(["ID", "Name", "Cells", "Active"])
        self._horizontal_header(self.battery_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._configure_sortable_table(self.battery_table)
        layout.addWidget(self.battery_table, 1)

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Battery", "Date", "Pack mOhm", "Cell mOhm", "Health", "Log"])
        self._horizontal_header(self.history_table).setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._configure_sortable_table(self.history_table)
        layout.addWidget(self.history_table, 2)
        self.tabs.addTab(tab, "Batteries")
        self.refresh_batteries()

    def _build_sync_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.sync_source = QLineEdit()
        self.sync_target = QLineEdit()
        form.addRow("Radio SD logs", self._path_picker(self.sync_source))
        form.addRow("PC library", self._path_picker(self.sync_target))
        layout.addLayout(form)
        buttons = QHBoxLayout()
        scan = QPushButton("Scan Newer Logs")
        scan.clicked.connect(self.scan_sync)
        copy = QPushButton("Copy Selected Candidates")
        copy.clicked.connect(self.copy_sync_candidates)
        buttons.addWidget(scan)
        buttons.addWidget(copy)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.sync_table = QTableWidget(0, 4)
        self.sync_table.setHorizontalHeaderLabels(["Reason", "Relative Path", "Source", "Target"])
        self._horizontal_header(self.sync_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.sync_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sync_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._configure_sortable_table(self.sync_table)
        layout.addWidget(self.sync_table)
        self.tabs.addTab(tab, "SD Sync")

    def _build_alias_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.alias_profile = QComboBox()
        self.alias_profile.setEditable(True)
        self.alias_profile.currentTextChanged.connect(self.load_alias_profile)
        save = QPushButton("Save Profile")
        save.clicked.connect(self.save_alias_profile)
        row.addWidget(QLabel("Alias profile"))
        row.addWidget(self.alias_profile)
        row.addWidget(save)
        layout.addLayout(row)
        self.alias_table = QTableWidget(0, 2)
        self.alias_table.setHorizontalHeaderLabels(["Hardware switch/file", "Radio alias/UI label"])
        self._horizontal_header(self.alias_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._configure_sortable_table(self.alias_table)
        layout.addWidget(self.alias_table)
        self.tabs.addTab(tab, "Switch Aliases")
        self.populate_alias_profiles()

    def _build_voice_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        add = QPushButton("Add Item")
        add.clicked.connect(self.add_voice_item)
        open_csv = QPushButton("Open CSV")
        open_csv.clicked.connect(self.open_voice_csv)
        save_csv = QPushButton("Save CSV")
        save_csv.clicked.connect(self.save_voice_csv)
        generate = QPushButton("Generate WAV Files")
        generate.clicked.connect(self.generate_voice_pack)
        row.addWidget(add)
        row.addWidget(open_csv)
        row.addWidget(save_csv)
        row.addWidget(generate)
        row.addStretch()
        layout.addLayout(row)
        self.voice_table = QTableWidget(0, 2)
        self.voice_table.setHorizontalHeaderLabels(["Text to be Spoken", "Target WAV Filename"])
        self._horizontal_header(self.voice_table).setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._horizontal_header(self.voice_table).setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._configure_sortable_table(self.voice_table)
        layout.addWidget(self.voice_table)
        self.tabs.addTab(tab, "Voice Pack")

    def _path_picker(self, line_edit: QLineEdit) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Browse")
        button.clicked.connect(lambda: self.pick_directory(line_edit))
        layout.addWidget(line_edit)
        layout.addWidget(button)
        return wrapper

    @staticmethod
    def _gps_range_spinbox() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
        spin.setSingleStep(1.0)
        spin.setMinimumWidth(120)
        return spin

    def _update_gps_color_buttons(self) -> None:
        for button, color in (
            (self.gps_start_color_button, self.gps_start_color),
            (self.gps_end_color_button, self.gps_end_color),
        ):
            button.setText(color.upper())
            button.setStyleSheet(f"background: {color}; color: white; border: 0; border-radius: 4px; padding: 7px 10px;")

    def choose_gps_color(self, role: str) -> None:
        current = self.gps_start_color if role == "start" else self.gps_end_color
        color = QColorDialog.getColor(QColor(current), self, f"Choose {role} gradient color")
        if not color.isValid():
            return
        if role == "start":
            self.gps_start_color = color.name()
        else:
            self.gps_end_color = color.name()
        self._update_gps_color_buttons()
        self.refresh_gps()

    def gps_color_changed(self, *_args) -> None:
        self._populate_gps_range_defaults()
        self._update_gps_range_enabled()
        self.refresh_gps()

    def gps_marker_values_changed(self, *_args) -> None:
        self.sync_gps_cursor()

    def populate_gps_value_combos(self) -> None:
        current_values = [combo.currentText() for combo in self.gps_marker_value_combos]
        for index, combo in enumerate(self.gps_marker_value_combos):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)")
            if self.current_log is not None:
                for column in self.current_log.parameter_columns:
                    combo.addItem(column)
            current = current_values[index] if index < len(current_values) else ""
            restored_index = combo.findText(current)
            if restored_index >= 0:
                combo.setCurrentIndex(restored_index)
            combo.blockSignals(False)
        self.sync_gps_cursor()

    def gps_auto_range_changed(self, *_args) -> None:
        self._populate_gps_range_defaults()
        self._update_gps_range_enabled()
        self.refresh_gps()

    def gps_midpoint_changed(self, *_args) -> None:
        self._populate_gps_range_defaults()
        self._update_gps_range_enabled()
        self.refresh_gps()

    def _update_gps_range_enabled(self) -> None:
        color_column = self.gps_color_combo.currentText()
        has_color = bool(color_column and color_column != "(none)")
        auto_range = self.gps_auto_range_check.isChecked()
        # Range controls only make sense when a numeric color column is active;
        # the midpoint slider is a second-order option layered on top of that.
        self.gps_auto_range_check.setEnabled(has_color)
        self.gps_min_spin.setEnabled(has_color and not auto_range)
        self.gps_max_spin.setEnabled(has_color and not auto_range)
        self.gps_midpoint_check.setEnabled(has_color)
        self.gps_midpoint_spin.setEnabled(has_color and self.gps_midpoint_check.isChecked())

    def _populate_gps_range_defaults(self) -> None:
        if self.current_log is None:
            return
        color_column = self.gps_color_combo.currentText()
        if not color_column or color_column == "(none)" or color_column not in self.current_log.dataframe.columns:
            return
        series = self.current_log.dataframe[color_column]
        values = series.dropna()
        if values.empty:
            return
        # Seed the manual range controls from the current data so the user can
        # immediately switch off auto-range without getting blank defaults.
        minimum = float(values.min())
        maximum = float(values.max())
        midpoint = minimum + (maximum - minimum) / 2.0
        for spin, value in (
            (self.gps_min_spin, minimum),
            (self.gps_max_spin, maximum),
            (self.gps_midpoint_spin, midpoint),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _gps_gradient_options(self) -> GpsGradientOptions:
        color = self.gps_color_combo.currentText() or None
        if color == "(none)":
            color = None
        return GpsGradientOptions(
            color_column=color,
            start_color=self.gps_start_color,
            end_color=self.gps_end_color,
            reverse=self.gps_reverse_check.isChecked(),
            auto_range=self.gps_auto_range_check.isChecked(),
            range_min=self.gps_min_spin.value(),
            range_max=self.gps_max_spin.value(),
            midpoint=self.gps_midpoint_spin.value() if self.gps_midpoint_check.isChecked() else None,
            scope_start_seconds=self.telemetry_visible_elapsed_range[0]
            if self.telemetry_visible_elapsed_range is not None
            else None,
            scope_end_seconds=self.telemetry_visible_elapsed_range[1]
            if self.telemetry_visible_elapsed_range is not None
            else None,
        )

    def _reset_gps_playback(self) -> None:
        self.gps_playback_playing = False
        self.gps_playback_speed = 1.0
        self.gps_playback_last_tick = None
        self.gps_playback_elapsed_seconds = 0.0
        self.gps_playback_timer.stop()
        self.gps_timeline_seconds = relative_seconds(self.current_log) if self.current_log is not None else []
        self.gps_timeline_is_monotonic = all(
            left <= right for left, right in zip(self.gps_timeline_seconds, self.gps_timeline_seconds[1:])
        )

    def _gps_elapsed_for_index(self, index: int) -> float:
        if not self.gps_timeline_seconds:
            return 0.0
        safe_index = max(0, min(index, len(self.gps_timeline_seconds) - 1))
        return float(self.gps_timeline_seconds[safe_index])

    def _gps_duration_seconds(self) -> float:
        return max(self.gps_timeline_seconds) if self.gps_timeline_seconds else 0.0

    def _gps_playback_scope_bounds(self) -> tuple[float, float]:
        duration = self._gps_duration_seconds()
        if self.telemetry_visible_elapsed_range is None:
            return 0.0, duration
        start, end = self.telemetry_visible_elapsed_range
        start = max(0.0, min(float(start), duration))
        end = max(0.0, min(float(end), duration))
        if end < start:
            start, end = end, start
        return start, end

    def _nearest_gps_index(self, elapsed_seconds: float) -> int:
        if not self.gps_timeline_seconds:
            return 0
        if not self.gps_timeline_is_monotonic:
            return min(
                range(len(self.gps_timeline_seconds)),
                key=lambda index: abs(self.gps_timeline_seconds[index] - elapsed_seconds),
            )
        insertion = bisect_left(self.gps_timeline_seconds, elapsed_seconds)
        if insertion <= 0:
            return 0
        if insertion >= len(self.gps_timeline_seconds):
            return len(self.gps_timeline_seconds) - 1
        before = insertion - 1
        after = insertion
        if abs(self.gps_timeline_seconds[after] - elapsed_seconds) < abs(elapsed_seconds - self.gps_timeline_seconds[before]):
            return after
        return before

    def _gps_marker_columns(self) -> list[str]:
        if self.current_log is None:
            return []
        columns: list[str] = []
        color_column = self.gps_color_combo.currentText()
        if color_column and color_column != "(none)" and color_column in self.current_log.dataframe.columns:
            columns.append(color_column)
        for combo in self.gps_marker_value_combos:
            column = combo.currentText()
            if (
                column
                and column != "(none)"
                and column in self.current_log.dataframe.columns
                and column not in columns
            ):
                columns.append(column)
        return columns[:4]

    def _gps_marker_values(self, index: int | None = None) -> list[dict[str, object]]:
        if self.current_log is None:
            return []
        values: list[dict[str, object]] = []
        selected_index = self.selected_index if index is None else index
        row_index = max(0, min(selected_index, len(self.current_log.dataframe) - 1))
        for column in self._gps_marker_columns():
            value = self.current_log.dataframe[column].iloc[row_index]
            if pd.isna(value):
                clean_value: object | None = None
            elif hasattr(value, "item"):
                clean_value = value.item()
            else:
                clean_value = value
            values.append({"label": column, "value": clean_value})
        return values

    def _selected_index_in_elapsed_scope(self, index: int) -> int:
        if self.current_log is None:
            return index
        scoped_index = max(0, min(index, len(self.current_log.dataframe) - 1))
        if self.telemetry_visible_elapsed_range is not None:
            scoped_index = self._clamp_index_to_elapsed_scope(
                self.telemetry_visible_elapsed_range,
                scoped_index,
            )
        return scoped_index

    def _gps_cursor_payload(self) -> dict[str, object]:
        selected_index = self._selected_index_in_elapsed_scope(self.selected_index)
        scope_start, scope_end = self._gps_playback_scope_bounds()
        if self.gps_playback_playing:
            elapsed = max(scope_start, min(self.gps_playback_elapsed_seconds, scope_end))
        else:
            elapsed = self._gps_elapsed_for_index(selected_index)
        return {
            "index": selected_index,
            "row": selected_index + 1,
            "elapsedSeconds": elapsed,
            "durationSeconds": max(0.0, scope_end - scope_start),
            "scopeStartSeconds": scope_start,
            "scopeEndSeconds": scope_end,
            "playing": self.gps_playback_playing,
            "speed": self.gps_playback_speed,
            "values": self._gps_marker_values(selected_index),
        }

    def sync_gps_cursor(self) -> None:
        if not hasattr(self, "gps_view"):
            return
        self.gps_view.set_cursor(self._gps_cursor_payload())

    def seek_gps_elapsed(self, elapsed_seconds: float) -> None:
        if self.current_log is None:
            return
        scope_start, scope_end = self._gps_playback_scope_bounds()
        self.gps_playback_elapsed_seconds = max(scope_start, min(float(elapsed_seconds), scope_end))
        self.set_selected_index(
            self._nearest_gps_index(self.gps_playback_elapsed_seconds),
            sync_playback_elapsed=False,
        )

    def set_gps_playback_playing(self, playing: bool) -> None:
        scope_start, scope_end = self._gps_playback_scope_bounds()
        self.gps_playback_playing = (
            bool(playing)
            and self.current_log is not None
            and bool(self.gps_timeline_seconds)
            and scope_end > scope_start
        )
        self.gps_playback_last_tick = time.perf_counter() if self.gps_playback_playing else None
        if self.gps_playback_playing:
            current_elapsed = self._gps_elapsed_for_index(self.selected_index)
            if current_elapsed < scope_start or current_elapsed > scope_end:
                self.gps_playback_elapsed_seconds = scope_start
                self.set_selected_index(
                    self._clamp_index_to_elapsed_scope((scope_start, scope_end)),
                    sync_playback_elapsed=False,
                )
            else:
                self.gps_playback_elapsed_seconds = current_elapsed
        if self.gps_playback_playing:
            self.gps_playback_timer.start()
        else:
            self.gps_playback_timer.stop()
        self.sync_gps_cursor()

    def set_gps_playback_speed(self, speed: float) -> None:
        allowed = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
        self.gps_playback_speed = min(allowed, key=lambda candidate: abs(candidate - float(speed)))
        self.sync_gps_cursor()

    def gps_playback_tick(self) -> None:
        if self.current_log is None or not self.gps_playback_playing:
            self.set_gps_playback_playing(False)
            return
        now = time.perf_counter()
        previous = self.gps_playback_last_tick or now
        self.gps_playback_last_tick = now
        next_elapsed = self.gps_playback_elapsed_seconds + (now - previous) * self.gps_playback_speed
        _scope_start, scope_end = self._gps_playback_scope_bounds()
        self.gps_playback_elapsed_seconds = min(next_elapsed, scope_end)
        if next_elapsed >= scope_end:
            self.set_selected_index(self._nearest_gps_index(scope_end), sync_playback_elapsed=False)
            self.set_gps_playback_playing(False)
            return
        self.set_selected_index(self._nearest_gps_index(next_elapsed), sync_playback_elapsed=False)

    @staticmethod
    def _horizontal_header(table: QTableWidget) -> QHeaderView:
        header = table.horizontalHeader()
        if header is None:
            raise RuntimeError("QTableWidget did not provide a horizontal header")
        return header

    @staticmethod
    def _configure_sortable_table(table: QTableWidget) -> None:
        header = MainWindow._horizontal_header(table)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        table.setSortingEnabled(True)

    def _apply_style(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            raise RuntimeError("No QApplication instance found")
        app.setStyle("Fusion")
        if self.dark_mode:
            self.setStyleSheet(
                """
            QMainWindow, QWidget { background: #20242b; color: #e5e7eb; }
            QMenuBar, QMenu, QStatusBar { background: #181b20; color: #e5e7eb; }
            QPushButton { background: #2f80ed; color: white; border: 0; border-radius: 4px; padding: 7px 10px; }
            QPushButton:hover { background: #3f8df2; }
            QPushButton:checked { background: #185fc7; border: 1px solid #87b7ff; padding: 6px 9px; }
            QLineEdit, QTextEdit, QComboBox, QTreeWidget, QTableWidget, QTableView {
                background: #15181d; color: #e5e7eb; border: 1px solid #3a414d; border-radius: 4px;
            }
            QHeaderView::section { background: #2b3038; color: #e5e7eb; padding: 5px; border: 0; }
            QTabWidget::pane { border: 1px solid #333a44; }
            QTabBar::tab { background: #242932; color: #e5e7eb; padding: 8px 14px; }
            QTabBar::tab:selected { background: #2f80ed; color: white; }
            QLabel#summary { color: #aab2c0; font-size: 12px; }
            """
            )
        else:
            self.setStyleSheet(
                """
            QMainWindow, QWidget { background: #f6f8fb; color: #1f2937; }
            QMenuBar, QMenu, QStatusBar { background: #ffffff; color: #1f2937; border-bottom: 1px solid #d7dde7; }
            QPushButton { background: #2f80ed; color: white; border: 0; border-radius: 4px; padding: 7px 10px; }
            QPushButton:hover { background: #1f6fd4; }
            QPushButton:checked { background: #185fc7; border: 1px solid #0f4fb0; padding: 6px 9px; }
            QLineEdit, QTextEdit, QComboBox, QTreeWidget, QTableWidget, QTableView {
                background: #ffffff; color: #1f2937; border: 1px solid #c9d2df; border-radius: 4px;
            }
            QHeaderView::section { background: #e8edf4; color: #1f2937; padding: 5px; border: 0; }
            QTabWidget::pane { border: 1px solid #ccd5e1; }
            QTabBar::tab { background: #e8edf4; color: #1f2937; padding: 8px 14px; }
            QTabBar::tab:selected { background: #2f80ed; color: white; }
            QLabel#summary { color: #586579; font-size: 12px; }
            """
            )

    def _restore_state(self) -> None:
        # Restore the library first so any saved last-log path can be resolved
        # against the same root that was active in the previous session.
        last_library = self.store.get_setting("library_root")
        if last_library and Path(last_library).exists():
            self.load_library(Path(last_library))
        last_log = self.store.get_setting("last_log")
        if last_log and Path(last_log).exists():
            self.load_log(Path(last_log))

    def _set_empty_graph(self) -> None:
        self.graph_view.set_plot(None, [])

    @staticmethod
    def _safe_log_dialog_directory() -> str:
        # Passing an empty directory lets the Windows native dialog reuse its
        # last location. After choosing a cloud-backed library that can block
        # the UI while the shell enumerates thousands of remote files.
        return str(Path.home())

    def open_log_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open telemetry log",
            self._safe_log_dialog_directory(),
            "Telemetry logs (*.csv *.log);;All files (*.*)",
        )
        if path:
            self.load_log(Path(path))

    def open_compare_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open compare telemetry log",
            self._safe_log_dialog_directory(),
            "Telemetry logs (*.csv *.log);;All files (*.*)",
        )
        if not path:
            return
        try:
            self.compare_log = load_log(Path(path), self.library_root)
            self.compare_toggle.setChecked(True)
            self.compare_label.setText(Path(path).name)
            self.refresh_plots()
        except Exception as exc:
            QMessageBox.warning(self, "Compare log failed", str(exc))

    def open_library_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open log library")
        if path:
            self.load_library(Path(path))

    def load_library(self, path: Path) -> None:
        self.library_root = path
        self.store.set_setting("library_root", str(path))
        self.library_logs = scan_library(path)
        self.populate_library_tree()
        self.status.showMessage(f"Loaded {len(self.library_logs)} logs from {path}")

    def populate_library_tree(self) -> None:
        self.library_tree.clear()
        for model, logs in group_by_model(self.library_logs).items():
            latest = max((log.modified for log in logs), default=0.0)
            total_size = sum(log.size for log in logs)
            parent = QTreeWidgetItem(
                [
                    model,
                    str(len(logs)),
                    self._format_timestamp(latest),
                    self._format_size(total_size),
                ]
            )
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            parent.setData(1, Qt.ItemDataRole.UserRole, len(logs))
            parent.setData(2, Qt.ItemDataRole.UserRole, latest)
            parent.setData(3, Qt.ItemDataRole.UserRole, total_size)
            # Store numeric sort keys separately from the visible labels so the
            # tree can be re-sorted without reparsing formatted text.
            self.library_tree.addTopLevelItem(parent)
            for log in logs:
                item = QTreeWidgetItem(
                    [
                        log.name,
                        "",
                        self._format_timestamp(log.modified),
                        self._format_size(log.size),
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, str(log.path))
                item.setData(2, Qt.ItemDataRole.UserRole, log.modified)
                item.setData(3, Qt.ItemDataRole.UserRole, log.size)
                parent.addChild(item)
        self._sort_library_tree(self.library_sort_column, self.library_sort_order)
        self.library_tree.collapseAll()

    def library_item_activated(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.load_log(Path(path))

    def library_header_clicked(self, column: int) -> None:
        # A real QHeaderView click may update its visual indicator before this
        # slot runs. Keep the authoritative state outside the header so one
        # user click produces exactly one direction change.
        if self.library_sort_column == column:
            order = (
                Qt.SortOrder.AscendingOrder
                if self.library_sort_order == Qt.SortOrder.DescendingOrder
                else Qt.SortOrder.DescendingOrder
            )
        else:
            order = Qt.SortOrder.DescendingOrder if column in {1, 2, 3} else Qt.SortOrder.AscendingOrder
        self._sort_library_tree(column, order)

    def _tab_is_active(self, tab_attr: str) -> bool:
        tab = getattr(self, tab_attr, None)
        return tab is not None and hasattr(self, "tabs") and self.tabs.currentWidget() is tab

    def _refresh_raw_log_if_visible(self, force: bool = False) -> None:
        if not force and not self._tab_is_active("raw_log_tab"):
            self._deferred_views_dirty.add("raw_log")
            return
        self._deferred_views_dirty.discard("raw_log")
        self._set_raw_log_dataframe(self.current_log.dataframe if self.current_log is not None else None)

    def _refresh_statistics_if_visible(self, force: bool = False) -> None:
        if not force and not self._tab_is_active("statistics_tab"):
            self._deferred_views_dirty.add("statistics")
            return
        self._deferred_views_dirty.discard("statistics")
        self.populate_statistics_table()

    def _refresh_flight_notes_if_visible(self, force: bool = False) -> None:
        if not force and not self._tab_is_active("flight_notes_tab"):
            self._deferred_views_dirty.add("flight_notes")
            return
        self._deferred_views_dirty.discard("flight_notes")
        self.load_flight_notes()

    def load_log(self, path: Path) -> None:
        try:
            self.current_log = load_log(path, self.library_root)
            # Discard range callbacks already queued by the outgoing WebEngine
            # document before the replacement log rebuilds the plot.
            self._telemetry_x_range_generation += 1
            self.selected_index = 0
            self.telemetry_visible_elapsed_range = None
            self.reset_telemetry_axis_grouping(refresh=False)
            self._reset_gps_playback()
            self.store.set_setting("last_log", str(path))
            # Every dependent widget needs a refresh because a new log changes
            # the available columns, GPS choices, and saved notes target.
            self._refresh_raw_log_if_visible()
            self.initialize_selected_parameters()
            self.populate_columns()
            self._refresh_statistics_if_visible()
            self.populate_gps_color_combo()
            self.populate_gps_value_combos()
            self.populate_analysis_combos()
            self._refresh_flight_notes_if_visible()
            self.refresh_plots()
            self.status.showMessage(f"Loaded {path.name}")
        except InvalidTelemetryLogError as exc:
            QMessageBox.warning(self, "Log load failed", str(exc))
        except Exception:
            # Preserve the previous successful log on failure so the Raw Log tab
            # stays aligned with the rest of the UI and does not temporarily
            # disappear while the existing session is still valid.
            QMessageBox.warning(self, "Log load failed", traceback.format_exc())

    def populate_columns(self, *_args) -> None:
        self.column_table.blockSignals(True)
        self.column_table.setSortingEnabled(False)
        self.column_table.setRowCount(0)
        if self.current_log is None:
            self.column_table.setSortingEnabled(True)
            self.column_table.blockSignals(False)
            return
        query = self.column_filter.text().strip().lower()
        for col in self.current_log.parameter_columns:
            if query and query not in col.lower():
                continue
            row = self.column_table.rowCount()
            self.column_table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setData(Qt.ItemDataRole.UserRole, col)
            check.setCheckState(Qt.CheckState.Checked if col in self.selected_parameter_columns else Qt.CheckState.Unchecked)
            name = QTableWidgetItem(col)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name.setData(Qt.ItemDataRole.UserRole, col)
            self.column_table.setItem(row, 0, check)
            self.column_table.setItem(row, 1, name)
        self.column_table.setSortingEnabled(True)
        self.column_table.blockSignals(False)
        self.update_summary()

    @staticmethod
    def _format_stat_value(value: float | int) -> str:
        if isinstance(value, int):
            return str(value)
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def populate_statistics_table(self) -> None:
        self.statistics_table.blockSignals(True)
        self.statistics_table.setSortingEnabled(False)
        self.statistics_table.setRowCount(0)
        if self.current_log is None:
            self.statistics_empty_label.setText("No log loaded")
            self.statistics_reset_button.setEnabled(False)
            self.statistics_table.setSortingEnabled(True)
            self.statistics_table.blockSignals(False)
            return

        stats = basic_stats(self.current_log.dataframe, self.current_log.parameter_columns)
        available_columns = [col for col in self.current_log.parameter_columns if col in stats]
        self.statistics_excluded_columns &= set(available_columns)
        visible_columns = [col for col in available_columns if col not in self.statistics_excluded_columns]
        self.statistics_reset_button.setEnabled(bool(self.statistics_excluded_columns))
        if not available_columns:
            self.statistics_empty_label.setText("No numeric telemetry statistics available")
            self.statistics_reset_button.setEnabled(False)
            self.statistics_table.setSortingEnabled(True)
            self.statistics_table.blockSignals(False)
            return
        if not visible_columns:
            self.statistics_empty_label.setText("All statistics excluded")
            self.statistics_table.setSortingEnabled(True)
            self.statistics_table.blockSignals(False)
            return

        self.statistics_empty_label.setText("")
        for col in visible_columns:
            row = self.statistics_table.rowCount()
            self.statistics_table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setData(Qt.ItemDataRole.UserRole, col)
            check.setCheckState(Qt.CheckState.Checked)
            self.statistics_table.setItem(row, 0, check)

            name = QTableWidgetItem(col)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name.setData(Qt.ItemDataRole.UserRole, col)
            self.statistics_table.setItem(row, 1, name)

            for stat_column, key in enumerate(("count", "min", "max", "mean", "std"), start=2):
                value = stats[col][key]
                item = _NumericSortableTableItem(self._format_stat_value(value), value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(Qt.ItemDataRole.UserRole, value)
                self.statistics_table.setItem(row, stat_column, item)

        self.statistics_table.setSortingEnabled(True)
        self.statistics_table.blockSignals(False)

    def reset_statistics_exclusions(self) -> None:
        self.statistics_excluded_columns.clear()
        self.populate_statistics_table()

    def statistics_item_changed(self, item: QTableWidgetItem | None = None) -> None:
        if item is None or item.column() != 0:
            return
        column = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(column, str):
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.statistics_excluded_columns.discard(column)
        else:
            self.statistics_excluded_columns.add(column)
        self.populate_statistics_table()

    def selected_columns(self) -> list[str]:
        if self.current_log is None:
            return []
        return [col for col in self.current_log.parameter_columns if col in self.selected_parameter_columns]

    def _selected_column_table_columns(self) -> list[str]:
        selection_model = self.column_table.selectionModel()
        if selection_model is None:
            return []
        rows = sorted({index.row() for index in selection_model.selectedRows()})
        columns: list[str] = []
        for row in rows:
            item = self.column_table.item(row, 1) or self.column_table.item(row, 0)
            if item is None:
                continue
            column = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(column, str):
                columns.append(column)
        return columns

    def show_column_context_menu(self, position: QPoint) -> None:
        index = self.column_table.indexAt(position)
        selection_model = self.column_table.selectionModel()
        if index.isValid() and selection_model is not None:
            selected_rows = {row_index.row() for row_index in selection_model.selectedRows()}
            if index.row() not in selected_rows:
                self.column_table.selectRow(index.row())

        selected_columns = self._selected_column_table_columns()
        menu = QMenu(self)
        group_action = QAction("Group selected on one Y axis", self)
        group_action.setEnabled(len(selected_columns) >= 2)
        menu.addAction(group_action)
        ungroup_action = QAction("Ungroup selected from shared axes", self)
        ungroup_action.setEnabled(bool(selected_columns))
        menu.addAction(ungroup_action)
        reset_action = QAction("Reset Y-axis grouping overrides", self)
        reset_action.setEnabled(bool(self.telemetry_axis_groups or self.telemetry_axis_ungrouped_columns))
        menu.addAction(reset_action)

        viewport = self.column_table.viewport()
        if viewport is None:
            return
        chosen = menu.exec(viewport.mapToGlobal(position))
        if chosen == group_action:
            self.group_selected_telemetry_axis_columns()
        elif chosen == ungroup_action:
            self.ungroup_selected_telemetry_axis_columns()
        elif chosen == reset_action:
            self.reset_telemetry_axis_grouping()

    def group_selected_telemetry_axis_columns(self) -> None:
        columns = self._selected_column_table_columns()
        if len(columns) < 2:
            return
        selected = set(columns)
        next_groups: list[tuple[str, ...]] = []
        for group in self.telemetry_axis_groups:
            remaining = tuple(column for column in group if column not in selected)
            if len(remaining) >= 2:
                next_groups.append(remaining)
        next_groups.append(tuple(columns))
        self.telemetry_axis_groups = next_groups
        self.telemetry_axis_ungrouped_columns.difference_update(selected)
        self.refresh_graph()
        self.status.showMessage(f"Grouped {len(columns)} telemetry parameters on one Y axis")

    def ungroup_selected_telemetry_axis_columns(self) -> None:
        columns = self._selected_column_table_columns()
        if not columns:
            return
        selected = set(columns)
        next_groups: list[tuple[str, ...]] = []
        for group in self.telemetry_axis_groups:
            remaining = tuple(column for column in group if column not in selected)
            if len(remaining) >= 2:
                next_groups.append(remaining)
        self.telemetry_axis_groups = next_groups
        self.telemetry_axis_ungrouped_columns.update(selected)
        self.refresh_graph()
        self.status.showMessage(f"Ungrouped {len(columns)} telemetry parameters for this session")

    def reset_telemetry_axis_grouping(self, refresh: bool = True) -> None:
        self.telemetry_axis_groups = []
        self.telemetry_axis_ungrouped_columns = set()
        if refresh:
            self.refresh_graph()
            self.status.showMessage("Reset telemetry Y-axis grouping overrides")

    def initialize_selected_parameters(self) -> None:
        if self.current_log is None:
            self.selected_parameter_columns = set()
            return
        available = set(self.current_log.parameter_columns)
        stored = set(self.store.get_setting("selected_columns", []))
        selected = stored & available
        if not selected:
            # Fall back to a heuristic shortlist only when the previous
            # selection no longer matches the current log.
            selected = set(suggest_display_columns(self.current_log.parameter_columns))
        self.selected_parameter_columns = selected

    def set_visible_columns_checked(self, checked: bool) -> None:
        if self.current_log is None:
            return
        # Block signals while bulk-toggling to avoid a cascade of itemChanged
        # callbacks for every row in the table.
        self.column_table.blockSignals(True)
        try:
            for row in range(self.column_table.rowCount()):
                check = self.column_table.item(row, 0)
                if check is None:
                    continue
                column = check.data(Qt.ItemDataRole.UserRole)
                if not isinstance(column, str):
                    continue
                if checked:
                    self.selected_parameter_columns.add(column)
                    check.setCheckState(Qt.CheckState.Checked)
                else:
                    self.selected_parameter_columns.discard(column)
                    check.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.column_table.blockSignals(False)
        self.commit_column_selection()

    def column_changed(self, item: QTableWidgetItem | None = None) -> None:
        if item is not None and item.column() == 0:
            column = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(column, str):
                if item.checkState() == Qt.CheckState.Checked:
                    self.selected_parameter_columns.add(column)
                else:
                    self.selected_parameter_columns.discard(column)
        self.commit_column_selection()

    def commit_column_selection(self) -> None:
        cols = self.selected_columns()
        cleared_visible_scope = False
        if not cols and self.telemetry_visible_elapsed_range is not None:
            self.telemetry_visible_elapsed_range = None
            cleared_visible_scope = True
        self.store.set_setting("selected_columns", cols)
        self.update_summary()
        self.refresh_graph()
        if cleared_visible_scope:
            self.refresh_gps()
            self.sync_gps_cursor()
        self.update_info_panel()

    def update_summary(self) -> None:
        if self.current_log is None:
            self.file_summary.setText("No log loaded")
            return
        info = self.current_log.info
        gps = "yes" if info.has_gps else "no"
        duration = f"{info.duration_seconds:.1f}s"
        self.file_summary.setText(
            f"<b>{html.escape(info.name)}</b><br>"
            f"Model: {html.escape(info.model)}<br>"
            f"Rows: {info.rows} | Columns: {info.columns}<br>"
            f"Duration: {duration} | GPS: {gps}"
        )

    def refresh_plots(self, *_args) -> None:
        self.update_summary()
        self.refresh_graph()
        self.refresh_gps()
        self.update_info_panel()

    def _telemetry_axis_value_to_elapsed(self, value: object) -> float | None:
        if self.current_log is None or value is None or isinstance(value, bool):
            return None
        try:
            number = float(value if isinstance(value, (int, float)) else str(value))
        except (TypeError, ValueError):
            number = math.nan
        if math.isfinite(number):
            return number
        if self.current_log.time is None or not self.current_log.time.notna().any():
            return None
        try:
            parsed_timestamp = pd.to_datetime(str(value), errors="coerce")
        except (TypeError, ValueError):
            return None
        if pd.isna(parsed_timestamp):
            return None
        timestamp = pd.Timestamp(str(parsed_timestamp))
        time_series = self.current_log.time.ffill().bfill()
        start_timestamp = pd.Timestamp(time_series.iloc[0])
        if pd.isna(start_timestamp):
            return None
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_localize(None)
        if start_timestamp.tzinfo is not None:
            start_timestamp = start_timestamp.tz_localize(None)
        return float((timestamp - start_timestamp).total_seconds())

    def _normalise_telemetry_elapsed_range(self, start: object, end: object) -> tuple[float, float] | None:
        if self.current_log is None:
            return None
        start_elapsed = self._telemetry_axis_value_to_elapsed(start)
        end_elapsed = self._telemetry_axis_value_to_elapsed(end)
        if start_elapsed is None or end_elapsed is None:
            return None
        if end_elapsed < start_elapsed:
            start_elapsed, end_elapsed = end_elapsed, start_elapsed
        elapsed_values = relative_seconds(self.current_log)
        if not elapsed_values:
            return None
        timeline_start = min(elapsed_values)
        timeline_end = max(elapsed_values)
        if timeline_end <= timeline_start:
            return None
        empty_scope_width = min(0.001, (timeline_end - timeline_start) / 1000.0)
        if end_elapsed < timeline_start:
            return timeline_start, timeline_start + empty_scope_width
        if start_elapsed > timeline_end:
            return timeline_end - empty_scope_width, timeline_end
        start_elapsed = max(timeline_start, min(start_elapsed, timeline_end))
        end_elapsed = max(timeline_start, min(end_elapsed, timeline_end))
        if end_elapsed <= start_elapsed:
            return None
        return start_elapsed, end_elapsed

    def _telemetry_elapsed_range_to_axis_range(self) -> tuple[object, object] | None:
        if self.current_log is None or self.telemetry_visible_elapsed_range is None:
            return None
        start_elapsed, end_elapsed = self.telemetry_visible_elapsed_range
        if self.telemetry_time_mode == "absolute" and self.current_log.time is not None and self.current_log.time.notna().any():
            time_series = self.current_log.time.ffill().bfill()
            start_timestamp = pd.Timestamp(time_series.iloc[0])
            if not pd.isna(start_timestamp):
                return (
                    start_timestamp + pd.Timedelta(seconds=start_elapsed),
                    start_timestamp + pd.Timedelta(seconds=end_elapsed),
                )
        return start_elapsed, end_elapsed

    def _gps_index_at_or_after_elapsed(self, elapsed_seconds: float, scope_end: float | None = None) -> int | None:
        if not self.gps_timeline_seconds:
            return None
        if self.gps_timeline_is_monotonic:
            index = bisect_left(self.gps_timeline_seconds, elapsed_seconds)
            if index < len(self.gps_timeline_seconds):
                value = self.gps_timeline_seconds[index]
                if scope_end is None or value <= scope_end:
                    return index
            return None
        for index, value in enumerate(self.gps_timeline_seconds):
            if value >= elapsed_seconds and (scope_end is None or value <= scope_end):
                return index
        return None

    def _gps_index_at_or_before_elapsed(self, elapsed_seconds: float, scope_start: float | None = None) -> int | None:
        if not self.gps_timeline_seconds:
            return None
        if self.gps_timeline_is_monotonic:
            index = bisect_right(self.gps_timeline_seconds, elapsed_seconds) - 1
            if index >= 0:
                value = self.gps_timeline_seconds[index]
                if scope_start is None or value >= scope_start:
                    return index
            return None
        for index in range(len(self.gps_timeline_seconds) - 1, -1, -1):
            value = self.gps_timeline_seconds[index]
            if value <= elapsed_seconds and (scope_start is None or value >= scope_start):
                return index
        return None

    def _clamp_index_to_elapsed_scope(self, scope: tuple[float, float], index: int | None = None) -> int:
        if self.current_log is None:
            return self.selected_index if index is None else index
        selected_index = self.selected_index if index is None else index
        selected_index = max(0, min(selected_index, len(self.current_log.dataframe) - 1))
        scope_start, scope_end = scope
        current_elapsed = self._gps_elapsed_for_index(selected_index)
        if scope_start <= current_elapsed <= scope_end:
            return selected_index
        if current_elapsed < scope_start:
            scoped_index = self._gps_index_at_or_after_elapsed(scope_start, scope_end)
        else:
            scoped_index = self._gps_index_at_or_before_elapsed(scope_end, scope_start)
        if scoped_index is not None:
            return scoped_index
        return selected_index

    def queue_telemetry_visible_x_range(self, payload: str) -> None:
        # Keep the WebChannel boundary to a Qt-owned string. Generic Python
        # object parameters can outlive the originating callback when queued.
        generation = self._telemetry_x_range_generation
        QTimer.singleShot(
            0,
            lambda: self._apply_telemetry_visible_x_range(payload, generation),
        )

    def _apply_telemetry_visible_x_range(self, payload: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._telemetry_x_range_generation:
            return
        try:
            values = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(values, dict):
            return
        document_generation = values.get("generation")
        range_values = values.get("range")
        if (
            not isinstance(document_generation, int)
            or isinstance(document_generation, bool)
            or document_generation != self.graph_view.render_generation
            or not isinstance(range_values, list)
            or len(range_values) != 2
        ):
            return
        self.set_telemetry_visible_x_range(range_values[0], range_values[1])

    def set_telemetry_visible_x_range(self, start: object, end: object) -> None:
        next_range = None if not self.selected_columns() else self._normalise_telemetry_elapsed_range(start, end)
        previous_range = self.telemetry_visible_elapsed_range
        if previous_range is not None and next_range is not None:
            if abs(previous_range[0] - next_range[0]) < 0.01 and abs(previous_range[1] - next_range[1]) < 0.01:
                return
        elif previous_range == next_range:
            return
        self.telemetry_visible_elapsed_range = next_range
        # The Plotly page already displays the range that raised this callback.
        # Keep the Python-side widget state aligned for later resize rerenders,
        # but do not reload the same WebEngine page while its QWebChannel call
        # is still unwinding.
        self.graph_view.set_x_axis_range(self._telemetry_elapsed_range_to_axis_range())
        if next_range is not None:
            clamped_index = self._clamp_index_to_elapsed_scope(next_range)
            if clamped_index != self.selected_index:
                self.selected_index = clamped_index
                self.gps_playback_elapsed_seconds = self._gps_elapsed_for_index(self.selected_index)
                self.update_info_panel()
                self.graph_view.set_cursor_index(self.selected_index)
        self.refresh_gps()
        self.sync_gps_cursor()

    def refresh_graph(self) -> None:
        if self.current_log is None:
            self._set_empty_graph()
            return
        cols = self.selected_columns()
        if not cols:
            self.graph_view.set_plot(self.current_log, [])
            return
        compare = self.compare_log if self.compare_toggle.isChecked() else None
        self.graph_view.set_plot(
            self.current_log,
            cols,
            compare=compare,
            selected_index=self.selected_index,
            show_grid=self.grid_action.isChecked(),
            dark=self.dark_mode,
            interaction_mode=self.telemetry_interaction_mode,
            time_mode=self.telemetry_time_mode,
            x_axis_range=self._telemetry_elapsed_range_to_axis_range(),
            manual_axis_groups=self.telemetry_axis_groups,
            ungrouped_axis_columns=tuple(sorted(self.telemetry_axis_ungrouped_columns)),
        )

    def refresh_gps(self, *_args, force: bool = False) -> None:
        if not force and not self._tab_is_active("gps_tab"):
            self._deferred_views_dirty.add("gps")
            return
        self._deferred_views_dirty.discard("gps")
        self.gps_view.set_path(
            self.current_log,
            options=self._gps_gradient_options(),
            dark=self.dark_mode,
        )
        self._gps_view_loaded = True
        self.sync_gps_cursor()

    def tab_changed(self, index: int) -> None:
        if hasattr(self, "statistics_tab") and self.tabs.widget(index) is self.statistics_tab:
            if "statistics" in self._deferred_views_dirty:
                self._refresh_statistics_if_visible(force=True)
        if hasattr(self, "raw_log_tab") and self.tabs.widget(index) is self.raw_log_tab:
            if "raw_log" in self._deferred_views_dirty:
                self._refresh_raw_log_if_visible(force=True)
        if hasattr(self, "flight_notes_tab") and self.tabs.widget(index) is self.flight_notes_tab:
            if "flight_notes" in self._deferred_views_dirty:
                self._refresh_flight_notes_if_visible(force=True)
        if hasattr(self, "gps_tab") and self.tabs.widget(index) is self.gps_tab:
            if not self._gps_view_loaded or "gps" in self._deferred_views_dirty:
                self.refresh_gps(force=True)
            self.gps_view.refresh_viewport(fit=True)

    def populate_gps_color_combo(self) -> None:
        self.gps_color_combo.blockSignals(True)
        current = self.gps_color_combo.currentText()
        self.gps_color_combo.clear()
        self.gps_color_combo.addItem("(none)")
        if self.current_log is not None:
            # Offer every numeric parameter here, not just selected plot
            # channels, so the color ramp can use hidden telemetry fields too.
            for col in self.current_log.parameter_columns:
                self.gps_color_combo.addItem(col)
        if current:
            index = self.gps_color_combo.findText(current)
            if index >= 0:
                self.gps_color_combo.setCurrentIndex(index)
        self.gps_color_combo.blockSignals(False)
        self._populate_gps_range_defaults()
        self._update_gps_range_enabled()

    def set_selected_index(self, index: int, sync_playback_elapsed: bool = True) -> None:
        if self.current_log is None:
            return
        self.selected_index = self._selected_index_in_elapsed_scope(index)
        if sync_playback_elapsed:
            self.gps_playback_elapsed_seconds = self._gps_elapsed_for_index(self.selected_index)
        self.update_info_panel()
        self.graph_view.set_cursor_index(self.selected_index)
        self.sync_gps_cursor()

    def step_selected_index(self, delta: int) -> None:
        self.set_selected_index(self.selected_index + delta)

    def update_info_panel(self) -> None:
        if self.current_log is None:
            self.info_panel.clear()
            return
        cols = self.selected_columns()
        compare = self.compare_log if self.compare_toggle.isChecked() else None
        values = cursor_values(self.current_log, self.selected_index, cols, compare)
        rows = []
        for value in values:
            delta = "" if value.delta is None else f"{value.delta:+.3f}"
            compare_value = "" if value.compare_value is None else f"{value.compare_value}"
            rows.append(
                "<tr>"
                f"<td>{html.escape(value.column)}</td>"
                f"<td>{html.escape(str(value.value))}</td>"
                f"<td>{html.escape(compare_value)}</td>"
                f"<td>{html.escape(delta)}</td>"
                "</tr>"
            )
        self.info_panel.setHtml(
            f"<h3>Cursor row {self.selected_index + 1}</h3>"
            "<table width='100%' cellspacing='0' cellpadding='4'>"
            "<tr><th align='left'>Parameter</th><th align='left'>Primary</th><th align='left'>Compare</th><th align='left'>Delta</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    def toggle_theme(self, *_args) -> None:
        self.dark_mode = self.dark_action.isChecked()
        self._apply_style()
        self.refresh_plots()

    def set_telemetry_interaction_mode(self, mode: str) -> None:
        mode = mode if mode in {"pan", "zoom"} else "zoom"
        self.telemetry_interaction_mode = mode
        self.telemetry_pan_button.setChecked(mode == "pan")
        self.telemetry_zoom_button.setChecked(mode == "zoom")
        self.graph_view.set_interaction_mode(mode)

    def telemetry_time_changed(self, text: str) -> None:
        self.telemetry_time_mode = "relative" if text == "Relative" else "absolute"
        self.refresh_graph()

    def reset_telemetry_view(self) -> None:
        self._telemetry_x_range_generation += 1
        self.telemetry_visible_elapsed_range = None
        self.graph_view.set_x_axis_range(None)
        # Reset Plotly in place. Rebuilding the active WebEngine document here
        # reintroduces the same navigation lifecycle risk as drag-zoom.
        self.graph_view.reset_view()
        self.refresh_gps()
        self.sync_gps_cursor()

    def load_flight_notes(self) -> None:
        if self.current_log is None:
            return
        flight = self.store.get_flight(str(self.current_log.info.path))
        self.flight_notes.setPlainText(flight.get("notes", ""))
        self.flight_video.setText(flight.get("video_path", ""))

    def save_flight_notes(self) -> None:
        if self.current_log is None:
            QMessageBox.information(self, "No log", "Open a log before saving flight notes.")
            return
        self.store.save_flight(
            str(self.current_log.info.path),
            self.current_log.info.model,
            self.flight_notes.toPlainText(),
            self.flight_video.text().strip(),
        )
        self.status.showMessage("Flight notes saved")

    def select_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select linked video", "", "Video files (*.mp4 *.mov *.mkv *.avi);;All files (*.*)")
        if path:
            self.flight_video.setText(path)

    def open_linked_video(self) -> None:
        path = self.flight_video.text().strip()
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def add_battery(self) -> None:
        name = self.battery_name.text().strip()
        if not name:
            return
        self.store.add_battery(name, self.battery_cells.value())
        self.battery_name.clear()
        self.refresh_batteries()

    def refresh_batteries(self) -> None:
        batteries = self.store.list_batteries()
        self.battery_select.clear()
        # Disable sorting while repopulating so row inserts do not keep moving
        # the cursor around mid-update.
        self.battery_table.setSortingEnabled(False)
        self.battery_table.setRowCount(0)
        for battery in batteries:
            self.battery_select.addItem(
                f"{battery['name']} ({battery['cells']}S)",
                {"id": battery["id"], "cells": battery["cells"]},
            )
            row = self.battery_table.rowCount()
            self.battery_table.insertRow(row)
            for col, key in enumerate(["id", "name", "cells", "active"]):
                self.battery_table.setItem(row, col, QTableWidgetItem(str(battery[key])))
        self.battery_table.setSortingEnabled(True)
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        for history in self.store.list_battery_history():
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            values = [
                history["battery_name"],
                history["measured_at"],
                f"{history['pack_milliohm']:.2f}",
                f"{history['cell_milliohm']:.2f}",
                history["health"],
                Path(history["file_path"]).name,
            ]
            for col, value in enumerate(values):
                self.history_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.history_table.setSortingEnabled(True)

    def populate_analysis_combos(self) -> None:
        self.voltage_combo.clear()
        self.current_combo.clear()
        if self.current_log is None:
            return
        for col in find_voltage_columns(self.current_log.parameter_columns):
            self.voltage_combo.addItem(col)
        for col in find_current_columns(self.current_log.parameter_columns):
            self.current_combo.addItem(col)

    def calculate_battery_ir(self) -> None:
        if self.current_log is None:
            return
        battery_data = self.battery_select.currentData()
        if not battery_data:
            QMessageBox.information(self, "No battery", "Add or select a battery first.")
            return
        if isinstance(battery_data, dict):
            battery_id = int(battery_data["id"])
            cells = int(battery_data["cells"])
        else:
            battery_id = int(battery_data)
            cells = None
        voltage = self.voltage_combo.currentText()
        current = self.current_combo.currentText()
        if (
            not voltage
            or not current
            or voltage not in self.current_log.dataframe.columns
            or current not in self.current_log.dataframe.columns
        ):
            QMessageBox.warning(
                self,
                "IR calculation failed",
                "Select valid voltage and current columns before calculating battery IR.",
            )
            return
        result = calculate_internal_resistance(self.current_log.dataframe, voltage, current, cells=cells)
        if result is None:
            QMessageBox.warning(self, "IR calculation failed", "The log does not contain enough voltage/current variation.")
            return
        self.store.add_battery_history(
            battery_id,
            str(self.current_log.info.path),
            result.pack_milliohm,
            result.cell_milliohm,
            result.health,
        )
        self.refresh_batteries()
        QMessageBox.information(
            self,
            "Battery IR",
            f"Pack: {result.pack_milliohm:.2f} mOhm\nCell: {result.cell_milliohm:.2f} mOhm\nHealth: {result.health}",
        )

    def pick_directory(self, line_edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select directory", line_edit.text())
        if path:
            line_edit.setText(path)

    def scan_sync(self, *_args) -> None:
        source = self.sync_source.text().strip()
        target = self.sync_target.text().strip()
        if not source or not target:
            QMessageBox.information(self, "Sync paths", "Select both source and target directories.")
            return
        self.sync_candidates = discover_sync_candidates(source, target)
        self.sync_table.setSortingEnabled(False)
        self.sync_table.setRowCount(0)
        for candidate_index, candidate in enumerate(self.sync_candidates):
            row = self.sync_table.rowCount()
            self.sync_table.insertRow(row)
            values = [candidate.reason, str(candidate.relative_path), str(candidate.source), str(candidate.target)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate_index)
                self.sync_table.setItem(row, col, item)
        self.sync_table.setSortingEnabled(True)
        self.status.showMessage(f"Found {len(self.sync_candidates)} sync candidates")

    def copy_sync_candidates(self) -> None:
        selected_candidates = self._selected_sync_candidates()
        if not selected_candidates:
            QMessageBox.information(self, "No sync candidates selected", "Select one or more candidate rows before copying.")
            return
        copied = copy_candidates(selected_candidates)
        self.status.showMessage(f"Copied {copied} log files")
        if self.sync_target.text().strip():
            self.load_library(Path(self.sync_target.text().strip()))

    def _selected_sync_candidates(self) -> list[SyncCandidate]:
        selection_model = self.sync_table.selectionModel()
        if selection_model is None:
            return []
        selected_rows = sorted({index.row() for index in selection_model.selectedRows()})
        selected: list[SyncCandidate] = []
        for row in selected_rows:
            item = self.sync_table.item(row, 0)
            if item is None:
                continue
            candidate_index = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(candidate_index, int):
                continue
            if 0 <= candidate_index < len(self.sync_candidates):
                selected.append(self.sync_candidates[candidate_index])
        return selected

    def populate_alias_profiles(self, preferred_profile: str | None = None) -> None:
        self.alias_profile.blockSignals(True)
        self.alias_profile.clear()
        profiles = self.store.alias_profiles()
        if not profiles:
            profiles = ["Default"]
        self.alias_profile.addItems(profiles)
        if preferred_profile:
            index = self.alias_profile.findText(preferred_profile)
            if index >= 0:
                self.alias_profile.setCurrentIndex(index)
        self.alias_profile.blockSignals(False)
        self.load_alias_profile(self.alias_profile.currentText())

    def load_alias_profile(self, profile: str) -> None:
        if not profile:
            return
        aliases = self.store.load_aliases(profile)
        if not aliases:
            # Seed a fixed switch matrix so an empty profile still exposes the
            # common radio switch names users are likely to edit.
            aliases = {switch: "" for switch in [f"S{i}" for i in range(1, 13)] + ["SA", "SB", "SC", "SD", "SE", "SF", "SG", "SH"]}
        self.alias_table.setSortingEnabled(False)
        self.alias_table.setRowCount(0)
        for hardware, alias in aliases.items():
            row = self.alias_table.rowCount()
            self.alias_table.insertRow(row)
            self.alias_table.setItem(row, 0, QTableWidgetItem(hardware))
            self.alias_table.setItem(row, 1, QTableWidgetItem(alias))
        self.alias_table.setSortingEnabled(True)

    def save_alias_profile(self) -> None:
        profile = self.alias_profile.currentText().strip() or "Default"
        aliases: dict[str, str] = {}
        for row in range(self.alias_table.rowCount()):
            hardware = self.alias_table.item(row, 0)
            alias = self.alias_table.item(row, 1)
            if hardware:
                aliases[hardware.text()] = alias.text() if alias else ""
        self.store.save_aliases(profile, aliases)
        self.populate_alias_profiles(profile)
        self.status.showMessage(f"Saved alias profile {profile}")

    def add_voice_item(self) -> None:
        self.voice_table.setSortingEnabled(False)
        row = self.voice_table.rowCount()
        self.voice_table.insertRow(row)
        self.voice_table.setItem(row, 0, QTableWidgetItem(""))
        self.voice_table.setItem(row, 1, QTableWidgetItem(""))
        self.voice_table.setSortingEnabled(True)

    def voice_items_from_table(self) -> list[VoiceItem]:
        items: list[VoiceItem] = []
        for row in range(self.voice_table.rowCount()):
            text = self.voice_table.item(row, 0)
            filename = self.voice_table.item(row, 1)
            if text and filename and text.text().strip() and filename.text().strip():
                # Ignore draft rows so partially entered voice-pack entries do
                # not produce empty output files.
                items.append(VoiceItem(text.text().strip(), filename.text().strip()))
        return items

    def open_voice_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open voice CSV", "", "CSV files (*.csv);;All files (*.*)")
        if not path:
            return
        self.voice_items = load_voice_csv(path)
        self.voice_table.setSortingEnabled(False)
        self.voice_table.setRowCount(0)
        for item in self.voice_items:
            row = self.voice_table.rowCount()
            self.voice_table.insertRow(row)
            self.voice_table.setItem(row, 0, QTableWidgetItem(item.text))
            self.voice_table.setItem(row, 1, QTableWidgetItem(item.filename))
        self.voice_table.setSortingEnabled(True)

    def save_voice_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save voice CSV", "audio_list.csv", "CSV files (*.csv)")
        if path:
            save_voice_csv(path, self.voice_items_from_table())

    def generate_voice_pack(self, *_args) -> None:
        items = self.voice_items_from_table()
        if not items:
            QMessageBox.information(self, "Voice pack", "Add at least one text/filename row.")
            return
        path = QFileDialog.getExistingDirectory(self, "Select output directory")
        if not path:
            return
        try:
            created = generate_voice_pack(items, path)
        except ValueError as exc:
            QMessageBox.warning(self, "Voice pack", str(exc))
            return
        self.status.showMessage(f"Generated {len(created)} WAV files")

    def about(self) -> None:
        QMessageBox.about(
            self,
            "About Sloppy Log Explorer",
            _about_text(),
        )

    def _sort_library_tree(self, column: int, order: Qt.SortOrder) -> None:
        _my_library_tree = self.library_tree
        if _my_library_tree is None:
            raise RuntimeError("Library tree widget not found for sorting")
        items: list[QTreeWidgetItem] = []
        for index in range(_my_library_tree.topLevelItemCount()):
            item = _my_library_tree.topLevelItem(index)
            if item is None:
                raise RuntimeError("Failed to retrieve library tree items for sorting")
            items.append(item)
        # Capture expansion state before we rebuild the tree, otherwise Qt will
        # collapse everything when the items are reinserted.
        expanded_state = {id(item): item.isExpanded() for item in items}
        current_item = _my_library_tree.currentItem()
        current_column = _my_library_tree.currentColumn()
        selected_items = _my_library_tree.selectedItems()
        while _my_library_tree.topLevelItemCount():
            _my_library_tree.takeTopLevelItem(0)

        reverse = order == Qt.SortOrder.DescendingOrder
        items.sort(key=lambda item: self._library_sort_key(item, column), reverse=reverse)
        self.library_sort_column = column
        self.library_sort_order = order

        _my_library_tree.setUpdatesEnabled(False)
        try:
            for index, item in enumerate(items):
                _my_library_tree.insertTopLevelItem(index, item)
                self._sort_library_children(item, column, order)
            for item in items:
                item.setExpanded(expanded_state.get(id(item), False))
            if current_item is not None:
                _my_library_tree.setCurrentItem(
                    current_item,
                    current_column,
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
            for selected_item in selected_items:
                selected_item.setSelected(True)
        finally:
            _my_library_tree.setUpdatesEnabled(True)

        header = _my_library_tree.header()
        if header is None:
            raise RuntimeError("Failed to get library tree header for sort indicator")
        header.setSortIndicator(column, order)

    def _sort_library_children(self, item: QTreeWidgetItem, column: int, order: Qt.SortOrder) -> None:
        children: list[QTreeWidgetItem] = []
        for index in range(item.childCount()):
            child = item.child(index)
            if child is None:
                raise RuntimeError("Failed to retrieve library tree child items for sorting")
            children.append(child)
        expanded_state = {id(child): child.isExpanded() for child in children}
        while item.childCount():
            item.takeChild(0)

        reverse = order == Qt.SortOrder.DescendingOrder
        children.sort(key=lambda child: self._library_sort_key(child, column, child_rows=True), reverse=reverse)
        for index, child in enumerate(children):
            item.insertChild(index, child)
            self._sort_library_children(child, column, order)
        for child in children:
            child.setExpanded(expanded_state.get(id(child), False))

    def _library_sort_key(self, item: QTreeWidgetItem, column: int, child_rows: bool = False) -> tuple[object, object]:
        label = str(item.text(0))
        label_key = self._natural_library_text_key(label)
        if column == 1:
            # Model rows sort by their stored counts, while child rows fall back
            # to the label because the count column is intentionally blank there.
            primary = label_key if child_rows else int(item.data(1, Qt.ItemDataRole.UserRole) or 0)
        elif column == 2:
            primary = float(item.data(2, Qt.ItemDataRole.UserRole) or 0.0)
        elif column == 3:
            primary = int(item.data(3, Qt.ItemDataRole.UserRole) or 0)
        else:
            primary = label_key
        return (primary, label_key)

    @staticmethod
    def _natural_library_text_key(value: str) -> tuple[tuple[int, object], ...]:
        return tuple(
            (1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", value)
            if part
        )

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_size(size: int) -> str:
        return f"{size / (1024 * 1024):.1f} MB"

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        self.store.close()
        super().closeEvent(a0)
