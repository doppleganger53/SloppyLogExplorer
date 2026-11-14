from __future__ import annotations

import copy
import json
from pathlib import Path
import re

import pytest

from sloppy_log_explorer.flight_log_sheets import (
    ALL_SHEET_TITLE,
    BASE_HEADER,
    FlightLogSheet,
    GoogleSheetsClient,
    GoogleSheetsError,
    ID_HEADER,
    OWNERSHIP_KEY,
    PERSONAL_SCOPES,
    _GoogleTokenStore,
    spreadsheet_id,
    stable_flight_id,
)


BOOK_ID = "test_workbook_id_1234567890"
HEADERS = ["Flight number", "Date/time", "Flight duration", "Minimum Rx VFR", "Notes"]


def table(aircraft="Jet", notes="", when="2026-10-06 09:00:00", duration="4:12"):
    return FlightLogSheet(aircraft, HEADERS, [["1", when, duration, "92", notes]])


@pytest.fixture(autouse=True)
def isolated_google_vault(monkeypatch):
    """Authentication tests must never read or mutate the user's native vault."""
    class Vault:
        entries = {}

        def get_password(self, service, identity):
            return self.entries.get((service, identity))

        def set_password(self, service, identity, value):
            self.entries[(service, identity)] = value

        def delete_password(self, service, identity):
            del self.entries[(service, identity)]

    vault = Vault()
    monkeypatch.setattr(_GoogleTokenStore, "_backend", staticmethod(lambda: vault))
    return vault


def test_personal_sign_in_is_remembered_securely_restored_and_disconnected(tmp_path, monkeypatch, isolated_google_vault):
    from datetime import datetime, timedelta, timezone
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import google.auth.transport.requests as auth_requests

    path = tmp_path / "desktop-client.json"
    path.write_text(json.dumps({"installed": {"client_id": "test-app.apps.googleusercontent.com", "client_secret": "test-secret",
                                              "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                              "token_uri": "https://oauth2.googleapis.com/token"}}))
    browser_calls, refresh_calls = [], []
    credentials = Credentials("test-access", refresh_token="test-refresh", token_uri="https://oauth2.googleapis.com/token",
                              client_id="test-app.apps.googleusercontent.com", client_secret="test-secret", scopes=PERSONAL_SCOPES)

    class Flow:
        class OAuthSession:
            def mount(self, *args):
                pass
        oauth2session = OAuthSession()

        def run_local_server(self, **kwargs):
            browser_calls.append(kwargs)
            return credentials

    def flow_config(config, **kwargs):
        assert kwargs["autogenerate_code_verifier"]
        assert kwargs["scopes"] == PERSONAL_SCOPES
        return Flow()

    class Session:
        def get(self, url, **kwargs):
            assert url == "https://openidconnect.googleapis.com/v1/userinfo"
            class Response:
                status_code = 200
                def json(self):
                    return {"email": "pilot@gmail.com", "email_verified": True}
            return Response()

        def close(self):
            pass

    def refresh(self, request):
        refresh_calls.append(request)
        self.token = "refreshed-test-access"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(InstalledAppFlow, "from_client_config", flow_config)
    monkeypatch.setattr(auth_requests, "AuthorizedSession", lambda *args, **kwargs: Session())
    monkeypatch.setattr(Credentials, "refresh", refresh)
    first = GoogleSheetsClient(path)
    first.authenticate()
    assert first.authorization_remembered and first.account_email == "pilot@gmail.com"
    assert len(browser_calls) == 1
    stored = next(iter(isolated_google_vault.entries.values()))
    assert "test-refresh" in stored and "test-access" not in stored
    assert list(tmp_path.iterdir()) == [path]
    first.close()
    restored = GoogleSheetsClient(path)
    restored.authenticate()
    assert len(browser_calls) == 1 and len(refresh_calls) == 1
    assert restored.signed_in and restored.authorization_remembered
    restored.disconnect()
    assert not restored.signed_in and not isolated_google_vault.entries
    assert not restored.authorization_remembered


def test_secure_store_failure_keeps_tokens_in_memory_only(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials
    import google.auth.transport.requests as auth_requests
    path = tmp_path / "desktop-client.json"
    path.write_text(json.dumps({"installed": {"client_id": "test-app.apps.googleusercontent.com",
                                              "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                              "token_uri": "https://oauth2.googleapis.com/token"}}))
    def unavailable():
        raise RuntimeError("private vault error")
    monkeypatch.setattr(_GoogleTokenStore, "_backend", staticmethod(unavailable))
    credentials = Credentials("test-access", refresh_token="test-refresh", token_uri="https://oauth2.googleapis.com/token",
                              client_id="test-app.apps.googleusercontent.com", client_secret="test-secret", scopes=PERSONAL_SCOPES)
    monkeypatch.setattr(GoogleSheetsClient, "_browser_sign_in", staticmethod(lambda *args: credentials))
    monkeypatch.setattr(auth_requests, "AuthorizedSession", lambda *args, **kwargs: object())
    client = GoogleSheetsClient(path)
    client.authenticate()
    assert client.signed_in and not client.authorization_remembered
    assert list(tmp_path.iterdir()) == [path]


class FakeSheets:
    """Stateful REST fake exercises incremental sync rather than replay fixtures."""

    def __init__(self):
        self.sheets = {}
        self.markers = []
        self.values = {}
        self.calls = []

    def add_unmanaged(self, title, values=None):
        sheet_id = max(self.sheets, default=-1) + 1
        self.sheets[sheet_id] = {"sheetId": sheet_id, "title": title,
                                 "gridProperties": {"rowCount": 1000, "columnCount": 26}}
        self.values[title] = values or []

    def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(params)))
        if method == "POST" and path == "/spreadsheets":
            assert body is not None
            prop = body["sheets"][0]["properties"]
            self.sheets[prop["sheetId"]] = dict(prop)
            self.markers.extend(copy.deepcopy(body["developerMetadata"]))
            return {"spreadsheetId": BOOK_ID}
        if method == "GET" and path.endswith("/values:batchGet"):
            assert params is not None
            return {"valueRanges": [{"values": copy.deepcopy(self.values.get(self.title(value), []))}
                                    for value in params["ranges"]]}
        if method == "GET":
            return {"sheets": [{"properties": copy.deepcopy(prop)} for prop in self.sheets.values()],
                    "developerMetadata": copy.deepcopy(self.markers)}
        if path.endswith("/values:batchUpdate"):
            assert body is not None
            assert body["valueInputOption"] == "RAW"
            for update in body["data"]:
                self.values[self.title(update["range"])] = copy.deepcopy(update["values"])
            return {}
        if path.endswith(":batchUpdate"):
            assert body is not None
            for request in body["requests"]:
                if "addSheet" in request:
                    prop = request["addSheet"]["properties"]
                    assert prop["title"] not in self.values
                    self.sheets[prop["sheetId"]] = dict(prop)
                elif "createDeveloperMetadata" in request:
                    self.markers.append(copy.deepcopy(request["createDeveloperMetadata"]["developerMetadata"]))
                elif "updateSheetProperties" in request:
                    prop = request["updateSheetProperties"]["properties"]
                    self.sheets[prop["sheetId"]].update(copy.deepcopy(prop))
            return {}
        raise AssertionError((method, path, body, params))

    @staticmethod
    def title(a1):
        match = re.match(r"'((?:[^']|'')*)'(?:!|$)", a1)
        assert match is not None
        return match.group(1).replace("''", "'")

    def note(self, title, value, row=1):
        values = self.values[title]
        values[row][values[0].index("Notes")] = value

    def written_notes(self, title=ALL_SHEET_TITLE):
        values = self.values[title]
        return [row[values[0].index("Notes")] for row in values[1:] if any(row)]

    def writes_since(self, call_index):
        return [call for call in self.calls[call_index:] if call[0] != "GET"]


@pytest.fixture
def remote_client():
    remote = FakeSheets()
    return remote, GoogleSheetsClient("unused.json", transport=remote)


@pytest.mark.parametrize("value", [BOOK_ID, f"https://docs.google.com/spreadsheets/d/{BOOK_ID}/edit#gid=0",
                                   f"https://docs.google.com/spreadsheets/u/0/d/{BOOK_ID}/edit"])
def test_workbook_id_or_google_url(value):
    assert spreadsheet_id(value) == BOOK_ID


@pytest.mark.parametrize("value", ["short", "../secret", f"http://docs.google.com/spreadsheets/d/{BOOK_ID}/edit",
                                   f"https://evil.test/spreadsheets/d/{BOOK_ID}/edit",
                                   f"https://docs.google.com.evil.test/spreadsheets/d/{BOOK_ID}/edit"])
def test_invalid_workbook_reference_is_rejected(value):
    with pytest.raises(GoogleSheetsError):
        spreadsheet_id(value)


def test_create_workbook_ownership_and_url(remote_client):
    remote, client = remote_client
    book = client.create_workbook("My flight logs")
    assert book.spreadsheet_id == BOOK_ID
    assert book.title == "My flight logs"
    assert book.url.endswith(f"/{BOOK_ID}/edit")
    assert remote.markers[0]["metadataValue"] == "all"
    assert remote.markers[0]["location"] == {"sheetId": 0}


def test_new_sync_builds_combined_and_aircraft_without_touching_existing_tabs(remote_client):
    remote, client = remote_client
    user_data = [["Budget", "Private"]]
    remote.add_unmanaged(ALL_SHEET_TITLE, user_data)
    remote.add_unmanaged("SLE - Jet", [["user tab"]])
    result = client.sync_workbook(BOOK_ID, [table(notes="=HYPERLINK(\"https://example.test\")"), table("Trainer")])
    assert result.succeeded
    assert result.rows_written == 2
    assert result.aircraft_written == 2
    assert remote.values[ALL_SHEET_TITLE] == user_data
    assert remote.values["SLE - Jet"] == [["user tab"]]
    assert "SLE - All flights (2)" in remote.values
    assert "SLE - Jet (2)" in remote.values
    assert remote.written_notes("SLE - Jet (2)")[0].startswith("=HYPERLINK")
    assert {marker["metadataKey"] for marker in remote.markers} == {OWNERSHIP_KEY}
    assert len(remote.markers) == 3


def test_repeated_sync_does_not_add_duplicate_rows_or_tabs(remote_client):
    remote, client = remote_client
    first = client.sync_workbook(BOOK_ID, [table(notes="Good landing")])
    before = copy.deepcopy(remote.values)
    second = client.sync_workbook(BOOK_ID, [table(notes="Good landing")])
    assert first.succeeded and second.succeeded
    assert remote.values == before
    assert len(remote.sheets) == 2
    assert len(remote.values[ALL_SHEET_TITLE]) == 2


def test_column_order_is_published_in_both_views_and_remote_notes_survive(remote_client):
    remote, client = remote_client
    source = table(notes="Original")
    client.sync_workbook(BOOK_ID, [source])
    remote.note("SLE - Jet", "Edited in Sheets")
    order = ["Notes", "Flight duration", "Minimum Rx VFR", "Date/time", "Flight number"]
    indices = [list(source.headers).index(header) for header in order]
    reordered = FlightLogSheet("Jet", order, [[row[index] for index in indices] for row in source.rows])
    result = client.sync_workbook(BOOK_ID, [reordered])
    assert result.succeeded and result.notes_preserved == 1
    assert remote.values["SLE - Jet"][0][2:] == order
    assert remote.values[ALL_SHEET_TITLE][0][3:] == order
    assert remote.written_notes("SLE - Jet") == ["Edited in Sheets"]
    assert remote.written_notes() == ["Edited in Sheets"]


@pytest.mark.parametrize("changed_tab", [ALL_SHEET_TITLE, "SLE - Jet"])
def test_remote_notes_edits_are_preserved_in_both_views(remote_client, changed_tab):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Original")])
    remote.note(changed_tab, "Pilot correction")
    result = client.sync_workbook(BOOK_ID, [table(notes="Original")])
    identity = stable_flight_id("Jet", "2026-10-06 09:00:00", "4:12")
    assert result.succeeded
    assert result.notes_preserved == 1
    assert result.remote_notes == {"Jet": {identity: "Pilot correction"}}
    assert remote.written_notes() == ["Pilot correction"]
    assert remote.written_notes("SLE - Jet") == ["Pilot correction"]
    repeated = client.sync_workbook(BOOK_ID, [table(notes="Original")])
    assert repeated.succeeded
    assert remote.written_notes() == ["Pilot correction"]


def test_remote_note_deletion_is_preserved_and_reported(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Original")])
    remote.note("SLE - Jet", "")
    result = client.sync_workbook(BOOK_ID, [table(notes="Original")])
    assert result.succeeded
    assert result.notes_preserved == 1
    assert list(result.remote_notes["Jet"].values()) == [""]
    assert remote.written_notes() == [""]


def test_local_only_notes_change_is_published(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Original")])
    result = client.sync_workbook(BOOK_ID, [table(notes="Local edit")])
    assert result.succeeded
    assert result.remote_notes == {}
    assert remote.written_notes() == ["Local edit"]


def test_simultaneous_notes_edits_stop_before_any_remote_write(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Original")])
    remote.note("SLE - Jet", "Remote edit")
    before = len(remote.calls)
    result = client.sync_workbook(BOOK_ID, [table(notes="Local edit"), table("New plane")])
    assert not result.succeeded
    assert result.rows_written == 0
    assert "both changed" in result.conflicts[0]
    assert remote.writes_since(before) == []
    assert len(remote.sheets) == 2


def test_different_notes_in_two_remote_views_stop_sync(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table()])
    remote.note(ALL_SHEET_TITLE, "Combined edit")
    remote.note("SLE - Jet", "Aircraft edit")
    before = len(remote.calls)
    result = client.sync_workbook(BOOK_ID, [table()])
    assert not result.succeeded
    assert "combined and aircraft" in result.conflicts[0]
    assert remote.writes_since(before) == []


def test_partial_aircraft_sync_preserves_other_aircraft_in_combined_view(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(), table("Trainer", notes="Keep this")])
    trainer_before = copy.deepcopy(remote.values["SLE - Trainer"])
    result = client.sync_workbook(BOOK_ID, [table(notes="New local note")])
    assert result.succeeded
    assert result.aircraft_written == 1
    combined = remote.values[ALL_SHEET_TITLE]
    assert len(combined) == 3
    assert set(remote.written_notes()) == {"New local note", "Keep this"}
    assert remote.values["SLE - Trainer"] == trainer_before


def test_missing_local_flight_stops_sync_to_preserve_remote_notes(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Preserve")])
    before = len(remote.calls)
    result = client.sync_workbook(BOOK_ID, [FlightLogSheet("Jet", HEADERS, [])])
    assert not result.succeeded
    assert "absent from the local log" in result.conflicts[0]
    assert remote.writes_since(before) == []


def test_corrupted_row_identity_or_headers_stop_before_writes(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table()])
    remote.values["SLE - Jet"][1][0] = "wrong identity"
    before = len(remote.calls)
    result = client.sync_workbook(BOOK_ID, [table()])
    assert not result.succeeded
    assert "row identity" in result.conflicts[0]
    assert remote.writes_since(before) == []
    remote.values["SLE - Jet"][0][0] = "Changed header"
    result = client.sync_workbook(BOOK_ID, [table()])
    assert any("headers were changed" in conflict for conflict in result.conflicts)


def test_schema_change_preserves_notes_and_blanks_old_cells(remote_client):
    remote, client = remote_client
    old_headers = [*HEADERS[:-1], "Maximum Current(A)", "Notes"]
    old = FlightLogSheet("Jet", old_headers, [["1", "2026-10-06 09:00:00", "4:12", "92", "33", ""]])
    client.sync_workbook(BOOK_ID, [old])
    remote.note("SLE - Jet", "Pilot note")
    result = client.sync_workbook(BOOK_ID, [table()])
    assert result.succeeded
    plane = remote.values["SLE - Jet"]
    assert plane[0][-1] == ""
    assert "Maximum Current(A)" not in plane[0]
    assert "Maximum Current(A)" not in remote.values[ALL_SHEET_TITLE][0]
    assert remote.written_notes("SLE - Jet") == ["Pilot note"]


def test_tab_titles_handle_invalid_characters_and_apostrophes(remote_client):
    remote, client = remote_client
    result = client.sync_workbook(BOOK_ID, [table("Pilot's [Jet]/Demo")])
    assert result.succeeded
    assert "SLE - Pilot's -Jet--Demo" in remote.values
    assert client.sync_workbook(BOOK_ID, [table("Pilot's [Jet]/Demo")]).succeeded


def test_duplicate_local_flight_rejected_without_network(remote_client):
    remote, client = remote_client
    single = table()
    with pytest.raises(GoogleSheetsError, match="duplicate date/time"):
        client.sync_workbook(BOOK_ID, [FlightLogSheet("Jet", HEADERS, [*single.rows, *single.rows])])
    assert remote.calls == []


def test_credentials_read_errors_do_not_disclose_local_paths_or_contents(tmp_path):
    credential_path = tmp_path / "private-credentials.json"
    credential_path.write_text("private_key: sensitive", encoding="utf-8")
    with pytest.raises(GoogleSheetsError) as exc:
        GoogleSheetsClient(credential_path).authenticate()
    assert "private_key" not in str(exc.value)
    assert str(credential_path) not in str(exc.value)


def test_auth_rejects_untrusted_endpoint_before_network(tmp_path, monkeypatch):
    # Exercise validation without requiring real Google credentials or sign-in.
    pytest.importorskip("google_auth_oauthlib")
    credential_path = tmp_path / "credentials.json"
    credential_path.write_text(json.dumps({"installed": {"auth_uri": "https://evil.test/auth",
                                                         "token_uri": "https://evil.test/token"}}))
    with pytest.raises(GoogleSheetsError, match="unsupported Google authentication endpoint"):
        GoogleSheetsClient(credential_path).authenticate()


def test_missing_credentials_type_is_actionable(tmp_path):
    pytest.importorskip("google_auth_oauthlib")
    credential_path = tmp_path / "credentials.json"
    credential_path.write_text("{}")
    with pytest.raises(GoogleSheetsError, match="Desktop OAuth"):
        GoogleSheetsClient(credential_path).authenticate()


def test_stable_identity_ignores_padding():
    assert stable_flight_id(" Jet ", " 2026-10-06 09:00:00 ", "4:12 ") == stable_flight_id("Jet", "2026-10-06 09:00:00", "4:12")


def test_concurrent_sheets_edit_detected_before_writes(remote_client):
    remote, client = remote_client
    client.sync_workbook(BOOK_ID, [table(notes="Original")])
    before = len(remote.calls)
    reads = 0

    def concurrent_transport(method, path, body, params):
        nonlocal reads
        if path.endswith("/values:batchGet"):
            reads += 1
            if reads == 2:
                remote.note("SLE - Jet", "Concurrent edit")
        return remote(method, path, body, params)

    client._transport = concurrent_transport
    result = client.sync_workbook(BOOK_ID, [table(notes="Original")])
    assert not result.succeeded
    assert "changed while the sync" in result.conflicts[0]
    assert remote.writes_since(before) == []


def test_oauth_sign_in_is_bounded_and_keeps_tokens_in_memory(tmp_path, monkeypatch):
    flow_module = pytest.importorskip("google_auth_oauthlib.flow")
    import google.auth.transport.requests as auth_requests

    credential_path = tmp_path / "desktop-client.json"
    contents = json.dumps({"installed": {"auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                         "token_uri": "https://oauth2.googleapis.com/token"}})
    credential_path.write_text(contents)
    calls = []

    class OAuthSession:
        def mount(self, prefix, adapter):
            calls.append((prefix, adapter))

    class Flow:
        oauth2session = OAuthSession()

        def run_local_server(self, **kwargs):
            calls.append(kwargs)
            return "fake credentials"

    monkeypatch.setattr(flow_module.InstalledAppFlow, "from_client_config", lambda *a, **kw: Flow())
    monkeypatch.setattr(auth_requests, "AuthorizedSession", lambda *a, **kw: object())
    client = GoogleSheetsClient(credential_path)
    client.authenticate()
    client.authenticate()
    assert len(calls) == 2
    assert calls[1]["timeout_seconds"] == 120
    assert calls[1]["host"] == "127.0.0.1"
    assert calls[1]["port"] == 0
    assert calls[1]["authorization_prompt_message"] == ""
    assert credential_path.read_text() == contents
    assert list(tmp_path.iterdir()) == [credential_path]


def test_service_account_create_refusal_is_actionable(remote_client):
    remote, client = remote_client
    client._credential_kind = "service_account"
    with pytest.raises(GoogleSheetsError, match="cannot create My Drive"):
        client.create_workbook("Flight logs")
    assert remote.calls == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_api_errors_hide_response_credentials(status):
    pytest.importorskip("google_auth_oauthlib")

    class Response:
        status_code = status

        def json(self):
            raise AssertionError("An error response body must not be exposed")

    class Session:
        closed = False

        def request(self, *args, **kwargs):
            return Response()

        def close(self):
            self.closed = True

    session = Session()
    client = GoogleSheetsClient("unused.json")
    client._session = session
    with pytest.raises(GoogleSheetsError) as exc:
        client._request("GET", "/spreadsheets/private_workbook")
    assert "private_workbook" not in str(exc.value)
    if status == 401:
        assert session.closed
        assert client._session is None


def test_revoked_auth_clears_session_and_hides_token():
    pytest.importorskip("google_auth_oauthlib")
    from google.auth.exceptions import RefreshError

    class Session:
        closed = False

        def request(self, *args, **kwargs):
            raise RefreshError("secret refresh token")

        def close(self):
            self.closed = True

    session = Session()
    client = GoogleSheetsClient("unused.json")
    client._session = session
    with pytest.raises(GoogleSheetsError, match="Sign in again") as exc:
        client._request("GET", "/spreadsheets/private_workbook")
    assert "secret" not in str(exc.value)
    assert session.closed and client._session is None
