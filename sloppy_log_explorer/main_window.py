from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget
)

from .analysis import (
    calculate_internal_resistance,
    cursor_values,
    find_current_columns,
    find_voltage_columns,
    suggest_display_columns,
)
from .library import group_by_model, scan_library
from .models import GpsGradientOptions, LibraryLogInfo, LoadedLog, SyncCandidate
from .parser import load_log
from .qt_plot import GpsPathWidget, TelemetryPlotWidget
from .storage import AppStore
from .sync import copy_candidates, discover_sync_candidates
from .voice import VoiceItem, generate_voice_pack, load_voice_csv, save_voice_csv


class MainWindow(QMainWindow):
    library_sort_column = 2

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
        self.telemetry_interaction_mode = "pan"
        self.telemetry_time_mode = "absolute"
        self.selected_parameter_columns: set[str] = set()
        self.gps_start_color = GpsGradientOptions.start_color
        self.gps_end_color = GpsGradientOptions.end_color
        self.sync_candidates: list[SyncCandidate] = []
        self.voice_items: list[VoiceItem] = []

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
        header = self.library_tree.header()
        if header is None:
            raise RuntimeError("Failed to get library tree header")
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.library_header_clicked)
        self.library_tree.setSortingEnabled(False)
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
        self.telemetry_pan_button.setChecked(True)
        self.telemetry_pan_button.clicked.connect(lambda: self.set_telemetry_interaction_mode("pan"))
        self.telemetry_zoom_button = QPushButton("Zoom")
        self.telemetry_zoom_button.setCheckable(True)
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
        layout.addWidget(self.graph_view, 5)
        self.info_panel = QTextEdit()
        self.info_panel.setReadOnly(True)
        self.info_panel.setMaximumHeight(190)
        layout.addWidget(self.info_panel, 1)
        self.tabs.addTab(tab, "Telemetry")
        self._set_empty_graph()

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

        self.gps_view = GpsPathWidget()
        layout.addWidget(self.gps_view, 1)
        self.gps_tab = tab
        self.tabs.addTab(tab, "Flight Map")
        self._update_gps_color_buttons()
        self._update_gps_range_enabled()
        self.gps_view.set_path(None, dark=self.dark_mode)

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
        )

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
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #20242b; color: #e5e7eb; }
            QMenuBar, QMenu, QStatusBar { background: #181b20; color: #e5e7eb; }
            QPushButton { background: #2f80ed; color: white; border: 0; border-radius: 4px; padding: 7px 10px; }
            QPushButton:hover { background: #3f8df2; }
            QPushButton:checked { background: #185fc7; border: 1px solid #87b7ff; padding: 6px 9px; }
            QLineEdit, QTextEdit, QComboBox, QTreeWidget, QTableWidget {
                background: #15181d; color: #e5e7eb; border: 1px solid #3a414d; border-radius: 4px;
            }
            QHeaderView::section { background: #2b3038; color: #e5e7eb; padding: 5px; border: 0; }
            QTabWidget::pane { border: 1px solid #333a44; }
            QTabBar::tab { background: #242932; color: #e5e7eb; padding: 8px 14px; }
            QTabBar::tab:selected { background: #2f80ed; color: white; }
            QLabel#summary { color: #aab2c0; font-size: 12px; }
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

    def open_log_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open telemetry log", "", "Telemetry logs (*.csv *.log);;All files (*.*)")
        if path:
            self.load_log(Path(path))

    def open_compare_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open compare telemetry log", "", "Telemetry logs (*.csv *.log);;All files (*.*)")
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
        self._sort_library_tree(self.library_sort_column, Qt.SortOrder.DescendingOrder)
        self.library_tree.collapseAll()

    def library_item_activated(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.load_log(Path(path))

    def library_header_clicked(self, column: int) -> None:
        header = self.library_tree.header()
        if header is None:
            return
        current_column = header.sortIndicatorSection()
        current_order = header.sortIndicatorOrder()
        if current_column == column:
            order = Qt.SortOrder.AscendingOrder if current_order == Qt.SortOrder.DescendingOrder else Qt.SortOrder.DescendingOrder
        else:
            order = Qt.SortOrder.DescendingOrder if column in {1, 2, 3} else Qt.SortOrder.AscendingOrder
        self._sort_library_tree(column, order)

    def load_log(self, path: Path) -> None:
        try:
            self.current_log = load_log(path, self.library_root)
            self.selected_index = 0
            self.store.set_setting("last_log", str(path))
            # Every dependent widget needs a refresh because a new log changes
            # the available columns, GPS choices, and saved notes target.
            self.initialize_selected_parameters()
            self.populate_columns()
            self.populate_gps_color_combo()
            self.populate_analysis_combos()
            self.load_flight_notes()
            self.refresh_plots()
            self.status.showMessage(f"Loaded {path.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Log load failed", str(exc))

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

    def selected_columns(self) -> list[str]:
        if self.current_log is None:
            return []
        return [col for col in self.current_log.parameter_columns if col in self.selected_parameter_columns]

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
        self.store.set_setting("selected_columns", cols)
        # The GPS color picker depends on the visible parameter list, so refresh
        # it before repainting the plots.
        self.populate_gps_color_combo()
        self.refresh_plots()

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
        )

    def refresh_gps(self, *_args) -> None:
        self.gps_view.set_path(
            self.current_log,
            options=self._gps_gradient_options(),
            dark=self.dark_mode,
        )

    def tab_changed(self, index: int) -> None:
        if hasattr(self, "gps_tab") and self.tabs.widget(index) is self.gps_tab:
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

    def set_selected_index(self, index: int) -> None:
        if self.current_log is None:
            return
        self.selected_index = max(0, min(index, len(self.current_log.dataframe) - 1))
        self.update_info_panel()
        self.refresh_graph()

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
        self.refresh_plots()

    def set_telemetry_interaction_mode(self, mode: str) -> None:
        self.telemetry_interaction_mode = mode
        self.telemetry_pan_button.setChecked(mode == "pan")
        self.telemetry_zoom_button.setChecked(mode == "zoom")
        self.graph_view.set_interaction_mode(mode)

    def telemetry_time_changed(self, text: str) -> None:
        self.telemetry_time_mode = "relative" if text == "Relative" else "absolute"
        self.refresh_graph()

    def reset_telemetry_view(self) -> None:
        if hasattr(self, "graph_view"):
            self.graph_view.reset_view()

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
            self.battery_select.addItem(f"{battery['name']} ({battery['cells']}S)", battery["id"])
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
        battery_id = self.battery_select.currentData()
        if not battery_id:
            QMessageBox.information(self, "No battery", "Add or select a battery first.")
            return
        voltage = self.voltage_combo.currentText()
        current = self.current_combo.currentText()
        result = calculate_internal_resistance(self.current_log.dataframe, voltage, current)
        if result is None:
            QMessageBox.warning(self, "IR calculation failed", "The log does not contain enough voltage/current variation.")
            return
        self.store.add_battery_history(
            int(battery_id),
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
        for candidate in self.sync_candidates:
            row = self.sync_table.rowCount()
            self.sync_table.insertRow(row)
            values = [candidate.reason, str(candidate.relative_path), str(candidate.source), str(candidate.target)]
            for col, value in enumerate(values):
                self.sync_table.setItem(row, col, QTableWidgetItem(value))
        self.sync_table.setSortingEnabled(True)
        self.status.showMessage(f"Found {len(self.sync_candidates)} sync candidates")

    def copy_sync_candidates(self) -> None:
        copied = copy_candidates(self.sync_candidates)
        self.status.showMessage(f"Copied {copied} log files")
        if self.sync_target.text().strip():
            self.load_library(Path(self.sync_target.text().strip()))

    def populate_alias_profiles(self) -> None:
        self.alias_profile.blockSignals(True)
        self.alias_profile.clear()
        profiles = self.store.alias_profiles()
        if not profiles:
            profiles = ["Default"]
        self.alias_profile.addItems(profiles)
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
        self.populate_alias_profiles()
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
        created = generate_voice_pack(items, path)
        self.status.showMessage(f"Generated {len(created)} WAV files")

    def about(self) -> None:
        QMessageBox.about(
            self,
            "About Sloppy Log Explorer",
            "Sloppy Log Explorer\n\nGPL-3.0-or-later telemetry log explorer for Ethos and OpenTX CSV logs.\nDerived from Ethos_LogView concepts with attribution in NOTICE.md.",
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
        while _my_library_tree.topLevelItemCount():
            _my_library_tree.takeTopLevelItem(0)

        reverse = order == Qt.SortOrder.DescendingOrder
        items.sort(key=lambda item: self._library_sort_key(item, column), reverse=reverse)

        _my_library_tree.setUpdatesEnabled(False)
        try:
            for index, item in enumerate(items):
                _my_library_tree.insertTopLevelItem(index, item)
                self._sort_library_children(item, column, order)
            for item in items:
                item.setExpanded(expanded_state.get(id(item), False))
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

    def _library_sort_key(self, item: QTreeWidgetItem, column: int, child_rows: bool = False) -> tuple[object, str]:
        label = str(item.text(0)).casefold()
        if column == 1:
            # Model rows sort by their stored counts, while child rows fall back
            # to the label because the count column is intentionally blank there.
            primary = label if child_rows else int(item.data(1, Qt.ItemDataRole.UserRole) or 0)
        elif column == 2:
            primary = float(item.data(2, Qt.ItemDataRole.UserRole) or 0.0)
        elif column == 3:
            primary = int(item.data(3, Qt.ItemDataRole.UserRole) or 0)
        else:
            primary = label
        return (primary, label)

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_size(size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024.0
        raise ValueError("Size value is too large to format")

    def closeEvent(self, event) -> None:
        self.store.close()
        super().closeEvent(event)
