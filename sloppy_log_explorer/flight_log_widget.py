"""Reviewed flight-log generation, pilot notes, and optional Google Sheets UI."""

from __future__ import annotations

import copy
import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTextEdit, QToolButton,
    QVBoxLayout, QWidget, QWizard, QWizardPage,
)

from .flight_logs import FlightLogDocument, FlightLogPreview, FlightLogService, infer_aircraft
from .flight_log_sheets import FlightLogSheet, GoogleSheetsClient, GoogleSheetsError, SheetsSyncReport, Workbook, default_google_oauth_client, google_credential_config, stable_flight_id, workbook_url
from .models import LibraryLogInfo
from .storage import AppStore, app_data_dir


_DEFAULT_POLICY = {
    "max_sensors": [], "exclude_sensors": [], "auto_vfr": True,
    "auto_temperatures": True,
    "auto_servo_currents": True,
}


class _FlightLogTask(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, work: Callable[[], object], parent: QWidget):
        super().__init__(parent)
        self.work = work

    def run(self) -> None:
        try:
            result = self.work()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


def _readonly_item(value: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _header(table: QTableWidget) -> QHeaderView:
    return cast(QHeaderView, table.horizontalHeader())


def _item(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    return cast(QTableWidgetItem, table.item(row, column))


def _review_text(report: dict[str, Any]) -> str:
    """Render all review fields, including notes at risk, without hiding errors."""
    lines: list[str] = []

    def render(value: object, indent: int = 0, exact_labels: bool = False) -> None:
        prefix = "  " * indent
        if isinstance(value, dict):
            priority = ["files_scanned", "accepted", "rejected", "pending", "rows_added", "duplicates", "late_conflicts", "errors", "updates"]
            keys = [key for key in priority if key in value] + [key for key in value if key not in priority]
            for key in keys:
                child = value[key]
                if key in {"available_headers", "id", "candidate_id", "pending_candidates", "dry_run", "vfr_minimums", "vfr_present", "temperature_maximums", "sensor_maximums"} or (key == "plane" and "aircraft" in value):
                    continue
                raw_label = str(key) if exact_labels else str(key).replace("_", " ")
                label = raw_label[:1].upper() + raw_label[1:]
                if isinstance(child, (dict, list, tuple)):
                    lines.append(f"{prefix}{label}:")
                    render(child, indent + 1, exact_labels=key == "metrics")
                else:
                    lines.append(f"{prefix}{label}: {child}")
        elif isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{prefix}None")
            for child in value:
                if isinstance(child, (dict, list, tuple)):
                    lines.append(f"{prefix}•")
                    render(child, indent + 1)
                else:
                    lines.append(f"{prefix}• {child}")
        else:
            lines.append(f"{prefix}{value}")

    render(report)
    return "\n".join(lines)


class GoogleSignInSetupDialog(QWizard):
    """Walk a personal-account owner through Google's required app registration."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Set up Google sign-in — personal account")
        self.resize(650, 480)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        steps = [
            ("1. Create a project", "Use your personal Gmail account",
             '<p>Google requires a project to identify this app. A Google Workspace account or organization is not required.</p>'
             '<ol><li>Open the link below and sign in with your Gmail account.</li><li>Choose <b>Create project</b> and name it <b>Sloppy Log Explorer</b>.</li><li>Select that same project throughout the next steps.</li></ol>'
             '<p><a href="https://console.cloud.google.com/projectcreate">Create a Google Cloud project</a></p>'),
            ("2. Enable Google Sheets", "Allow this project to use the Sheets API",
             '<p>Open the link below. Check the project selector at the top, then click <b>Enable</b>.</p>'
             '<p><a href="https://console.cloud.google.com/apis/library/sheets.googleapis.com">Enable Google Sheets API</a></p>'),
            ("3. Allow your Gmail account", "Choose External and add yourself as a test user",
             '<ol><li>Open <b>Google Auth platform → Branding</b> and choose <b>Get started</b>.</li><li>Use <b>Sloppy Log Explorer</b> as the app name and your Gmail address for support and contact email.</li><li>For <b>Audience</b>, choose <b>External</b>.</li><li>Under <b>Audience → Test users → Add users</b>, add your Gmail address.</li><li>Under <b>Data Access → Add or remove scopes</b>, add <b>https://www.googleapis.com/auth/spreadsheets</b> and save.</li></ol>'
             '<p><a href="https://console.cloud.google.com/auth/branding">Open Google Auth platform</a></p>'
             '<p>While the project is in Testing, Google may ask you to sign in again after seven days.</p>'),
            ("4. Download the app client", "Create a Desktop app client, then download its JSON",
             '<ol><li>Open <b>Google Auth platform → Clients</b> and click <b>Create client</b>.</li><li>Choose <b>Desktop app</b> as the application type.</li><li>Name it <b>Sloppy Log Explorer desktop</b> and click <b>Create</b>.</li><li>Open the client details and choose <b>Download JSON</b>. Keep this file on your computer.</li></ol>'
             '<p><a href="https://console.cloud.google.com/auth/clients">Open OAuth clients</a></p>'),
        ]
        for title, subtitle, instructions in steps:
            page = QWizardPage()
            page.setTitle(title)
            page.setSubTitle(subtitle)
            layout = QVBoxLayout(page)
            text = QTextBrowser()
            text.setOpenExternalLinks(True)
            text.setHtml(instructions)
            layout.addWidget(text)
            self.addPage(page)
        page = QWizardPage()
        page.setTitle("5. Finish setup")
        page.setSubTitle("Choose the downloaded file once, then sign in through your browser")
        layout = QVBoxLayout(page)
        hint = QLabel("Select the Desktop app JSON you downloaded. After Finish, choose Sign in with Google and approve Sheets access using your Gmail account.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.client_path_edit = QLineEdit()
        self.client_path_edit.setReadOnly(True)
        page.registerField("clientPath*", self.client_path_edit)
        layout.addWidget(self.client_path_edit)
        self.import_button = QPushButton("Choose downloaded JSON…")
        self.import_button.clicked.connect(self._choose_client)
        layout.addWidget(self.import_button)
        layout.addStretch(1)
        self.addPage(page)

    def _choose_client(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose downloaded Desktop OAuth client", "", "JSON files (*.json)")
        if not filename:
            return
        try:
            config = google_credential_config(filename)
            installed = config.get("installed", {})
            if not installed.get("client_id") or not installed.get("client_secret"):
                raise GoogleSheetsError("Choose an OAuth client of type Desktop app. A service account or Web application client cannot sign in with your Gmail account here.")
        except GoogleSheetsError as exc:
            QMessageBox.warning(self, "Google sign-in setup", str(exc))
            return
        self.client_path_edit.setText(filename)


class FlightLogWidget(QWidget):
    """One workspace for local log maintenance and a deliberate cloud export."""

    library_requested = pyqtSignal()

    def __init__(self, store: AppStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.output_dir = Path(store.get_setting("flight_logs.output_dir", str(app_data_dir() / "flight-logs")))
        self.service = FlightLogService(self.output_dir)
        self._settings_error = ""
        try:
            self.settings = self.service.load_settings()
        except (OSError, ValueError) as exc:
            self.settings = {"defaults": copy.deepcopy(_DEFAULT_POLICY), "planes": {}}
            self._settings_error = str(exc)
        self._logs: list[LibraryLogInfo] = []
        self._root: Path | None = None
        self._preview: FlightLogPreview | None = None
        self._decisions: dict[str, str] = {}
        self._headers: dict[str, list[str]] = {}
        self._document: FlightLogDocument | None = None
        self._note_row = -1
        self._note_original = ""
        self._log_aircraft = ""
        self._revision = 0
        self._busy = False
        self._populating = False
        self._closing = False
        self._tasks: list[_FlightLogTask] = []
        self._callback: Callable[[object], None] | None = None
        self._client: GoogleSheetsClient | None = None
        self._client_credentials = ""
        self._saved_decisions = self.store.get_setting("flight_logs.candidate_decisions", {}).get(str(self.output_dir), {})
        self._aircraft_mapping: dict[Path, str] = {}
        self._sheets_local_documents: dict[str, FlightLogDocument] = {}
        self._remote_notes: dict[str, dict[str, str]] = {}
        self._sheets_review: list[FlightLogSheet] = []
        self._policy_scope = ""
        self._policy_original: dict[str, Any] | None = None
        self._displaying_columns = False
        self._build_ui()
        self._refresh_log_list()
        self._load_policy()
        self._update_controls()
        if self._settings_error:
            self.status_label.setText(f"Flight-log settings could not be read: {self._settings_error}. Correct that file or choose another output folder before generation.")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        explanation = QLabel("Add flights from your telemetry library, keep pilot notes, and optionally share a Google Sheets view.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Flight-log folder:"))
        self.output_edit = QLineEdit(str(self.output_dir))
        self.output_edit.setReadOnly(True)
        output_row.addWidget(self.output_edit, 1)
        self.output_button = QPushButton("Choose folder…")
        self.output_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.output_button)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        output_row.addWidget(self.open_folder_button)
        layout.addLayout(output_row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_generate()
        self._build_logs()
        self._build_telemetry()
        self._build_sheets()
        self.status_label = QLabel("Choose a telemetry library, then preview the selected aircraft.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _build_generate(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        split = QSplitter()
        aircraft_panel = QWidget()
        left = QVBoxLayout(aircraft_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("1. Choose aircraft"))
        self.open_library_button = QPushButton("Open library…")
        self.open_library_button.setToolTip("Choose the folder containing your aircraft telemetry recordings.")
        self.open_library_button.clicked.connect(self.library_requested.emit)
        left.addWidget(self.open_library_button)
        self.aircraft_list = QListWidget()
        self.aircraft_list.itemChanged.connect(self._selection_changed)
        left.addWidget(self.aircraft_list)
        select = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.select_none_button = QPushButton("Select none")
        self.select_all_button.clicked.connect(lambda: self._select_aircraft(True))
        self.select_none_button.clicked.connect(lambda: self._select_aircraft(False))
        select.addWidget(self.select_all_button)
        select.addWidget(self.select_none_button)
        left.addLayout(select)
        self.mapping_button = QPushButton("Source aircraft…")
        self.mapping_button.setToolTip("Review and correct the aircraft assigned to each telemetry source file.")
        self.mapping_button.clicked.connect(self._review_source_mapping)
        left.addWidget(self.mapping_button)
        split.addWidget(aircraft_panel)
        review_panel = QWidget()
        right = QVBoxLayout(review_panel)
        right.setContentsMargins(0, 0, 0, 0)
        self.workflow_label = QLabel("2. Review flights  →  3. Save to your flight logs")
        self.workflow_label.setWordWrap(True)
        right.addWidget(self.workflow_label)
        hint = QLabel("Confirmed flights are included automatically. Decide only the flights marked Needs review, then save. Your existing flights and pilot notes are kept.")
        hint.setWordWrap(True)
        right.addWidget(hint)
        buttons = QGridLayout()
        self.preview_button = QPushButton("Review flights")
        self.preview_button.clicked.connect(self.preview_selected)
        self.generate_button = QPushButton("Save reviewed flights")
        self.generate_button.clicked.connect(self.generate_logs)
        self.rebuild_button = QPushButton("Review rebuild…")
        self.rebuild_button.setToolTip("Preview replacing generated rows, changing columns, and renumbering flights. Requires a separate review and approval.")
        self.rebuild_button.clicked.connect(self.preview_rebuild)
        buttons.addWidget(self.preview_button, 0, 0)
        buttons.addWidget(self.generate_button, 0, 1)
        right.addLayout(buttons)
        self.preview_summary = QLabel("No preview yet.")
        self.preview_summary.setWordWrap(True)
        right.addWidget(self.preview_summary)
        self.candidate_table = QTableWidget(0, 7)
        self.candidate_table.setHorizontalHeaderLabels(["Aircraft", "Source", "Start", "Duration", "Detection", "Include?", "Why"])
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        _header(self.candidate_table).setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _header(self.candidate_table).setStretchLastSection(True)
        self.candidate_table.itemSelectionChanged.connect(self._show_candidate)
        review_actions = QGridLayout()
        self.pending_only_check = QCheckBox("Needs review only")
        self.pending_only_check.toggled.connect(self._filter_candidates)
        review_actions.addWidget(self.pending_only_check, 0, 0)
        self.accept_selected_button = QPushButton("Accept selected")
        self.reject_selected_button = QPushButton("Reject selected")
        self.next_pending_button = QPushButton("Next to review")
        self.accept_selected_button.clicked.connect(lambda: self._decide_selected("Accept"))
        self.reject_selected_button.clicked.connect(lambda: self._decide_selected("Reject"))
        self.next_pending_button.clicked.connect(self._next_pending)
        review_actions.addWidget(self.next_pending_button, 0, 1)
        review_actions.addWidget(self.accept_selected_button, 1, 0)
        review_actions.addWidget(self.reject_selected_button, 1, 1)
        review_actions.setColumnStretch(2, 1)
        right.addLayout(review_actions)
        self.review_tabs = QTabWidget()
        self.review_tabs.addTab(self.candidate_table, "Detected flights")
        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Aircraft:"))
        self.preview_aircraft_combo = QComboBox()
        self.preview_aircraft_combo.currentTextChanged.connect(self._render_output_preview)
        output_row.addWidget(self.preview_aircraft_combo, 1)
        output_layout.addLayout(output_row)
        self.preview_columns_hint = QLabel("Drag the column headings to choose the order before saving a new log.")
        self.preview_columns_hint.setWordWrap(True)
        output_layout.addWidget(self.preview_columns_hint)
        self.output_preview_table = QTableWidget()
        _header(self.output_preview_table).setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _header(self.output_preview_table).setSectionsMovable(True)
        _header(self.output_preview_table).sectionMoved.connect(self._preview_column_moved)
        output_layout.addWidget(self.output_preview_table)
        self.review_tabs.addTab(output_page, "Flight-log preview")
        right.addWidget(self.review_tabs, 3)
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Detection details and warnings")
        self.details_toggle.setCheckable(True)
        right.addWidget(self.details_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.preview_details = QTextEdit()
        self.preview_details.setReadOnly(True)
        self.preview_details.setPlaceholderText("Detection evidence, metrics, ground rejections, errors, and late-flight conflicts appear here.")
        self.preview_details.setVisible(False)
        self.details_toggle.toggled.connect(self.preview_details.setVisible)
        right.addWidget(self.preview_details, 1)
        right.addWidget(self.rebuild_button, alignment=Qt.AlignmentFlag.AlignLeft)
        split.addWidget(review_panel)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 850])
        layout.addWidget(split)
        self.tabs.addTab(page, "Add flights")

    def _build_logs(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("Aircraft:"))
        self.log_aircraft_combo = QComboBox()
        self.log_aircraft_combo.currentTextChanged.connect(self._load_document)
        row.addWidget(self.log_aircraft_combo, 1)
        self.refresh_logs_button = QPushButton("Refresh logs")
        self.refresh_logs_button.clicked.connect(self._refresh_log_list)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self._export_csv)
        self.open_log_button = QPushButton("Open Markdown")
        self.open_log_button.clicked.connect(self._open_log)
        row.addWidget(self.refresh_logs_button)
        row.addWidget(self.export_button)
        row.addWidget(self.open_log_button)
        layout.addLayout(row)
        self.log_path_label = QLabel("No flight log selected.")
        self.log_path_label.setWordWrap(True)
        layout.addWidget(self.log_path_label)
        columns_hint = QLabel("Drag column headings to arrange your log, then choose Save column order. The same order is used in Markdown, CSV and Google Sheets.")
        columns_hint.setWordWrap(True)
        layout.addWidget(columns_hint)
        self.log_table = QTableWidget()
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        _header(self.log_table).setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _header(self.log_table).setSectionsMovable(True)
        _header(self.log_table).sectionMoved.connect(self._update_controls)
        self.log_table.itemSelectionChanged.connect(self._show_notes)
        layout.addWidget(self.log_table, 2)
        layout.addWidget(QLabel("Pilot Notes for the selected flight:"))
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Select a flight, enter pilot notes, and save.")
        self.notes_edit.textChanged.connect(self._update_controls)
        layout.addWidget(self.notes_edit, 1)
        self.save_notes_button = QPushButton("Save pilot notes")
        self.save_notes_button.clicked.connect(self.save_notes)
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_notes_button)
        self.save_columns_button = QPushButton("Save column order")
        self.save_columns_button.clicked.connect(self.save_column_order)
        save_row.addWidget(self.save_columns_button)
        save_row.addStretch(1)
        layout.addLayout(save_row)
        self.tabs.addTab(page, "Flight logs")

    def _build_telemetry(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel("Choose exact source headers and units. Maxima use samples inside each accepted flight. Exclusions take priority. Existing log columns change only through a reviewed rebuild.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("Settings for:"))
        self.policy_aircraft_combo = QComboBox()
        self.policy_aircraft_combo.addItem("Defaults for all aircraft", "")
        self.policy_aircraft_combo.currentIndexChanged.connect(self._load_policy)
        row.addWidget(self.policy_aircraft_combo, 1)
        self.discover_button = QPushButton("Load source telemetry items")
        self.discover_button.clicked.connect(self._discover_sensors)
        row.addWidget(self.discover_button)
        layout.addLayout(row)
        self.inherit_check = QCheckBox("Use default settings for this aircraft")
        self.inherit_check.toggled.connect(self._policy_controls)
        layout.addWidget(self.inherit_check)
        automatic = QVBoxLayout()
        self.auto_vfr_check = QCheckBox("Automatically capture present VFR families")
        self.auto_temperature_check = QCheckBox("Automatically capture valid temperature maxima")
        self.auto_servo_current_check = QCheckBox("Capture paired servo-current maxima")
        self.auto_servo_current_check.setToolTip("Match each named A/mA current channel to a temperature channel with the same name. Known ESC, BEC, battery, receiver and motor names are excluded. Exact source headers and units are kept; individual exclusions still win.")
        automatic.addWidget(self.auto_vfr_check)
        automatic.addWidget(self.auto_temperature_check)
        automatic.addWidget(self.auto_servo_current_check)
        layout.addLayout(automatic)
        self.sensor_table = QTableWidget(0, 3)
        self.sensor_table.setHorizontalHeaderLabels(["Exact source telemetry header", "Extra maximum", "Exclude"])
        _header(self.sensor_table).setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _header(self.sensor_table).setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        _header(self.sensor_table).setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.sensor_table, 1)
        custom = QHBoxLayout()
        self.custom_sensor_edit = QLineEdit()
        self.custom_sensor_edit.setPlaceholderText("Add an exact header, e.g. Current(A)")
        self.add_sensor_button = QPushButton("Add item")
        self.add_sensor_button.clicked.connect(self._add_sensor)
        custom.addWidget(self.custom_sensor_edit, 1)
        custom.addWidget(self.add_sensor_button)
        layout.addLayout(custom)
        self.save_policy_button = QPushButton("Save telemetry settings")
        self.save_policy_button.clicked.connect(self.save_telemetry_settings)
        layout.addWidget(self.save_policy_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(page, "Telemetry")

    def _build_sheets(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        privacy = QLabel("Use your personal Google account to keep a Sheets copy of your flight logs. Sync shares aircraft names, flight summaries and pilot Notes. Raw telemetry and GPS tracks stay on this computer.")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addWidget(QLabel("1. Connect your Google account"))
        self.google_account_label = QLabel("Not connected. Personal Gmail accounts are supported.")
        self.google_account_label.setWordWrap(True)
        layout.addWidget(self.google_account_label)
        connection_row = QHBoxLayout()
        self.sign_in_button = QPushButton("Sign in with Google")
        self.sign_in_button.clicked.connect(self.sign_in_google)
        self.setup_google_button = QPushButton("One-time setup guide…")
        self.setup_google_button.clicked.connect(self._setup_google)
        self.disconnect_google_button = QPushButton("Disconnect")
        self.disconnect_google_button.clicked.connect(self.disconnect_google)
        for button in (self.sign_in_button, self.setup_google_button, self.disconnect_google_button):
            connection_row.addWidget(button)
        connection_row.addStretch(1)
        layout.addLayout(connection_row)
        self.remember_google_check = QCheckBox("Remember sign-in securely on this Windows account")
        self.remember_google_check.setChecked(bool(self.store.get_setting("flight_logs.sheets.remember", True)))
        self._remember_google = self.remember_google_check.isChecked()
        layout.addWidget(self.remember_google_check)
        self.credentials_toggle = QToolButton()
        self.credentials_toggle.setText("Advanced connection settings")
        self.credentials_toggle.setCheckable(True)
        layout.addWidget(self.credentials_toggle)
        self.credentials_panel = QWidget()
        credential_form = QFormLayout(self.credentials_panel)
        form = QFormLayout()
        credentials_row = QHBoxLayout()
        self.credentials_edit = QLineEdit(str(self.store.get_setting("flight_logs.sheets.credentials", "")) or default_google_oauth_client())
        self.credentials_edit.setPlaceholderText("Desktop OAuth client JSON (service account optional)")
        self.credentials_button = QPushButton("Choose JSON…")
        self.credentials_button.clicked.connect(self._choose_credentials)
        credentials_row.addWidget(self.credentials_edit, 1)
        credentials_row.addWidget(self.credentials_button)
        credential_form.addRow("App client:", credentials_row)
        self.credentials_panel.setVisible(False)
        self.credentials_toggle.toggled.connect(self.credentials_panel.setVisible)
        layout.addWidget(self.credentials_panel)
        layout.addWidget(QLabel("2. Choose a workbook"))
        self.workbook_title_edit = QLineEdit(str(self.store.get_setting("flight_logs.sheets.title", "RC flight logs")))
        self.workbook_edit = QLineEdit(str(self.store.get_setting("flight_logs.sheets.workbook", "")))
        self.workbook_edit.setPlaceholderText("Existing Google Sheets workbook URL or ID")
        form.addRow("New workbook title:", self.workbook_title_edit)
        form.addRow("Linked workbook:", self.workbook_edit)
        layout.addLayout(form)
        auth_hint = QLabel("Create a workbook for your logs, or paste an existing workbook link. Google sign-in opens in your normal browser.")
        auth_hint.setWordWrap(True)
        layout.addWidget(auth_hint)
        self.sheets_selected_check = QCheckBox("Sync only aircraft selected on Add flights")
        layout.addWidget(self.sheets_selected_check)
        buttons = QHBoxLayout()
        self.create_workbook_button = QPushButton("Create workbook")
        self.create_workbook_button.clicked.connect(self.create_workbook)
        self.sync_workbook_button = QPushButton("Review sync")
        self.sync_workbook_button.clicked.connect(self.review_sheets_sync)
        self.open_workbook_button = QPushButton("Open workbook")
        self.open_workbook_button.clicked.connect(self._open_workbook)
        self.save_sheets_button = QPushButton("Save connection settings")
        self.save_sheets_button.clicked.connect(self._save_sheets_settings)
        self.import_notes_button = QPushButton("Import reviewed Sheets Notes…")
        self.import_notes_button.clicked.connect(self._import_sheets_notes)
        for button in (self.create_workbook_button, self.sync_workbook_button, self.open_workbook_button):
            buttons.addWidget(button)
        credential_form.addRow(self.save_sheets_button)
        layout.addLayout(buttons)
        layout.addWidget(self.import_notes_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.sheets_report = QTextEdit()
        self.sheets_report.setReadOnly(True)
        self.sheets_report.setPlaceholderText("Workbook creation and sync reports appear here. No cloud connection is made until you request one.")
        layout.addWidget(self.sheets_report, 1)
        self.workbook_edit.textChanged.connect(self._workbook_changed)
        self.credentials_edit.textChanged.connect(self._connection_changed)
        self.remember_google_check.toggled.connect(self._connection_changed)
        self.sheets_selected_check.toggled.connect(self._workbook_changed)
        self.tabs.addTab(page, "Google Sheets")

    def prepare_library_change(self) -> bool:
        return self._confirm_notes_change() and self._confirm_policy_change()

    def set_library(self, root: str | Path | None, logs: list[LibraryLogInfo]) -> bool:
        if not self.prepare_library_change():
            return False
        saved = self.store.get_setting("flight_logs.aircraft_selection", None)
        checked = set(self.selected_aircraft() if self._logs else saved or [])
        first = not self._logs and saved is None
        self._root = Path(root) if root else None
        self._logs = list(logs)
        overrides = self.store.get_setting("flight_logs.source_aircraft", {})
        self._aircraft_mapping = {log.path: str(overrides.get(str(log.path.resolve()), infer_aircraft(log.path, self._root))) for log in logs}
        self._headers = {}
        self._populating = True
        self.aircraft_list.clear()
        counts: dict[str, int] = {}
        for log in logs:
            aircraft = self._aircraft_mapping[log.path]
            counts[aircraft] = counts.get(aircraft, 0) + 1
        for aircraft in sorted(counts, key=str.casefold):
            item = QListWidgetItem(f"{aircraft} ({counts[aircraft]} source logs)")
            item.setData(Qt.ItemDataRole.UserRole, aircraft)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if first or aircraft in checked else Qt.CheckState.Unchecked)
            self.aircraft_list.addItem(item)
        self._populating = False
        self._populate_policy_aircraft()
        self._invalidate_preview("Library changed. Preview the selected aircraft again.")
        return True

    def selected_aircraft(self) -> list[str]:
        return [str(item.data(Qt.ItemDataRole.UserRole)) for index in range(self.aircraft_list.count()) if (item := self.aircraft_list.item(index)) is not None and item.checkState() == Qt.CheckState.Checked]

    def _file_aircraft(self) -> dict[str | Path, str]:
        result: dict[str | Path, str] = {path: name for path, name in self._aircraft_mapping.items()}
        return result

    def _select_aircraft(self, checked: bool) -> None:
        self._populating = True
        for index in range(self.aircraft_list.count()):
            item = self.aircraft_list.item(index)
            if item:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._populating = False
        self._selection_changed()

    def _selection_changed(self, *_: object) -> None:
        if not self._populating:
            self.store.set_setting("flight_logs.aircraft_selection", self.selected_aircraft())
            if self.sheets_selected_check.isChecked():
                self._workbook_changed()
            self._invalidate_preview("Aircraft selection changed. Preview again before writing.")

    def _invalidate_preview(self, message: str) -> None:
        self._revision += 1
        self._preview = None
        self._decisions = {}
        self._sheets_review = []
        self.candidate_table.setRowCount(0)
        self.preview_summary.setText("No current preview.")
        self.preview_details.clear()
        self.preview_aircraft_combo.clear()
        self.output_preview_table.setRowCount(0)
        self.output_preview_table.setColumnCount(0)
        self.status_label.setText(message)
        self._update_controls()

    def _run(self, label: str, work: Callable[[], object], callback: Callable[[object], None]) -> None:
        if self._busy or self._closing:
            return
        self._busy = True
        self._callback = callback
        self.status_label.setText(label)
        self._update_controls()
        task = _FlightLogTask(work, self)
        self._tasks.append(task)
        task.completed.connect(self._task_succeeded)
        task.failed.connect(self._task_failed)
        task.finished.connect(lambda: self._task_finished(task))
        task.start()

    def _task_succeeded(self, result: object) -> None:
        self._busy = False
        callback, self._callback = self._callback, None
        if not self._closing and callback:
            try:
                callback(result)
            except Exception as exc:
                self._task_failed(str(exc))
        self._update_controls()

    def _task_failed(self, error: str) -> None:
        self._busy = False
        self._callback = None
        if not self._closing:
            self.status_label.setText(f"Operation failed: {error}")
            if self._client is not None and not self._client.signed_in:
                self.google_account_label.setText("Google is not connected. Choose Sign in with Google to reconnect.")
            QMessageBox.warning(self, "Flight logs", error)
        self._update_controls()

    def _task_finished(self, task: _FlightLogTask) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.deleteLater()

    def _update_controls(self) -> None:
        if self._displaying_columns:
            return
        idle = not self._busy and not self._closing
        self.open_library_button.setEnabled(idle)
        for widget in (self.output_button, self.aircraft_list, self.mapping_button, self.select_all_button, self.select_none_button, self.policy_aircraft_combo, self.save_policy_button, self.discover_button, self.refresh_logs_button, self.log_aircraft_combo, self.credentials_edit, self.credentials_button, self.workbook_edit, self.workbook_title_edit, self.create_workbook_button, self.sync_workbook_button, self.save_sheets_button, self.sheets_selected_check, self.pending_only_check, self.preview_aircraft_combo, self.output_preview_table, self.log_table, self.sign_in_button, self.setup_google_button, self.remember_google_check):
            widget.setEnabled(idle)
        selected = bool(self.selected_aircraft())
        self.preview_button.setEnabled(idle and selected and not self._settings_error)
        self.rebuild_button.setEnabled(idle and selected and not self._settings_error)
        self.save_policy_button.setEnabled(idle and not self._settings_error)
        self.import_notes_button.setEnabled(idle and bool(self._remote_notes))
        self.generate_button.setEnabled(idle and not self._settings_error and self._preview is not None and self._preview.can_generate and not self._preview.pending_candidates)
        self.candidate_table.setEnabled(idle)
        has_selection = bool(self.candidate_table.selectedItems())
        self.accept_selected_button.setEnabled(idle and has_selection)
        self.reject_selected_button.setEnabled(idle and has_selection)
        self.next_pending_button.setEnabled(idle and self._preview is not None and bool(self._preview.pending_candidates))
        self.save_columns_button.setEnabled(idle and self._document is not None and self._visual_headers(self.log_table) != self._document.headers)
        self.export_button.setEnabled(idle and self._document is not None)
        self.open_log_button.setEnabled(self._document is not None)
        self.notes_edit.setEnabled(idle and self._document is not None and bool(self.log_table.selectedItems()))
        self.save_notes_button.setEnabled(idle and self._document is not None and bool(self.log_table.selectedItems()))
        self._policy_controls()
        configured = bool(self.credentials_edit.text().strip())
        self.sign_in_button.setText("Sign in with Google" if configured else "Set up Google sign-in…")
        self.disconnect_google_button.setEnabled(idle and configured)
        self.sync_workbook_button.setEnabled(idle and bool(self.workbook_edit.text().strip()))
        row_count = sum(len(table.rows) for table in self._sheets_review)
        self.sync_workbook_button.setText(f"Sync {row_count} {'flight' if row_count == 1 else 'flights'} now" if self._sheets_review else "Review sync")
        preview = self._preview
        if preview is None:
            self.workflow_label.setText("2. Review flights  →  3. Save to your flight logs")
            self.generate_button.setText("Save reviewed flights")
        elif preview.pending_candidates:
            count = len(preview.pending_candidates)
            self.workflow_label.setText(f"2. Review {count} uncertain {'flight' if count == 1 else 'flights'}  →  3. Save")
            self.generate_button.setText("Finish flight review to save")
        elif preview.rebuild:
            self.workflow_label.setText("3. Review the changes and approve the rebuild")
            self.generate_button.setText("Review and approve rebuild…")
        else:
            self.workflow_label.setText("3. Ready to save — check Flight-log preview for the result")
            count = preview.report['rows_added']
            self.generate_button.setText(f"Save {count} new {'flight' if count == 1 else 'flights'}" if count else "Finish review (no new flights)")

    def preview_selected(self) -> None:
        self._start_preview(False)

    def preview_rebuild(self) -> None:
        self._start_preview(True)

    def _start_preview(self, rebuild: bool) -> None:
        if not self.selected_aircraft() or self._settings_error or not self._confirm_notes_change() or not self._confirm_policy_change():
            return
        revision = self._revision
        files, selected, settings, service = self._file_aircraft(), self.selected_aircraft(), copy.deepcopy(self.settings), self.service
        decisions = {**self._saved_decisions, **self._decisions}

        def work() -> object:
            preview = service.preview(files, selected, settings=settings, rebuild=rebuild)
            ids = {str(candidate.get("id", candidate.get("candidate_id", ""))) for candidate in preview.candidates}
            current = {key: value for key, value in decisions.items() if key in ids}
            return service.with_decisions(preview, current) if current else preview

        def ready(result: object) -> None:
            if revision != self._revision:
                self.status_label.setText("Library or settings changed during scanning. Preview again.")
                return
            preview = cast(FlightLogPreview, result)
            self._preview = preview
            self._headers = preview.report.get("available_headers", {})
            self._render_preview()
            self._load_policy()
            self.pending_only_check.setChecked(bool(preview.pending_candidates))
            self._next_pending()

        self._run("Scanning telemetry and preparing a rebuild review…" if rebuild else "Scanning telemetry and preparing a flight preview…", work, ready)

    def _render_preview(self) -> None:
        preview = self._preview
        if preview is None:
            return
        selected_ids = {str(cast(QComboBox, self.candidate_table.cellWidget(item.row(), 5)).property("candidate_id")) for item in self.candidate_table.selectedItems()}
        self._populating = True
        self.candidate_table.setRowCount(len(preview.candidates))
        for row, candidate in enumerate(preview.candidates):
            values = [candidate.get("aircraft", candidate.get("plane", "")), Path(str(candidate.get("source", ""))).name, candidate.get("start", candidate.get("datetime", "")), candidate.get("duration", ""), candidate.get("confidence", ""), "", candidate.get("evidence", "")]
            for column, value in enumerate(values):
                if column == 4:
                    value = "Confirmed" if value == "confirmed" else "Needs review"
                item = _readonly_item(value)
                item.setToolTip(str(value))
                self.candidate_table.setItem(row, column, item)
            decision = QComboBox()
            decision.addItems(["Pending", "Accept", "Reject"])
            status = str(candidate.get("decision", "pending")).lower()
            decision.setCurrentText({"accept": "Accept", "accepted": "Accept", "reject": "Reject", "rejected": "Reject"}.get(status, "Pending"))
            candidate_id = str(candidate.get("id", candidate.get("candidate_id", "")))
            decision.setProperty("candidate_id", candidate_id)
            decision.currentTextChanged.connect(self._candidate_decision_changed)
            self.candidate_table.setCellWidget(row, 5, decision)
            if candidate_id in selected_ids:
                self.candidate_table.selectRow(row)
        self._populating = False
        report = preview.report
        summary = f"New: {report['rows_added']} • Already logged: {report['duplicates']} • Needs review: {len(preview.pending_candidates)} • Excluded: {report['candidates_rejected']}"
        if report['read_errors'] or report['errors'] or report['late_conflicts']:
            summary += f"\n{len(report['errors'])} errors • {report['late_conflicts']} earlier flights need a rebuild — open details to review."
        self.preview_summary.setText(summary + (" • Rebuild preview" if preview.rebuild else ""))
        self.preview_details.setPlainText(_review_text(preview.report))
        self.status_label.setText("Resolve every pending candidate before generation." if preview.pending_candidates else "Preview ready. Review evidence, ground rejections, errors, and late conflicts before writing.")
        current = self.preview_aircraft_combo.currentText()
        self.preview_aircraft_combo.blockSignals(True)
        self.preview_aircraft_combo.clear()
        self.preview_aircraft_combo.addItems([update["aircraft"] for update in preview.updates if "headers" in update])
        if current:
            self.preview_aircraft_combo.setCurrentText(current)
        self.preview_aircraft_combo.blockSignals(False)
        self._render_output_preview()
        self._filter_candidates()
        self._update_controls()

    def _candidate_decision_changed(self, *_: object) -> None:
        if self._populating or self._preview is None:
            return
        decisions: dict[str, str] = {}
        for row in range(self.candidate_table.rowCount()):
            combo = cast(QComboBox, self.candidate_table.cellWidget(row, 5))
            status = combo.currentText().lower()
            if status != "pending":
                decisions[str(combo.property("candidate_id"))] = status
            else:
                self._saved_decisions.pop(str(combo.property("candidate_id")), None)
        self._decisions = decisions
        self._saved_decisions.update(decisions)
        saved = self.store.get_setting("flight_logs.candidate_decisions", {})
        saved[str(self.output_dir)] = self._saved_decisions
        self.store.set_setting("flight_logs.candidate_decisions", saved)
        # Decisions re-plan the in-memory snapshot; no telemetry is re-read and
        # the UI stays available while the pilot works through the candidates.
        self._preview = self.service.with_decisions(self._preview, decisions)
        self._render_preview()

    def _filter_candidates(self, *_: object) -> None:
        if self._preview is not None:
            for row, candidate in enumerate(self._preview.candidates):
                self.candidate_table.setRowHidden(row, self.pending_only_check.isChecked() and candidate["decision"] != "pending")
        self._update_controls()

    def _next_pending(self) -> None:
        if self._preview is None:
            return
        pending = [row for row, candidate in enumerate(self._preview.candidates) if candidate["decision"] == "pending"]
        if pending:
            current = self.candidate_table.currentRow()
            self.candidate_table.selectRow(next((row for row in pending if row > current), pending[0]))

    def _decide_selected(self, decision: str) -> None:
        rows = {item.row() for item in self.candidate_table.selectedItems() if not self.candidate_table.isRowHidden(item.row())}
        self._populating = True
        for row in rows:
            cast(QComboBox, self.candidate_table.cellWidget(row, 5)).setCurrentText(decision)
        self._populating = False
        if rows:
            self._candidate_decision_changed()
            self._next_pending()

    @staticmethod
    def _visual_headers(table: QTableWidget) -> list[str]:
        header = _header(table)
        return [cast(QTableWidgetItem, table.horizontalHeaderItem(header.logicalIndex(index))).text()
                for index in range(table.columnCount())]

    def _render_output_preview(self, *_: object) -> None:
        preview = self._preview
        if preview is None:
            return
        update = next((item for item in preview.updates if item["aircraft"] == self.preview_aircraft_combo.currentText()), None)
        self._displaying_columns = True
        self.output_preview_table.setRowCount(0)
        self.output_preview_table.setColumnCount(0)
        if update is not None and "headers" in update:
            headers = update["headers"]
            self.output_preview_table.setColumnCount(len(headers))
            self.output_preview_table.setHorizontalHeaderLabels(headers)
            self.output_preview_table.setRowCount(len(update["rows"]))
            for row, values in enumerate(update["rows"]):
                for column, value in enumerate(values):
                    self.output_preview_table.setItem(row, column, _readonly_item(value))
            movable = update.get("created", False) or preview.rebuild
            _header(self.output_preview_table).setSectionsMovable(movable)
            self.preview_columns_hint.setText("Drag column headings to choose the saved order." if movable else "This log keeps its saved column order. To change it, drag the headings in Flight logs and save the order.")
        self._displaying_columns = False

    def _preview_column_moved(self, *_: object) -> None:
        if self._displaying_columns or self._preview is None:
            return
        self._preview = self.service.with_column_order(self._preview, self.preview_aircraft_combo.currentText(), self._visual_headers(self.output_preview_table))
        self._render_output_preview()

    def _show_candidate(self) -> None:
        if self._preview is None or self._populating:
            return
        row = self.candidate_table.currentRow()
        if 0 <= row < len(self._preview.candidates):
            self.preview_details.setPlainText(_review_text(self._preview.candidates[row]) + "\n\nFull review:\n" + _review_text(self._preview.report))
        self._update_controls()

    def generate_logs(self) -> None:
        if not self._confirm_notes_change() or not self._confirm_policy_change():
            return
        preview = self._preview
        if preview is None or not preview.can_generate or preview.pending_candidates:
            return
        if getattr(preview, "rebuild", False):
            self._review_rebuild(preview)
            return
        self._write_preview(preview, False)

    def _review_rebuild(self, preview: FlightLogPreview) -> None:
        if not preview.can_generate or preview.pending_candidates:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Review flight-log rebuild")
        dialog.resize(850, 650)
        layout = QVBoxLayout(dialog)
        label = QLabel("Rebuild replaces generated rows and renumbers accepted flights. Review the proposed schema, removed flights, and unmatched pilot notes below. Matching flight Notes are retained; Notes on removed or unmatched flights may be lost.")
        label.setWordWrap(True)
        layout.addWidget(label)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(_review_text(preview.report))
        layout.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = cast(QPushButton, buttons.button(QDialogButtonBox.StandardButton.Ok))
        ok.setText("Approve and rebuild")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._write_preview(preview, True)

    def _write_preview(self, preview: FlightLogPreview, rebuild: bool) -> None:
        self._note_original = self.notes_edit.toPlainText()
        service = self.service

        def ready(result: object) -> None:
            report = cast(dict[str, Any], result)
            self._invalidate_preview("Flight-log update finished. Refresh preview before another write.")
            self.preview_details.setPlainText(_review_text(report))
            self.preview_summary.setText("Generation report — review per-aircraft results below.")
            self.settings = service.load_settings()
            self._refresh_log_list()
            self.tabs.setCurrentIndex(1)

        def write() -> object:
            if preview._settings.get("column_orders") != self.settings.get("column_orders"):
                service.save_settings(preview._settings)
            return service.generate(preview, approve_rebuild=rebuild)

        self._run("Writing the reviewed flight logs…", write, ready)

    def _choose_output(self) -> None:
        if not self._confirm_notes_change() or not self._confirm_policy_change():
            return
        path = QFileDialog.getExistingDirectory(self, "Choose flight-log output folder", str(self.output_dir))
        if path:
            self.output_dir = Path(path).resolve()
            self.output_edit.setText(str(self.output_dir))
            self.store.set_setting("flight_logs.output_dir", str(self.output_dir))
            self.service = FlightLogService(self.output_dir)
            self._remote_notes = {}
            self._sheets_local_documents = {}
            self._settings_error = ""
            try:
                self.settings = self.service.load_settings()
            except (OSError, ValueError) as exc:
                self.settings = {"defaults": copy.deepcopy(_DEFAULT_POLICY), "planes": {}}
                self._settings_error = str(exc)
            self._saved_decisions = self.store.get_setting("flight_logs.candidate_decisions", {}).get(str(self.output_dir), {})
            self._document = None
            self._populate_policy_aircraft()
            self._invalidate_preview("Output folder changed. Preview again before writing.")
            self._refresh_log_list()
            if self._settings_error:
                self.status_label.setText(f"Flight-log settings could not be read: {self._settings_error}. Correct the settings file or choose another folder.")

    def _open_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def _refresh_log_list(self) -> None:
        if not self._confirm_notes_change():
            return
        current = self.log_aircraft_combo.currentText()
        self.log_aircraft_combo.blockSignals(True)
        self.log_aircraft_combo.clear()
        try:
            names = self.service.list_aircraft()
        except (OSError, ValueError) as exc:
            names = []
            self.status_label.setText(f"Flight logs could not be read: {exc}. Correct the files or choose another output folder.")
        self.log_aircraft_combo.addItems(names)
        if current:
            self.log_aircraft_combo.setCurrentText(current)
        self.log_aircraft_combo.blockSignals(False)
        self._load_document(self.log_aircraft_combo.currentText())

    def _load_document(self, aircraft: str) -> None:
        if self._busy:
            return
        if not self._confirm_notes_change():
            self.log_aircraft_combo.blockSignals(True)
            self.log_aircraft_combo.setCurrentText(self._log_aircraft)
            self.log_aircraft_combo.blockSignals(False)
            return
        self._document = None
        self._note_row = -1
        self._note_original = ""
        self._log_aircraft = aircraft
        self.log_table.setRowCount(0)
        self.log_table.setColumnCount(0)
        self.notes_edit.clear()
        self.log_path_label.setText("No flight log selected.")
        self._update_controls()
        if aircraft:
            service = self.service
            self._run("Loading flight log…", lambda: service.read_log(aircraft), self._display_document)

    def _display_document(self, result: object) -> None:
        document = cast(FlightLogDocument, result)
        self._document = document
        self.log_path_label.setText(str(document.path))
        self._displaying_columns = True
        self.log_table.setColumnCount(0)
        self.log_table.setColumnCount(len(document.headers))
        self.log_table.setHorizontalHeaderLabels(document.headers)
        self.log_table.setRowCount(len(document.rows))
        for row, values in enumerate(document.rows):
            for column, value in enumerate(values):
                self.log_table.setItem(row, column, _readonly_item(value))
        self._displaying_columns = False
        self.status_label.setText(f"Loaded {len(document.rows)} flights for {self.log_aircraft_combo.currentText()}.")
        if document.rows:
            self.log_table.selectRow(0)
        self._update_controls()

    def _show_notes(self) -> None:
        row = self.log_table.currentRow()
        if row != self._note_row and not self._confirm_notes_change():
            self.log_table.blockSignals(True)
            self.log_table.selectRow(self._note_row)
            self.log_table.blockSignals(False)
            return
        self._note_row = row
        self._note_original = ""
        self.notes_edit.clear()
        document = self._document
        if document is not None and 0 <= row < len(document.rows) and "Notes" in document.headers:
            self._note_original = document.rows[row][document.headers.index("Notes")]
            self.notes_edit.setPlainText(self._note_original)
        self._update_controls()

    def save_notes(self) -> None:
        document = self._document
        row = self._note_row
        if document is None or not 0 <= row < len(document.rows):
            return
        aircraft, notes, service = self.log_aircraft_combo.currentText(), self.notes_edit.toPlainText(), self.service
        flight_number = int(document.rows[row][document.headers.index("Flight number")])

        def ready(result: object) -> None:
            self._note_original = notes
            self._display_document(result)
            self.log_table.selectRow(row)
            self.status_label.setText("Pilot Notes saved.")
            self._invalidate_preview("Pilot Notes saved. Preview again before generation.")

        self._run("Saving pilot Notes…", lambda: service.save_notes(aircraft, flight_number, notes, expected_fingerprint=document.fingerprint), ready)

    def _export_csv(self) -> None:
        document = self._document
        if document is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Export flight log to CSV", str(document.path.with_suffix(".csv")), "CSV files (*.csv)")
        if filename:
            headers, rows = list(document.headers), copy.deepcopy(document.rows)

            def work() -> object:
                with Path(filename).open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(headers)
                    writer.writerows(rows)
                return filename

            self._run("Exporting flight-log CSV…", work, lambda result: self.status_label.setText(f"Exported CSV: {result}"))

    def save_column_order(self) -> None:
        document = self._document
        if document is None or not self._confirm_notes_change():
            return
        headers = self._visual_headers(self.log_table)
        if headers == document.headers:
            return
        service = self.service

        def ready(result: object) -> None:
            self._display_document(result)
            self.settings = service.load_settings()
            self._remote_notes = {}
            self._sheets_local_documents = {}
            self._invalidate_preview("Column order saved. Markdown, CSV and the next Sheets sync use this layout.")

        self._run("Saving flight-log column order…", lambda: service.reorder_columns(document.aircraft, headers, document.fingerprint), ready)

    def _open_log(self) -> None:
        if self._document:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._document.path)))

    def _populate_policy_aircraft(self) -> None:
        aircraft = str(self.policy_aircraft_combo.currentData() or "")
        try:
            existing = self.service.list_aircraft()
        except (OSError, ValueError):
            existing = []
        names = set(self._aircraft_mapping.values()) | set(self.settings.get("planes", {})) | set(existing)
        if self._policy_scope:
            names.add(self._policy_scope)
        self.policy_aircraft_combo.blockSignals(True)
        self.policy_aircraft_combo.clear()
        self.policy_aircraft_combo.addItem("Defaults for all aircraft", "")
        for name in sorted(names, key=str.casefold):
            self.policy_aircraft_combo.addItem(name, name)
        index = self.policy_aircraft_combo.findData(aircraft)
        self.policy_aircraft_combo.setCurrentIndex(max(index, 0))
        self.policy_aircraft_combo.blockSignals(False)
        self._load_policy()

    def _load_policy(self, *_: object, _skip_guard: bool = False, _scope: str | None = None) -> None:
        if not _skip_guard and not self._confirm_policy_change():
            self.policy_aircraft_combo.blockSignals(True)
            self.policy_aircraft_combo.setCurrentIndex(max(0, self.policy_aircraft_combo.findData(self._policy_scope)))
            self.policy_aircraft_combo.blockSignals(False)
            return
        aircraft = str(self.policy_aircraft_combo.currentData() or "") if _scope is None else _scope
        defaults = {**_DEFAULT_POLICY, **self.settings.get("defaults", {})}
        override = next((value for name, value in self.settings.get("planes", {}).items() if name.casefold() == aircraft.casefold()), None)
        policy = {**defaults, **(override or {})}
        self.inherit_check.blockSignals(True)
        has_override = override is not None and any(key in _DEFAULT_POLICY for key in override)
        self.inherit_check.setChecked(bool(aircraft) and not has_override)
        self.inherit_check.blockSignals(False)
        self.inherit_check.setVisible(bool(aircraft))
        self.auto_vfr_check.setChecked(policy.get("auto_vfr", True))
        self.auto_temperature_check.setChecked(policy.get("auto_temperatures", True))
        self.auto_servo_current_check.setChecked(policy.get("auto_servo_currents", True))
        headers = set(policy.get("max_sensors", [])) | set(policy.get("exclude_sensors", []))
        if aircraft:
            headers.update(self._headers.get(aircraft, []))
        else:
            for values in self._headers.values():
                headers.update(values)
        self.sensor_table.setRowCount(0)
        for header in sorted(headers, key=str.casefold):
            self._insert_sensor(header, header in policy.get("max_sensors", []), header in policy.get("exclude_sensors", []))
        self._policy_scope = aircraft
        self._policy_original = self._policy_snapshot()
        self._policy_controls()

    def _policy_snapshot(self) -> dict[str, Any]:
        return {
            "inherit": self.inherit_check.isChecked(),
            "auto_vfr": self.auto_vfr_check.isChecked(),
            "auto_temperatures": self.auto_temperature_check.isChecked(),
            "auto_servo_currents": self.auto_servo_current_check.isChecked(),
            "max_sensors": sorted(_item(self.sensor_table, row, 0).text() for row in range(self.sensor_table.rowCount()) if _item(self.sensor_table, row, 1).checkState() == Qt.CheckState.Checked),
            "exclude_sensors": sorted(_item(self.sensor_table, row, 0).text() for row in range(self.sensor_table.rowCount()) if _item(self.sensor_table, row, 2).checkState() == Qt.CheckState.Checked),
        }

    def _confirm_policy_change(self) -> bool:
        if self._policy_original is None or self._policy_snapshot() == self._policy_original:
            return True
        discard = QMessageBox.question(self, "Unsaved telemetry settings", "Discard the unsaved telemetry settings? Choose No to keep editing and save them first.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        if discard:
            self._load_policy(_skip_guard=True, _scope=self._policy_scope)
        return discard

    def _policy_controls(self, *_: object) -> None:
        active = not self._busy and not self._closing and (not self.policy_aircraft_combo.currentData() or not self.inherit_check.isChecked())
        self.inherit_check.setEnabled(not self._busy and not self._closing)
        for widget in (self.sensor_table, self.auto_vfr_check, self.auto_temperature_check, self.auto_servo_current_check, self.custom_sensor_edit, self.add_sensor_button):
            widget.setEnabled(active)

    def _insert_sensor(self, header: str, maximum: bool = False, excluded: bool = False) -> None:
        row = self.sensor_table.rowCount()
        self.sensor_table.insertRow(row)
        self.sensor_table.setItem(row, 0, _readonly_item(header))
        for column, checked in ((1, maximum), (2, excluded)):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.sensor_table.setItem(row, column, item)

    def _add_sensor(self) -> None:
        header = self.custom_sensor_edit.text().strip()
        if header and all(_item(self.sensor_table, row, 0).text() != header for row in range(self.sensor_table.rowCount())):
            self._insert_sensor(header, maximum=True)
            self.custom_sensor_edit.clear()

    def _discover_sensors(self) -> None:
        if not self._confirm_policy_change():
            return
        service, files = self.service, self._file_aircraft()

        def ready(result: object) -> None:
            self._headers = cast(dict[str, list[str]], result)
            self._load_policy()
            self.status_label.setText("Loaded exact source telemetry headers. Save changes to apply them.")

        self._run("Reading source telemetry headers…", lambda: service.available_sensors(files), ready)

    def save_telemetry_settings(self) -> None:
        aircraft = str(self.policy_aircraft_combo.currentData() or "")
        settings = copy.deepcopy(self.settings)
        settings.setdefault("planes", {})
        policy: dict[str, Any] = {"auto_vfr": self.auto_vfr_check.isChecked(), "auto_temperatures": self.auto_temperature_check.isChecked(), "auto_servo_currents": self.auto_servo_current_check.isChecked(), "max_sensors": [], "exclude_sensors": []}
        for row in range(self.sensor_table.rowCount()):
            header = _item(self.sensor_table, row, 0).text()
            if _item(self.sensor_table, row, 1).checkState() == Qt.CheckState.Checked:
                policy["max_sensors"].append(header)
            if _item(self.sensor_table, row, 2).checkState() == Qt.CheckState.Checked:
                policy["exclude_sensors"].append(header)
        if aircraft:
            for key in list(settings["planes"]):
                if key.casefold() == aircraft.casefold():
                    del settings["planes"][key]
            if not self.inherit_check.isChecked():
                settings["planes"][aircraft] = policy
        else:
            settings["defaults"] = policy
        service = self.service

        def ready(result: object) -> None:
            self.settings = cast(dict[str, Any], result)
            self._invalidate_preview("Telemetry settings saved. Preview again to review their effect.")
            self._load_policy(_skip_guard=True)

        self._run("Saving telemetry settings…", lambda: service.save_settings(settings), ready)

    def _choose_credentials(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose Google credentials JSON", self.credentials_edit.text(), "JSON files (*.json)")
        if filename:
            try:
                google_credential_config(filename)
            except GoogleSheetsError as exc:
                QMessageBox.warning(self, "Google sign-in", str(exc))
                return
            self.credentials_edit.setText(filename)
            self._save_sheets_settings()

    def _setup_google(self) -> None:
        dialog = GoogleSignInSetupDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.credentials_edit.setText(dialog.client_path_edit.text())
            self._save_sheets_settings()
            self.google_account_label.setText("Setup complete. Choose Sign in with Google to connect your Gmail account.")

    def sign_in_google(self) -> None:
        credentials = self.credentials_edit.text().strip()
        if not credentials:
            self._setup_google()
            return
        self._save_sheets_settings()

        def ready(result: object) -> None:
            client = cast(GoogleSheetsClient, result)
            label = client.account_email or "Google account"
            if client._credential_kind == "service_account":
                self.google_account_label.setText(f"Connected service account: {label}")
            else:
                remembered = "Sign-in is remembered securely on this Windows account." if client.authorization_remembered else "Sign-in is available for this app session."
                if self._remember_google and not client.authorization_remembered:
                    remembered += " Authorization could not be remembered securely; sign in again after restarting."
                self.google_account_label.setText(f"Connected: {label}. {remembered}")
            self.status_label.setText("Google connected. Create or link a workbook, then review the sync.")

        self._run("Opening Google sign-in in your browser…", lambda: self._google_client(credentials), ready)

    def disconnect_google(self) -> None:
        credentials = self.credentials_edit.text().strip()
        if not credentials:
            return
        client = self._client or GoogleSheetsClient(credentials)

        def ready(_result: object) -> None:
            self._client = None
            self._client_credentials = ""
            self._workbook_changed()
            self.google_account_label.setText("Disconnected. Saved authorization was removed from this computer.")
            self.status_label.setText("Google disconnected. Your workbook remains available in Google Sheets.")

        self._run("Removing saved Google sign-in…", client.disconnect, ready)

    def _connection_changed(self, *_: object) -> None:
        self._remember_google = self.remember_google_check.isChecked()
        self._workbook_changed()
        self.google_account_label.setText("Connection settings changed. Choose Sign in with Google to connect.")

    def _save_sheets_settings(self) -> None:
        old_workbook = str(self.store.get_setting("flight_logs.sheets.workbook", ""))
        if old_workbook != self.workbook_edit.text().strip():
            self._remote_notes = {}
            self._sheets_local_documents = {}
        self.store.set_setting("flight_logs.sheets.credentials", self.credentials_edit.text().strip())
        self.store.set_setting("flight_logs.sheets.title", self.workbook_title_edit.text().strip())
        self.store.set_setting("flight_logs.sheets.workbook", self.workbook_edit.text().strip())
        self.store.set_setting("flight_logs.sheets.remember", self.remember_google_check.isChecked())
        self._remember_google = self.remember_google_check.isChecked()
        self.status_label.setText("Google Sheets connection settings saved locally.")
        self._update_controls()

    def _workbook_changed(self, *_: object) -> None:
        self._remote_notes = {}
        self._sheets_local_documents = {}
        self._sheets_review = []
        self._update_controls()

    def _google_client(self, credentials: str) -> GoogleSheetsClient:
        if self._client is None or self._client_credentials != credentials or self._client.remember_sign_in != self._remember_google:
            if self._client is not None:
                self._client.close()
            self._client = GoogleSheetsClient(credentials, remember_sign_in=self._remember_google)
            self._client_credentials = credentials
        self._client.authenticate()
        return self._client

    def create_workbook(self) -> None:
        credentials, title = self.credentials_edit.text().strip(), self.workbook_title_edit.text().strip()
        if not credentials:
            self._setup_google()
            return
        if not title:
            QMessageBox.information(self, "Google Sheets", "Enter a title for the new workbook first.")
            return
        self._save_sheets_settings()

        def ready(result: object) -> None:
            workbook = cast(Workbook, result)
            self.workbook_edit.setText(workbook.url)
            self._save_sheets_settings()
            self.sheets_report.setPlainText(f"Created workbook: {workbook.title}\n{workbook.url}\n\nChoose Review sync, check the flight counts, then Sync flights now.")

        self._run("Signing in and creating a Google Sheets workbook…", lambda: self._google_client(credentials).create_workbook(title), ready)

    def review_sheets_sync(self) -> None:
        if not self._confirm_notes_change():
            return
        credentials, workbook = self.credentials_edit.text().strip(), self.workbook_edit.text().strip()
        if not credentials or not workbook:
            if not credentials:
                self._setup_google()
            else:
                QMessageBox.information(self, "Google Sheets", "Create a workbook or paste an existing workbook link first.")
            return
        try:
            workbook_url(workbook)
        except GoogleSheetsError as exc:
            QMessageBox.warning(self, "Google Sheets", str(exc))
            return
        self._save_sheets_settings()
        if self._sheets_review:
            snapshots = dict(self._sheets_local_documents)
            tables = list(self._sheets_review)
            service = self.service

            def sync() -> object:
                client = self._google_client(credentials)
                for aircraft, document in snapshots.items():
                    if service.read_log(aircraft).fingerprint != document.fingerprint:
                        raise ValueError(f"Local log for {aircraft} changed since the sync review. Review again before publishing.")
                return client.sync_workbook(workbook, tables)

            self._sheets_review = []
            self._run("Syncing Google Sheets flight-log views…", sync, self._show_sheets_report)
            return
        names = self.selected_aircraft() if self.sheets_selected_check.isChecked() else None
        service = self.service

        def load() -> object:
            documents = {aircraft: service.read_log(aircraft) for aircraft in service.list_aircraft() if names is None or aircraft in names}
            return documents

        def ready(result: object) -> None:
            self._sheets_local_documents = cast(dict[str, FlightLogDocument], result)
            tables = [FlightLogSheet(name, document.headers, document.rows) for name, document in self._sheets_local_documents.items()]
            if not tables:
                self.status_label.setText("No generated flight logs match the sync selection.")
                return
            lines = [f"{table.aircraft}: {len(table.rows)} flights" for table in tables]
            self._sheets_review = tables
            self.sheets_report.setPlainText("Ready to sync to:\n" + workbook_url(workbook) + "\n\n" + "\n".join(lines) + "\n\nAircraft, flight summaries and pilot Notes will be sent. Edited Sheets Notes are kept; conflicting edits stop sync.\n\nChoose Sync flights now to publish these reviewed tables.")
            self.status_label.setText("Review the flight counts above, then choose Sync flights now.")

        self._run("Preparing the Google Sheets sync review…", load, ready)

    def _show_sheets_report(self, result: object) -> None:
        report = cast(SheetsSyncReport, result)
        self._remote_notes = report.remote_notes if not report.conflicts else {}
        text = f"Aircraft synced: {report.aircraft_written}\nFlight rows written: {report.rows_written}\nEdited remote Notes preserved: {report.notes_preserved}\nWorkbook: {report.workbook_url}"
        if report.conflicts:
            text += "\n\nSync blocked by Notes conflicts. Reconcile the local and Sheets Notes, then retry:\n" + "\n".join(report.conflicts)
        self.sheets_report.setPlainText(text)
        self.status_label.setText("Sheets sync requires resolving reported Notes conflicts." if report.conflicts else "Google Sheets sync complete.")

    def _import_sheets_notes(self) -> None:
        if not self._remote_notes or not self._confirm_notes_change():
            return
        updates: dict[str, dict[int, str]] = {}
        review: list[str] = []
        fingerprints: dict[str, str] = {}
        for aircraft, notes in self._remote_notes.items():
            document = self._sheets_local_documents.get(aircraft)
            if document is None:
                continue
            fingerprints[aircraft] = document.fingerprint
            date_column = document.headers.index("Date/time")
            duration_column = document.headers.index("Flight duration")
            number_column = document.headers.index("Flight number")
            notes_column = document.headers.index("Notes")
            for row in document.rows:
                identity = stable_flight_id(aircraft, row[date_column], row[duration_column])
                if identity in notes and notes[identity] != row[notes_column]:
                    updates.setdefault(aircraft, {})[int(row[number_column])] = notes[identity]
                    review.append(f"{aircraft} — {row[date_column]} ({row[duration_column]})\nLocal Notes: {row[notes_column] or '(empty)'}\nSheets Notes: {notes[identity] or '(empty)'}")
        if not updates:
            self.status_label.setText("No differing Sheets Notes are available to import.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Review Sheets Notes import")
        dialog.resize(750, 550)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Import these reviewed Notes into local Markdown logs. Local files must still match the versions used for the last sync. Empty Sheets Notes clear the corresponding local Notes.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText("\n\n".join(review))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        cast(QPushButton, buttons.button(QDialogButtonBox.StandardButton.Ok)).setText("Import reviewed Notes")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        service = self.service

        def ready(result: object) -> None:
            self._remote_notes = {}
            self._note_original = self.notes_edit.toPlainText()
            self._invalidate_preview("Reviewed Sheets Notes imported. Preview again before generation.")
            self.sheets_report.append(f"\nImported reviewed Sheets Notes: {result}")
            self._refresh_log_list()

        self._run("Importing reviewed Google Sheets Notes…", lambda: service.save_notes_many(updates, fingerprints), ready)

    def _open_workbook(self) -> None:
        value = self.workbook_edit.text().strip()
        if value:
            try:
                QDesktopServices.openUrl(QUrl(workbook_url(value)))
            except GoogleSheetsError as exc:
                QMessageBox.warning(self, "Google Sheets", str(exc))

    def wait_for_workers(self, timeout_ms: int = 15000) -> bool:
        """Wait for work during application close; never terminate a table write."""
        for task in list(self._tasks):
            if not task.wait(timeout_ms):
                return False
        return True

    def shutdown(self, timeout_ms: int = 15000) -> bool:
        self._closing = True
        self._update_controls()
        complete = self.wait_for_workers(timeout_ms)
        if complete and self._client is not None:
            self._client.close()
            self._client = None
        return complete

    def _confirm_notes_change(self) -> bool:
        if self._document is None or self._note_row < 0 or self.notes_edit.toPlainText() == self._note_original:
            return True
        discard = QMessageBox.question(self, "Unsaved pilot Notes", "Discard the unsaved pilot Notes? Choose No to keep editing or save them first.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        if discard:
            self.notes_edit.setPlainText(self._note_original)
        return discard

    def prepare_close(self) -> bool:
        if self._busy or any(task.isRunning() for task in self._tasks):
            QMessageBox.information(self, "Flight logs are working", "Wait for the current flight-log or Google Sheets operation to finish, then close the app.")
            return False
        if not self._confirm_notes_change():
            self.tabs.setCurrentIndex(1)
            return False
        if not self._confirm_policy_change():
            self.tabs.setCurrentIndex(2)
            return False
        return True

    def _review_source_mapping(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Review source aircraft")
        dialog.resize(800, 550)
        layout = QVBoxLayout(dialog)
        label = QLabel("Review which aircraft owns each telemetry file. Edit an aircraft name to correct folders that contain multiple models. These choices are saved locally and applied to future previews.")
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(self._logs), 2)
        table.setHorizontalHeaderLabels(["Telemetry file", "Aircraft"])
        _header(table).setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _header(table).setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for row, log in enumerate(self._logs):
            table.setItem(row, 0, _readonly_item(log.path))
            table.setItem(row, 1, QTableWidgetItem(self._aircraft_mapping[log.path]))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mapping = {str(log.path.resolve()): _item(table, row, 1).text().strip() for row, log in enumerate(self._logs)}
        if any(not value or "\n" in value or "\r" in value for value in mapping.values()):
            QMessageBox.warning(self, "Source aircraft", "Every source file needs a nonempty, single-line aircraft name.")
            return
        saved = self.store.get_setting("flight_logs.source_aircraft", {})
        saved.update(mapping)
        self.store.set_setting("flight_logs.source_aircraft", saved)
        self.set_library(self._root, self._logs)
