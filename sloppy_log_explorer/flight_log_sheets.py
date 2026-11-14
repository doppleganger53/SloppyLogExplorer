"""Explicit Google Sheets publishing with managed tabs and three-way Notes merges.

Local Markdown owns flight metrics. Sheets Notes can be edited in either the
combined or aircraft view; concurrent conflicting edits stop the entire sync.
Personal Google accounts use browser OAuth; remembered authorization stays in
Windows Credential Manager, never in application settings or plaintext files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
PERSONAL_SCOPES = [SHEETS_SCOPE, "openid", "https://www.googleapis.com/auth/userinfo.email"]
API_ROOT = "https://sheets.googleapis.com/v4"
OWNERSHIP_KEY = "sloppy_log_explorer.flight_logs.v1"
ID_HEADER = "_SLE flight id"
BASE_HEADER = "_SLE local notes baseline"
ALL_SHEET_TITLE = "SLE - All flights"
REQUIRED_HEADERS = ("Date/time", "Flight duration", "Notes")
_TOKEN_ENDPOINTS = {
    "https://oauth2.googleapis.com/token",
    "https://accounts.google.com/o/oauth2/token",
    "https://www.googleapis.com/oauth2/v4/token",
}
_AUTH_ENDPOINTS = {
    "https://accounts.google.com/o/oauth2/auth",
    "https://accounts.google.com/o/oauth2/v2/auth",
}


class GoogleSheetsError(RuntimeError):
    """An actionable, sanitized error safe to display in the desktop UI."""


def default_google_oauth_client() -> str:
    """Allow a distributor's registered desktop client, or local one-time setup."""
    configured = os.environ.get("SLE_GOOGLE_OAUTH_CLIENT", "").strip()
    if configured:
        return configured
    bundled = Path(__file__).parent / "assets" / "google-oauth-client.json"
    return str(bundled) if bundled.is_file() else ""


def google_credential_config(path: str | Path) -> dict[str, Any]:
    """Read and validate configuration before sign-in or saving its location."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise GoogleSheetsError("The credentials file could not be read as Google JSON credentials.") from None
    if not isinstance(config, dict):
        raise GoogleSheetsError("Choose a Desktop app OAuth JSON downloaded from Google.")
    installed = config.get("installed")
    if isinstance(installed, dict):
        if installed.get("token_uri") not in _TOKEN_ENDPOINTS or installed.get("auth_uri") not in _AUTH_ENDPOINTS:
            raise GoogleSheetsError("The credentials contain an unsupported Google authentication endpoint.")
        return config
    if config.get("type") == "service_account":
        if config.get("token_uri") not in _TOKEN_ENDPOINTS or config.get("universe_domain", "googleapis.com") != "googleapis.com":
            raise GoogleSheetsError("The credentials contain an unsupported Google authentication endpoint.")
        return config
    raise GoogleSheetsError("Select a Google Desktop OAuth client or service-account JSON file.")


class _GoogleTokenStore:
    """Use the native Windows vault explicitly; never fall back to a text file."""

    service = "SloppyLogExplorer.GoogleSheets"

    @staticmethod
    def _backend() -> Any:
        if sys.platform != "win32":
            raise RuntimeError("Secure Windows storage is unavailable")
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def load(self, identity: str) -> str | None:
        try:
            return self._backend().get_password(self.service, identity)
        except Exception:
            return None

    def save(self, identity: str, value: str) -> bool:
        try:
            self._backend().set_password(self.service, identity, value)
            return True
        except Exception:
            return False

    def delete(self, identity: str) -> None:
        try:
            backend = self._backend()
            if backend.get_password(self.service, identity) is not None:
                backend.delete_password(self.service, identity)
        except Exception:
            raise GoogleSheetsError("Could not remove saved Google authorization from Windows Credential Manager. Retry disconnecting.") from None


@dataclass(frozen=True)
class FlightLogSheet:
    aircraft: str
    headers: Sequence[str]
    rows: Sequence[Sequence[str]]


@dataclass(frozen=True)
class Workbook:
    spreadsheet_id: str
    url: str
    title: str


@dataclass(frozen=True)
class SheetsSyncReport:
    workbook_url: str
    aircraft_written: int = 0
    rows_written: int = 0
    notes_preserved: int = 0
    conflicts: tuple[str, ...] = ()
    remote_notes: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return not self.conflicts


Transport = Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], dict[str, Any]]


def spreadsheet_id(value: str) -> str:
    """Accept a workbook ID or an HTTPS Google Sheets workbook URL."""
    candidate = value.strip()
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or parsed.netloc != "docs.google.com":
            raise GoogleSheetsError("Use an HTTPS docs.google.com spreadsheet URL or workbook ID.")
        match = re.match(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
        if match is None:
            raise GoogleSheetsError("The URL does not identify a Google Sheets workbook.")
        candidate = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", candidate):
        raise GoogleSheetsError("The Google Sheets workbook ID is invalid.")
    return candidate


def workbook_url(value: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id(value)}/edit"


def stable_flight_id(aircraft: str, date_time: str, duration: str) -> str:
    # Flight number may change after a reviewed rebuild; source identity must not.
    value = json.dumps([aircraft.strip(), date_time.strip(), duration.strip()], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aircraft_key(aircraft: str) -> str:
    return "aircraft:" + hashlib.sha256(aircraft.encode("utf-8")).hexdigest()


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _quoted_title(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _unique_title(desired: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", desired).strip(" '")[:100] or "SLE - Aircraft"
    title = base
    number = 2
    while title.casefold() in used:
        suffix = f" ({number})"
        title = base[:100 - len(suffix)] + suffix
        number += 1
    used.add(title.casefold())
    return title


def _validated_tables(tables: Sequence[FlightLogSheet]) -> list[FlightLogSheet]:
    result: list[FlightLogSheet] = []
    names: set[str] = set()
    for table in tables:
        aircraft = str(table.aircraft).strip()
        if not aircraft or aircraft in names:
            raise GoogleSheetsError("Every exported aircraft must have a unique, nonempty name.")
        names.add(aircraft)
        headers = [str(header) for header in table.headers]
        if len(set(headers)) != len(headers) or any(header not in headers for header in REQUIRED_HEADERS):
            raise GoogleSheetsError(f"{aircraft}: expected unique headers including Date/time, Flight duration and Notes.")
        if any(header in headers for header in (ID_HEADER, BASE_HEADER, "Aircraft")):
            raise GoogleSheetsError(f"{aircraft}: a reserved Sheets column name appears in the flight log.")
        rows = [[str(value) for value in row] for row in table.rows]
        seen: set[str] = set()
        for row in rows:
            if len(row) != len(headers):
                raise GoogleSheetsError(f"{aircraft}: a flight log row has a different number of cells than its header.")
            date_time, duration = row[headers.index("Date/time")], row[headers.index("Flight duration")]
            if not date_time.strip() or not duration.strip():
                raise GoogleSheetsError(f"{aircraft}: every flight needs a date/time and duration.")
            identity = stable_flight_id(aircraft, date_time, duration)
            if identity in seen:
                raise GoogleSheetsError(f"{aircraft}: duplicate date/time and duration prevent a safe Sheets sync.")
            seen.add(identity)
        result.append(FlightLogSheet(aircraft, headers, rows))
    if not result:
        raise GoogleSheetsError("There are no aircraft flight logs to sync.")
    return result


def _merge_note(local: str, remote: str, baseline: str) -> tuple[str, bool, bool]:
    """Return merged note, whether Sheets edited it, and whether edits conflict."""
    if local == remote:
        return local, remote != baseline, False
    if remote == baseline:
        return local, False, False
    if local == baseline:
        return remote, True, False
    return local, True, True


class GoogleSheetsClient:
    """Sheets REST client. Instantiate and call from a background worker.

    A Desktop OAuth client JSON signs in through the browser (120 second
    timeout). A service-account JSON can edit an existing workbook explicitly
    shared with its client_email, but cannot create ordinary My Drive files.
    """

    def __init__(self, credentials_path: str | Path = "", transport: Transport | None = None,
                 *, remember_sign_in: bool = True):
        self.credentials_path = Path(credentials_path or default_google_oauth_client())
        self._transport = transport
        self._session: Any = None
        self._credential_kind = ""
        self._token_store = _GoogleTokenStore()
        self._token_key = ""
        self.remember_sign_in = remember_sign_in
        self.authorization_remembered = False
        self.account_email = ""

    @property
    def signed_in(self) -> bool:
        return self._session is not None

    def authenticate(self) -> None:
        if self._transport is not None or self._session is not None:
            return
        config = google_credential_config(self.credentials_path)
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google.auth.exceptions import RefreshError
            from google_auth_oauthlib.flow import InstalledAppFlow
            from requests.adapters import HTTPAdapter
        except ImportError:
            raise GoogleSheetsError("Google sign-in support is missing. Install google-auth-oauthlib or update the application.") from None
        try:
            if config.get("type") == "service_account":
                credentials = service_account.Credentials.from_service_account_info(config, scopes=[SHEETS_SCOPE])
                self._credential_kind = "service_account"
                self.account_email = str(config.get("client_email", ""))
            elif isinstance(config.get("installed"), dict):
                installed = config["installed"]
                self._token_key = hashlib.sha256(json.dumps([installed.get("client_id"), PERSONAL_SCOPES]).encode()).hexdigest()
                saved = self._token_store.load(self._token_key)
                if not self.remember_sign_in:
                    if saved is not None:
                        self._token_store.delete(self._token_key)
                    saved = None
                credentials = None
                if saved:
                    try:
                        cached = json.loads(saved)
                        if cached.get("client_id") != installed.get("client_id") or cached.get("token_uri") not in _TOKEN_ENDPOINTS:
                            raise ValueError("Saved Google client differs")
                        credentials = Credentials.from_authorized_user_info(cached)
                        if not credentials.has_scopes(PERSONAL_SCOPES):
                            raise ValueError("Saved Google permission differs")
                        credentials.refresh(partial(Request(), timeout=60))
                        self.authorization_remembered = True
                    except (ValueError, RefreshError):
                        self._token_store.delete(self._token_key)
                        credentials = None
                if credentials is None:
                    credentials = self._browser_sign_in(config, InstalledAppFlow, HTTPAdapter)
                if self.remember_sign_in and getattr(credentials, "refresh_token", None):
                    self.authorization_remembered = self._token_store.save(self._token_key, credentials.to_json(strip=["token"]))
                self._credential_kind = "installed"
            else:
                raise GoogleSheetsError("Select a Google Desktop OAuth client or service-account JSON file.")
            self._session = AuthorizedSession(credentials, refresh_timeout=60)
            if self._credential_kind == "installed":
                self._read_account_email()
        except GoogleSheetsError:
            raise
        except Exception:
            # Google/library exception messages may contain credentials or URLs.
            raise GoogleSheetsError("Google sign-in failed or timed out. Finish browser sign-in within two minutes. If access is blocked, use an External app and add your Gmail address as a test user in Google Auth platform → Audience.") from None

    @staticmethod
    def _browser_sign_in(config: dict[str, Any], flow_type: Any, adapter_type: Any) -> Any:
        flow = flow_type.from_client_config(config, scopes=PERSONAL_SCOPES, autogenerate_code_verifier=True)

        class _OAuthTimeoutAdapter(adapter_type):
            def send(self, request: Any, stream: Any = False, timeout: Any = None,
                     verify: Any = True, cert: Any = None, proxies: Any = None) -> Any:
                return super().send(request, stream=stream, timeout=timeout or (10, 60),
                                    verify=verify, cert=cert, proxies=proxies)

        flow.oauth2session.mount("https://", _OAuthTimeoutAdapter())
        return flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True,
            authorization_prompt_message="", timeout_seconds=120,
            success_message="Sloppy Log Explorer sign-in completed. You may close this browser tab.",
            access_type="offline", prompt="consent select_account",
        )

    def _read_account_email(self) -> None:
        try:
            response = self._session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=(10, 15), allow_redirects=False)
            if response.status_code == 200:
                profile = response.json()
                if profile.get("email_verified") and isinstance(profile.get("email"), str):
                    self.account_email = profile["email"]
        except Exception:
            pass  # Displaying an account label is optional; Sheets access is authorized.

    def disconnect(self) -> None:
        """Forget this computer's saved authorization; keep the workbook intact."""
        if not self._token_key:
            config = google_credential_config(self.credentials_path)
            if isinstance(config.get("installed"), dict):
                self._token_key = hashlib.sha256(json.dumps([config["installed"].get("client_id"), PERSONAL_SCOPES]).encode()).hexdigest()
        self.close()
        if self._token_key:
            self._token_store.delete(self._token_key)
        self.authorization_remembered = False

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        self._credential_kind = ""
        self.account_email = ""

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._transport is not None:
            return self._transport(method, path, body, params)
        self.authenticate()
        from google.auth.exceptions import RefreshError
        try:
            response = self._session.request(method, API_ROOT + path, json=body, params=params,
                                             timeout=(10, 60), allow_redirects=False)
        except RefreshError:
            self.close()
            if self._token_key:
                self._token_store.delete(self._token_key)
            self.authorization_remembered = False
            raise GoogleSheetsError("Google authorization expired or was revoked. Sign in again.") from None
        except Exception:
            raise GoogleSheetsError("Could not reach Google Sheets. Check your connection and retry.") from None
        if not 200 <= response.status_code < 300:
            messages = {
                400: "Google rejected the Sheets request. Check the workbook and managed tab structure.",
                401: "Google authorization expired. Sign in again.",
                403: "Google denied access. Enable the Sheets API and confirm this account has Editor access to the workbook.",
                404: "The workbook was not found or is not shared with this account.",
                429: "Google Sheets request quota was exceeded. Wait briefly and retry.",
            }
            if response.status_code == 401:
                self.close()
                if self._token_key:
                    self._token_store.delete(self._token_key)
                self.authorization_remembered = False
            raise GoogleSheetsError(messages.get(response.status_code, f"Google Sheets returned HTTP {response.status_code}. Retry later."))
        try:
            value = response.json()
        except ValueError:
            raise GoogleSheetsError("Google Sheets returned an unreadable response.") from None
        if not isinstance(value, dict):
            raise GoogleSheetsError("Google Sheets returned an unexpected response.")
        return value

    def create_workbook(self, title: str) -> Workbook:
        title = title.strip()
        if not title:
            raise GoogleSheetsError("Enter a workbook title.")
        self.authenticate()
        if self._credential_kind == "service_account":
            raise GoogleSheetsError("Service accounts cannot create My Drive workbooks. Use Desktop OAuth, or create a workbook in Google Sheets and share it with the service account as Editor.")
        response = self._request("POST", "/spreadsheets", {
            "properties": {"title": title},
            "sheets": [{"properties": {"sheetId": 0, "title": ALL_SHEET_TITLE}}],
            "developerMetadata": [{"metadataKey": OWNERSHIP_KEY, "metadataValue": "all",
                                   "visibility": "DOCUMENT", "location": {"sheetId": 0}}],
        })
        identity = spreadsheet_id(str(response.get("spreadsheetId", "")))
        return Workbook(identity, workbook_url(identity), title)

    def sync_workbook(self, workbook: str, tables: Sequence[FlightLogSheet]) -> SheetsSyncReport:
        identity = spreadsheet_id(workbook)
        tables = _validated_tables(tables)
        url = workbook_url(identity)
        metadata = self._request("GET", f"/spreadsheets/{identity}", params={
            "fields": "sheets(properties(sheetId,title,gridProperties)),developerMetadata",
        })
        properties = {int(sheet["properties"]["sheetId"]): dict(sheet["properties"])
                      for sheet in metadata.get("sheets", [])}
        managed: dict[str, dict[str, Any]] = {}
        for marker in metadata.get("developerMetadata", []):
            if marker.get("metadataKey") != OWNERSHIP_KEY:
                continue
            key = str(marker.get("metadataValue", ""))
            sheet_id = marker.get("location", {}).get("sheetId")
            if sheet_id not in properties:
                continue
            if key in managed:
                raise GoogleSheetsError("Duplicate managed flight-log tabs were found. Remove duplicate ownership metadata before syncing.")
            managed[key] = properties[sheet_id]

        requested = {"all": ALL_SHEET_TITLE}
        requested.update({_aircraft_key(table.aircraft): "SLE - " + table.aircraft for table in tables})
        existing = {key: managed[key] for key in requested if key in managed}
        remote: dict[str, list[list[str]]] = {}
        if existing:
            result = self._request("GET", f"/spreadsheets/{identity}/values:batchGet", params={
                "ranges": [_quoted_title(sheet["title"]) for sheet in existing.values()],
                "valueRenderOption": "FORMULA",
            })
            ranges = result.get("valueRanges", [])
            if len(ranges) != len(existing):
                raise GoogleSheetsError("Google returned incomplete managed-tab data; nothing was synced.")
            remote = {key: [[str(cell) for cell in row] for row in value.get("values", [])]
                      for key, value in zip(existing, ranges)}

        local: dict[str, dict[str, str]] = {}
        table_for_id: dict[str, FlightLogSheet] = {}
        for table in tables:
            for values in table.rows:
                row = dict(zip(table.headers, values))
                row_id = stable_flight_id(table.aircraft, row["Date/time"], row["Flight duration"])
                local[row_id] = row
                table_for_id[row_id] = table

        conflicts: list[str] = []
        remote_changes: dict[str, set[str]] = {}
        preserved_other: list[dict[str, str]] = []
        other_headers: list[str] = []
        chosen_aircraft = {table.aircraft for table in tables}
        for key, values in remote.items():
            if not values:
                continue
            headers = values[0]
            required = [ID_HEADER, BASE_HEADER, *REQUIRED_HEADERS]
            if key == "all":
                required.append("Aircraft")
                other_headers = [header for header in headers if header not in (ID_HEADER, BASE_HEADER, "Aircraft")]
            if any(header not in headers for header in required) or len(set(headers)) != len(headers):
                conflicts.append(f"{existing[key]['title']}: managed headers were changed. Restore the original headers before syncing.")
                continue
            seen: set[str] = set()
            for raw in values[1:]:
                if not any(raw):
                    continue
                row = {header: raw[index] if index < len(raw) else "" for index, header in enumerate(headers)}
                row_id = row[ID_HEADER]
                if key == "all":
                    aircraft = row["Aircraft"]
                else:
                    aircraft = next(table.aircraft for table in tables if _aircraft_key(table.aircraft) == key)
                if row_id in seen or row_id != stable_flight_id(aircraft, row["Date/time"], row["Flight duration"]):
                    conflicts.append(f"{existing[key]['title']}: a row identity is missing, duplicated or changed. Restore its original date/time and duration.")
                    continue
                seen.add(row_id)
                if key == "all" and aircraft not in chosen_aircraft:
                    preserved_other.append(row)
                    continue
                if row_id not in local:
                    conflicts.append(f"{aircraft}: Sheets contains a flight absent from the local log ({row['Date/time']}). Sync stopped to preserve its Notes.")
                    continue
                _note, changed, conflict = _merge_note(local[row_id]["Notes"], row["Notes"], row[BASE_HEADER])
                if conflict:
                    conflicts.append(f"{aircraft}: local and Sheets Notes both changed ({row['Date/time']}). Make the Notes agree before syncing.")
                elif changed:
                    remote_changes.setdefault(row_id, set()).add(row["Notes"])

        merged_notes: dict[str, str] = {}
        remote_notes: dict[str, dict[str, str]] = {}
        for row_id, row in local.items():
            changes = remote_changes.get(row_id, set())
            table = table_for_id[row_id]
            if len(changes) > 1:
                conflicts.append(f"{table.aircraft}: Notes differ between combined and aircraft Sheets tabs ({row['Date/time']}). Make the two views agree before syncing.")
            note = next(iter(changes)) if len(changes) == 1 else row["Notes"]
            merged_notes[row_id] = note
            if note != row["Notes"]:
                remote_notes.setdefault(table.aircraft, {})[row_id] = note
        if conflicts:
            return SheetsSyncReport(url, conflicts=tuple(dict.fromkeys(conflicts)), remote_notes=remote_notes)

        # Prepare all data before the first write; conflicts never create tabs.
        combined_headers: list[str] = []
        for header in [header for table in tables for header in table.headers] + (other_headers if preserved_other else []):
            if header not in combined_headers:
                combined_headers.append(header)
        output: dict[str, list[list[str]]] = {
            "all": [[ID_HEADER, BASE_HEADER, "Aircraft", *combined_headers]],
        }
        combined_rows: list[dict[str, str]] = preserved_other[:]
        for table in tables:
            rows = [[ID_HEADER, BASE_HEADER, *table.headers]]
            for values in table.rows:
                row = dict(zip(table.headers, values))
                row_id = stable_flight_id(table.aircraft, row["Date/time"], row["Flight duration"])
                baseline = row["Notes"]
                row["Notes"] = merged_notes[row_id]
                rows.append([row_id, baseline, *[row[header] for header in table.headers]])
                combined_rows.append({ID_HEADER: row_id, BASE_HEADER: baseline, "Aircraft": table.aircraft, **row})
            output[_aircraft_key(table.aircraft)] = rows
        combined_rows.sort(key=lambda row: (row["Date/time"], row["Aircraft"], row[ID_HEADER]))
        output["all"].extend([[row.get(header, "") for header in output["all"][0]] for row in combined_rows])

        structure: list[dict[str, Any]] = []
        used_titles = {str(prop["title"]).casefold() for prop in properties.values()}
        next_id = max(properties, default=-1) + 1
        for key, desired in requested.items():
            if key not in managed:
                title = _unique_title(desired, used_titles)
                managed[key] = {"sheetId": next_id, "title": title, "gridProperties": {}}
                structure.extend([
                    {"addSheet": {"properties": {"sheetId": next_id, "title": title}}},
                    {"createDeveloperMetadata": {"developerMetadata": {
                        "metadataKey": OWNERSHIP_KEY, "metadataValue": key,
                        "visibility": "DOCUMENT", "location": {"sheetId": next_id},
                    }}},
                ])
                next_id += 1
            prop = managed[key]
            grid = prop.get("gridProperties", {})
            width = max(len(output[key][0]), max((len(row) for row in remote.get(key, [])), default=0))
            height = max(len(output[key]), len(remote.get(key, [])))
            structure.extend([
                {"updateSheetProperties": {"properties": {"sheetId": prop["sheetId"], "gridProperties": {
                    "rowCount": max(int(grid.get("rowCount", 1000)), height),
                    "columnCount": max(int(grid.get("columnCount", 26)), width), "frozenRowCount": 1,
                }}, "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount"}},
                {"updateDimensionProperties": {"range": {"sheetId": prop["sheetId"], "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": 2}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
                {"repeatCell": {"range": {"sheetId": prop["sheetId"], "startRowIndex": 0, "endRowIndex": 1,
                    "endColumnIndex": len(output[key][0])}, "cell": {"userEnteredFormat": {
                        "textFormat": {"bold": True}, "backgroundColor": {"red": 0.87, "green": 0.93, "blue": 0.98},
                    }}, "fields": "userEnteredFormat"}},
                {"setBasicFilter": {"filter": {"range": {"sheetId": prop["sheetId"], "startRowIndex": 0,
                    "endRowIndex": len(output[key]), "startColumnIndex": 0, "endColumnIndex": len(output[key][0])}}}},
            ])
        # Re-read immediately before mutating. Sheets has no conditional atomic
        # compare-and-swap API; this catches edits during a long local merge.
        if existing:
            check = self._request("GET", f"/spreadsheets/{identity}/values:batchGet", params={
                "ranges": [_quoted_title(sheet["title"]) for sheet in existing.values()],
                "valueRenderOption": "FORMULA",
            })
            current = {key: [[str(cell) for cell in row] for row in value.get("values", [])]
                       for key, value in zip(existing, check.get("valueRanges", []))}
            if current != remote:
                return SheetsSyncReport(url, conflicts=("Sheets changed while the sync was being prepared. Finish editing the workbook, then retry.",))
        self._request("POST", f"/spreadsheets/{identity}:batchUpdate", {"requests": structure})
        updates: list[dict[str, Any]] = []
        for key, rows in output.items():
            old = remote.get(key, [])
            width = max(len(rows[0]), max((len(row) for row in old), default=0))
            height = max(len(rows), len(old))
            padded = [(rows[index] if index < len(rows) else []) + [""] * (width - (len(rows[index]) if index < len(rows) else 0))
                      for index in range(height)]
            updates.append({"range": f"{_quoted_title(managed[key]['title'])}!A1:{_column_name(width)}{height}",
                            "majorDimension": "ROWS", "values": padded})
        # RAW preserves exact units, timestamps and text beginning with '='.
        self._request("POST", f"/spreadsheets/{identity}/values:batchUpdate", {"valueInputOption": "RAW", "data": updates})
        return SheetsSyncReport(url, aircraft_written=len(tables), rows_written=len(local),
                               notes_preserved=sum(len(notes) for notes in remote_notes.values()), remote_notes=remote_notes)
