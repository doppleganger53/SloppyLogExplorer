from __future__ import annotations

import csv
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sloppy_log_explorer.flight_logs import FlightLogService, effective_policy, infer_aircraft
from sloppy_log_explorer._flight_log_engine import servo_current_headers
from sloppy_log_explorer import _flight_log_engine as engine
from sloppy_log_explorer.library import NONFLIGHT_ARCHIVE, scan_library
from sloppy_log_explorer.sync import discover_sync_candidates


def telemetry(path: Path, *, windows: tuple[tuple[int, int], ...] = ((60, 180),),
              day: int = 1, direct: bool = True, ground: bool = False,
              delimiter: str = ",") -> None:
    headers = ["Date", "Time", "Rx VFR(%)", "VFR 2.4G(%)", "Rudder", "Elevator", "Throttle", "Aileron",
               "Current(A)", "Rx Current(A)", "Temp2(°C)", "Unused temp(°C)"]
    if direct:
        headers += ["GPS speed(mph)", "GPS alt(m)"]
    start = datetime(2026, 7, day, 9)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        for second in range(max(window[1] for window in windows) + 80):
            active = any(begin <= second <= end for begin, end in windows)
            phase = second % 20
            timestamp = start + timedelta(seconds=second)
            if direct:
                link = 80 + second % 21
            else:
                # One departure/recovery pattern remains uncertain despite
                # dynamic primary controls and a running motor.
                begin, end = windows[0]
                progress = (second - begin) / max(end - begin, 1)
                link = 100 - 35 * min(progress, 1 - progress) * 2
            row = [timestamp.strftime("%Y-%m-%d"), timestamp.strftime("%H:%M:%S"),
                   link if active else 0, (70 + second % 31 if direct else link - 10) if active else 0,
                   -500 + phase * 50 if active else 0, 400 - phase * 45 if active else 0,
                   200 if active else -1024, -450 + phase * 48 if active else 0,
                   10 + phase if active else 1000, 1, 40 + phase if active else 500, -1024]
            if direct:
                row += [35 if active and not ground else 0,
                        50 + phase * (0.05 if ground else 2) if active else 0]
            writer.writerow(row)


def service(tmp_path: Path) -> FlightLogService:
    return FlightLogService(tmp_path / "flight-logs")


@pytest.mark.parametrize(("headers", "expected"), [
    (["ELE L curr(A)", "ELE L temp(°F)", "RUD R curr(A)", "RUD R temp(°F)"], {"ELE L curr(A)", "RUD R curr(A)"}),
    (["SRV3 curr(mA)", "SRV3 temp(°C)", "SRV4 curr(A)"], {"SRV3 curr(mA)"}),
    (["Left_Ail Current(A)", "left-ail Temperature(°C)"], {"Left_Ail Current(A)"}),
    (["Rudder(A)", "Rudder(°F)"], {"Rudder(A)"}),
    (["SRV curr1(A)", "SRV temp2(°C)"], set()),
    (["SRV curr1(A)", "SRV temp1(°C)"], {"SRV curr1(A)"}),
    (["SRV curr(A).1", "SRV temp(°C)"], set()),
    (["SRV curr(A).1", "SRV temp(°C).1"], {"SRV curr(A).1"}),
    (["Current(A)", "Temp(°C)", "Rx Current(A)", "Rx Temp(°C)", "ESC current(A)", "ESC temp(°C)",
      "BEC curr(A)", "BEC temp(°C)", "Motor current(A)", "Motor temp(°C)"], set()),
    (["Left curr(A)", "Right temp(°C)", "Left voltage(V)", "Left consumption(mAh)"], set()),
])
def test_servo_current_pairing_retains_custom_names_and_avoids_power_channels(headers, expected) -> None:
    assert servo_current_headers(headers) == expected


def servo_telemetry(path: Path) -> None:
    telemetry(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    rows[0] += ["ELE L curr(A)", "ELE L temp(°F)", "SRV3 curr(mA)", "SRV3 temp(°C)",
                "ESC current(A)", "ESC temp(°C)", "Unpaired curr(A)"]
    for second, row in enumerate(rows[1:]):
        active = 60 <= second <= 180
        phase = second % 20
        # Large ground readings, sentinel/nonfinite samples and unpaired/power
        # channels must not become servo flight maxima.
        first = "nan" if second == 100 else "inf" if second == 101 else -1024 if second == 102 else phase / 10
        row += [first if active else 1000, 112 + phase if active else 500,
                200 + phase * 10 if active else 99999, 40 + phase if active else 500,
                30, 60, 20]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def test_servo_maxima_default_to_paired_channels_and_only_finite_in_flight_samples(tmp_path: Path) -> None:
    source = tmp_path / "ServoFlight.csv"
    servo_telemetry(source)
    logs = service(tmp_path)
    preview = logs.preview({source: "F-14"})
    candidate = preview.candidates[0]
    assert candidate["sensor_maximums"] == {"ELE L curr(A)": 1.9, "SRV3 curr(mA)": 390}
    assert candidate["metrics"]["Maximum ELE L curr(A)"] == 1.9
    assert "Maximum Current(A)" not in preview.updates[0]["headers"]
    assert "Maximum ESC current(A)" not in preview.updates[0]["headers"]
    logs.generate(preview)
    document = logs.read_log("F-14")
    assert document.rows[0][document.headers.index("Maximum ELE L curr(A)")] == "1.9"
    assert document.rows[0][document.headers.index("Maximum SRV3 curr(mA)")] == "390"
    assert logs.generate(logs.preview({source: "F-14"}))["rows_added"] == 0


def test_servo_policy_opt_out_and_exclusions_do_not_change_detection(tmp_path: Path) -> None:
    source = tmp_path / "ServoFlight.csv"
    servo_telemetry(source)
    logs = service(tmp_path)
    default = logs.preview({source: "F-14"}).candidates[0]
    opt_out = logs.preview({source: "F-14"}, settings={"planes": {"f-14": {"auto_servo_currents": False}}}).candidates[0]
    assert not opt_out["sensor_maximums"]
    assert opt_out["candidate_id"] == default["candidate_id"]
    rejected = logs.preview({source: "F-14"}, decisions={opt_out["candidate_id"]: "reject"})
    assert rejected.candidates[0]["decision"] == "reject"
    assert (opt_out["start"], opt_out["duration"], opt_out["confidence"]) == (default["start"], default["duration"], default["confidence"])
    settings = {"defaults": {"exclude_sensors": ["ELE L curr(A)", "SRV3 temp(°C)"],
                              "max_sensors": ["ELE L curr(A)"], "auto_temperatures": False}}
    filtered = logs.preview({source: "F-14"}, settings=settings).candidates[0]
    assert filtered["sensor_maximums"] == {"SRV3 curr(mA)": 390}
    assert not filtered["temperature_maximums"]
    with pytest.raises(ValueError, match="must be true or false"):
        logs.save_settings({"defaults": {"auto_servo_currents": "false"}})


def test_existing_servo_log_requires_reviewed_rebuild_to_add_default_current_columns(tmp_path: Path) -> None:
    source = tmp_path / "ServoFlight.csv"
    servo_telemetry(source)
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "F-14"}, settings={"defaults": {"auto_servo_currents": False}}))
    document = logs.save_notes("F-14", 1, "Keep pilot trim notes")
    before = document.path.read_bytes()
    append = logs.preview({source: "F-14"})
    assert not append.can_generate and "reviewed rebuild" in append.updates[0]["error"]
    assert document.path.read_bytes() == before
    rebuild = logs.preview({source: "F-14"}, rebuild=True)
    assert set(rebuild.updates[0]["schema_changes"]["added"]) == {"Maximum ELE L curr(A)", "Maximum SRV3 curr(mA)"}
    with pytest.raises(ValueError, match="explicit approval"):
        logs.generate(rebuild)
    logs.generate(rebuild, approve_rebuild=True)
    rebuilt = logs.read_log("F-14")
    assert rebuilt.rows[0][rebuilt.headers.index("Notes")] == "Keep pilot trim notes"


def test_reordered_columns_preserve_cells_notes_and_surrounding_text_then_append(tmp_path: Path) -> None:
    source, later = tmp_path / "Flight.csv", tmp_path / "Later.csv"
    telemetry(source)
    telemetry(later, day=2)
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "Falcon"}))
    document = logs.save_notes("Falcon", 1, "Pilot | checked engine; keep this")
    with document.path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(document.text.replace("\n", "\r\n") + "\r\nPilot maintenance history.\r\n")
    document = logs.read_log("Falcon")
    original_values = dict(zip(document.headers, document.rows[0]))
    order = ["Notes", *reversed(document.headers[:-1])]
    reordered = logs.reorder_columns("Falcon", order, document.fingerprint)
    assert reordered.headers == order
    assert dict(zip(reordered.headers, reordered.rows[0])) == original_values
    assert reordered.text.endswith("\r\nPilot maintenance history.\r\n")
    assert "Pilot \\| checked engine; keep this" in reordered.text
    preview = logs.preview({source: "Falcon", later: "Falcon"})
    assert preview.report["rows_added"] == 1 and preview.report["duplicates"] == 1
    logs.generate(preview)
    final = logs.read_log("Falcon")
    assert final.headers == order
    assert dict(zip(final.headers, final.rows[0])) == original_values
    assert final.rows[1][final.headers.index("Flight number")] == "2"
    assert final.rows[1][final.headers.index("Date/time")].startswith("2026-07-02")
    final = logs.save_notes("Falcon", 2, "Later pilot notes", final.fingerprint)
    logs.save_notes_many({"Falcon": {1: "Reviewed Sheets | note"}}, {"Falcon": final.fingerprint})
    final = logs.read_log("Falcon")
    assert final.rows[0][final.headers.index("Notes")] == "Reviewed Sheets | note"
    assert final.rows[1][final.headers.index("Notes")] == "Later pilot notes"
    assert logs.export_workbook_rows()["Falcon"][0] == order


def test_new_log_layout_can_be_reviewed_without_rescanning_or_accepting_uncertain_flights(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source, direct=False, windows=((30, 270),))
    logs = service(tmp_path)
    preview = logs.preview({source: "Glider"})
    # A pending flight has no output table until the pilot accepts it.
    candidate_id = preview.pending_candidates[0]
    preview = logs.with_decisions(preview, {candidate_id: "accept"})
    order = list(reversed(preview.updates[0]["headers"]))
    reordered = logs.with_column_order(preview, "Glider", order)
    assert not reordered.pending_candidates
    assert reordered.candidates[0]["confidence"] == "uncertain"
    assert reordered.candidates[0]["candidate_id"] == candidate_id
    assert reordered.updates[0]["headers"] == order
    assert not logs.output_dir.exists()
    logs.save_settings(reordered._settings)
    logs.generate(reordered)
    assert logs.read_log("Glider").headers == order
    assert logs.load_settings()["column_orders"]["Glider"] == order


def test_reorder_rejects_stale_or_invalid_layout_without_touching_file(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "Falcon"}))
    document = logs.read_log("Falcon")
    with pytest.raises(ValueError, match="exactly once"):
        logs.reorder_columns("Falcon", ["Notes"] * len(document.headers), document.fingerprint)
    changed = logs.save_notes("Falcon", 1, "New pilot edit")
    before = changed.path.read_bytes()
    with pytest.raises(ValueError, match="changed since"):
        logs.reorder_columns("Falcon", list(reversed(document.headers)), document.fingerprint)
    assert changed.path.read_bytes() == before


def test_selected_aircraft_exact_headers_in_window_and_repeat_preserves_notes(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    other = tmp_path / "Other.csv"
    telemetry(source, delimiter=";")
    telemetry(other)
    logs = service(tmp_path)
    preview = logs.preview({source: "Falcon", other: "Trainer"}, selected_aircraft=["falcon"],
                           settings={"defaults": {"max_sensors": ["Current(A)"]}})
    assert preview.report["files_scanned"] == 1
    assert len(preview.candidates) == 1
    candidate = preview.candidates[0]
    assert candidate["start"] == "2026-07-01 09:01:00"
    assert candidate["duration"] == "00:02:00"
    assert candidate["metrics"]["Maximum Current(A)"] == 29
    assert candidate["metrics"]["Maximum Temp2(°C)"] == 59
    assert "Maximum Rx Current(A)" not in candidate["metrics"]
    assert "Maximum Unused temp(°C)" not in candidate["metrics"]
    result = logs.generate(preview)
    assert result["rows_added"] == 1
    assert logs.list_aircraft() == ["Falcon"]
    document = logs.read_log("Falcon")
    logs.save_notes("Falcon", 1, "Changed prop | smooth landing", document.fingerprint)
    before = document.path.read_bytes()
    repeated = logs.generate(logs.preview({source: "Falcon"}, settings={"defaults": {"max_sensors": ["Current(A)"]}}))
    assert repeated["rows_added"] == 0
    assert repeated["duplicates"] == 1
    assert document.path.read_bytes() == before
    assert logs.export_workbook_rows()["Falcon"][-1][-1] == "Changed prop | smooth landing"


def test_uncertain_candidate_blocks_all_aircraft_until_explicit_decision(tmp_path: Path) -> None:
    uncertain = tmp_path / "Uncertain.csv"
    confirmed = tmp_path / "Confirmed.csv"
    telemetry(uncertain, direct=False, windows=((30, 270),))
    telemetry(confirmed)
    logs = service(tmp_path)
    preview = logs.preview({uncertain: "Glider", confirmed: "Falcon"})
    assert len(preview.pending_candidates) == 1
    assert not preview.can_generate
    with pytest.raises(ValueError, match="uncertain candidate"):
        logs.generate(preview)
    assert not logs.output_dir.exists()
    candidate_id = preview.pending_candidates[0]
    accepted = logs.with_decisions(preview, {candidate_id: "accept"})
    assert accepted.can_generate
    assert not accepted.pending_candidates
    reset = logs.with_decisions(accepted, {})
    assert reset.pending_candidates == [candidate_id]
    result = logs.generate(accepted)
    assert result["rows_added"] == 2
    assert "Uncertain flight" in logs.read_log("Glider").rows[0][-1]
    assert next(candidate for candidate in accepted.candidates if candidate["id"] == candidate_id)["confidence"] == "uncertain"


def test_ground_candidates_are_rejected_and_never_promoted(tmp_path: Path) -> None:
    source = tmp_path / "Bench.csv"
    telemetry(source, ground=True)
    logs = service(tmp_path)
    preview = logs.preview({source: "Falcon"})
    assert not preview.candidates
    assert preview.report["rejected_candidates"] == 1
    with pytest.raises(ValueError, match="unknown"):
        logs.with_decisions(preview, {"ground": "accept"})
    assert not logs.output_dir.exists()


@pytest.mark.parametrize("changed", ["source", "output"])
def test_stale_review_blocks_before_any_write(tmp_path: Path, changed: str) -> None:
    source = tmp_path / "Flight.csv"
    other = tmp_path / "Other.csv"
    telemetry(source)
    telemetry(other)
    logs = service(tmp_path)
    if changed == "output":
        logs.generate(logs.preview({source: "Falcon"}))
    preview = logs.preview({source: "Falcon", other: "Trainer"})
    target = source if changed == "source" else logs.read_log("Falcon").path
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed since preview"):
        logs.generate(preview)
    assert "Trainer" not in logs.list_aircraft()


def test_candidate_ids_survive_moves_but_change_with_policy_or_content(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    first = logs.preview({source: "Falcon"}).candidates[0]["id"]
    moved = tmp_path / "Moved.csv"
    moved.write_bytes(source.read_bytes())
    assert logs.preview({moved: "falcon"}).candidates[0]["id"] == first
    changed_policy = logs.preview({source: "Falcon"}, settings={"defaults": {"auto_vfr": False}})
    assert changed_policy.candidates[0]["id"] != first
    source.write_bytes(source.read_bytes() + b"\n")
    changed_content = logs.preview({source: "Falcon"})
    assert changed_content.candidates[0]["id"] != first
    with pytest.raises(ValueError, match="stale or unknown"):
        logs.with_decisions(changed_content, {first: "accept"})


def test_sensor_overrides_disable_auto_capture_without_changing_detection(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    settings = {"defaults": {"max_sensors": ["Current(A)"], "exclude_sensors": ["Temp2(°C)"]},
                "planes": {"falcon": {"auto_vfr": False, "auto_temperatures": False,
                                      "max_sensors": ["Rx Current(A)", "Temp2(°C)"],
                                      "exclude_sensors": ["Rx Current(A)"]}}}
    preview = logs.preview({source: "Falcon"}, settings=settings)
    assert len(preview.candidates) == 1
    assert preview.candidates[0]["confidence"] == "confirmed"
    assert preview.updates[0]["headers"] == ["Flight number", "Date/time", "Flight duration", "Maximum Temp2(°C)", "Notes"]
    assert "Current(A)" in preview.report["available_headers"]["Falcon"]


def test_excluding_vfr_header_changes_capture_and_keeps_flight_detection(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    preview = logs.preview({source: "Falcon"}, settings={"defaults": {"exclude_sensors": ["Rx VFR(%)"]}})
    assert len(preview.candidates) == 1
    assert "Minimum Rx VFR" not in preview.updates[0]["headers"]
    assert "Minimum 2.4GHz VFR" in preview.updates[0]["headers"]


def test_schema_change_requires_reviewed_rebuild_preserves_notes_and_surrounding_text(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "Falcon"}))
    document = logs.save_notes("Falcon", 1, "Pilot checked engine")
    document.path.write_text(document.text + "\nPilot maintenance history remains here.\n", encoding="utf-8")
    settings = {"defaults": {"max_sensors": ["Current(A)"]}}
    append = logs.preview({source: "Falcon"}, settings=settings)
    assert not append.can_generate
    assert "reviewed rebuild" in append.updates[0]["error"]
    rebuild = logs.preview({source: "Falcon"}, settings=settings, rebuild=True)
    assert rebuild.updates[0]["schema_changes"]["added"] == ["Maximum Current(A)"]
    with pytest.raises(ValueError, match="explicit approval"):
        logs.generate(rebuild)
    result = logs.generate(rebuild, approve_rebuild=True)
    assert result["rows_added"] == 0
    document = logs.read_log("Falcon")
    assert document.rows[0][-1] == "Pilot checked engine"
    assert document.text.splitlines()[-1] == "Pilot maintenance history remains here."


def test_rebuild_lists_removed_rows_and_unmatched_notes_and_all_rejected_leaves_log(tmp_path: Path) -> None:
    source = tmp_path / "Multi.csv"
    telemetry(source, windows=((30, 120), (270, 390)))
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "Falcon"}))
    document = logs.save_notes("Falcon", 1, "Keep this note")
    preview = logs.preview({source: "Falcon"}, rebuild=True)
    first, second = preview.candidates
    reviewed = logs.with_decisions(preview, {first["id"]: "reject", second["id"]: "accept"})
    assert reviewed.updates[0]["removed"][0]["notes"] == "Keep this note"
    assert reviewed.updates[0]["unmatched_notes"] == reviewed.updates[0]["removed"]
    rejected = logs.with_decisions(preview, {first["id"]: "reject", second["id"]: "reject"})
    assert not rejected.updates
    before = document.path.read_bytes()
    logs.generate(rejected, approve_rebuild=True)
    assert document.path.read_bytes() == before


def test_rebuild_keeps_uncertain_callout_with_blank_or_human_matching_notes(tmp_path: Path) -> None:
    source = tmp_path / "Uncertain.csv"
    telemetry(source, direct=False, windows=((30, 270),))
    logs = service(tmp_path)
    preview = logs.preview({source: "Glider"})
    decisions = {preview.candidates[0]["id"]: "accept"}
    logs.generate(logs.with_decisions(preview, decisions))
    for note in ("", "Pilot comment", "Pilot comment; Uncertain flight"):
        logs.save_notes("Glider", 1, note)
        rebuild = logs.preview({source: "Glider"}, decisions=decisions, rebuild=True)
        logs.generate(rebuild, approve_rebuild=True)
        notes = logs.read_log("Glider").rows[0][-1]
        assert notes.count("Uncertain flight") == 1
        if note:
            assert notes.startswith(note)


def test_late_conflict_is_reported_without_changing_history(tmp_path: Path) -> None:
    earlier, later = tmp_path / "Earlier.csv", tmp_path / "Later.csv"
    telemetry(earlier, day=1)
    telemetry(later, day=2)
    logs = service(tmp_path)
    logs.generate(logs.preview({later: "Falcon"}))
    before = logs.read_log("Falcon").path.read_bytes()
    result = logs.generate(logs.preview({earlier: "Falcon", later: "Falcon"}))
    assert result["late_conflicts"] == 1
    assert result["rows_added"] == 0
    assert logs.read_log("Falcon").path.read_bytes() == before


def test_duplicate_source_files_add_one_row_on_first_run(tmp_path: Path) -> None:
    source, copy = tmp_path / "Flight.csv", tmp_path / "Copy.csv"
    telemetry(source)
    copy.write_bytes(source.read_bytes())
    logs = service(tmp_path)
    report = logs.generate(logs.preview({source: "Falcon", copy: "Falcon"}))
    assert report["rows_added"] == 1
    assert report["duplicates"] == 1
    assert len(logs.read_log("Falcon").rows) == 1


def test_read_errors_do_not_block_unaffected_append_but_block_incomplete_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    missing = tmp_path / "Missing.csv"
    telemetry(source)
    logs = service(tmp_path)
    preview = logs.preview({source: "Falcon", missing: "Trainer"})
    assert preview.report["read_errors"] == 1
    result = logs.generate(preview)
    assert result["rows_added"] == 1
    rebuild = logs.preview({source: "Falcon", missing: "Falcon"}, rebuild=True)
    assert not rebuild.can_generate
    assert "telemetry read errors" in rebuild.updates[0]["error"]


def test_settings_round_trip_reset_inheritance_and_keep_unknown_metadata(tmp_path: Path) -> None:
    logs = service(tmp_path)
    logs.save_settings({"external": "retain", "planes": {"Falcon": {"max_sensors": ["Current(A)"], "pilot": "Kurt"}}})
    logs.save_settings({"defaults": {"max_sensors": ["Rx Current(A)"]}, "planes": {}})
    settings = logs.load_settings()
    assert settings["external"] == "retain"
    assert settings["planes"] == {"Falcon": {"pilot": "Kurt"}}
    assert effective_policy(settings, "Falcon")["max_sensors"] == ["Rx Current(A)"]
    logs.save_settings({"planes": {"falcon": {"max_sensors": ["Current(A)"]}}})
    assert logs.load_settings()["planes"]["Falcon"]["pilot"] == "Kurt"
    with pytest.raises(ValueError, match="must be true or false"):
        logs.save_settings({"defaults": {"auto_vfr": "false"}})


def test_slug_collision_and_stale_note_edit_do_not_overwrite_logs(tmp_path: Path) -> None:
    source, other = tmp_path / "Flight.csv", tmp_path / "Other.csv"
    telemetry(source)
    telemetry(other)
    logs = service(tmp_path)
    with pytest.raises(ValueError, match="same output filename"):
        logs.preview({source: "A B", other: "A-B"})
    logs.generate(logs.preview({source: "Falcon"}))
    original = logs.read_log("Falcon")
    logs.save_notes("Falcon", 1, "A newer note")
    with pytest.raises(ValueError, match="changed since it was opened"):
        logs.save_notes("Falcon", 1, "Stale note", original.fingerprint)
    assert logs.read_log("Falcon").rows[0][-1] == "A newer note"


def test_nonconforming_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "Flight.csv"
    telemetry(source)
    logs = service(tmp_path)
    logs.output_dir.mkdir()
    path = logs.output_dir / "Falcon-flight-log.md"
    path.write_text("# Falcon Flight Log\n\nHandwritten record", encoding="utf-8")
    before = path.read_bytes()
    preview = logs.preview({source: "Falcon"}, rebuild=True)
    assert not preview.can_generate
    logs.generate(preview, approve_rebuild=True)
    assert path.read_bytes() == before


def test_filename_mapping_takes_priority_over_archive_folder(tmp_path: Path) -> None:
    path = tmp_path / "archive" / "SW-falcon-2026-07-01-09-00-00.csv"
    assert infer_aircraft(path, tmp_path) == "SW-falcon"
    assert infer_aircraft(tmp_path / "Trainer" / "recording.csv", tmp_path) == "Trainer"


@pytest.mark.parametrize("empty_table", [False, True])
def test_append_to_table_without_final_newline_keeps_rows_separate(tmp_path: Path, empty_table: bool) -> None:
    earlier, later = tmp_path / "Earlier.csv", tmp_path / "Later.csv"
    telemetry(earlier, day=1)
    telemetry(later, day=2)
    logs = service(tmp_path)
    logs.generate(logs.preview({earlier: "Falcon"}))
    document = logs.read_log("Falcon")
    lines = document.text.splitlines()
    original = "\n".join(lines[:4] if empty_table else lines)
    document.path.write_bytes(original.encode("utf-8"))
    logs.generate(logs.preview({later: "Falcon"}))
    saved = logs.read_log("Falcon")
    assert saved.text.startswith(original + "\n")
    assert len(saved.rows) == (1 if empty_table else 2)
    assert saved.rows[-1][0] == ("1" if empty_table else "2")


def test_batch_notes_validate_all_rows_and_fingerprints_before_writes(tmp_path: Path) -> None:
    first, second = tmp_path / "First.csv", tmp_path / "Second.csv"
    telemetry(first)
    telemetry(second)
    logs = service(tmp_path)
    logs.generate(logs.preview({first: "Falcon", second: "Trainer"}))
    originals = {name: logs.read_log(name) for name in logs.list_aircraft()}
    fingerprints = {name: document.fingerprint for name, document in originals.items()}
    with pytest.raises(ValueError, match="unknown flight numbers"):
        logs.save_notes_many({"Falcon": {1: "New Falcon note"}, "Trainer": {99: "Invalid"}}, fingerprints)
    for document in originals.values():
        assert document.path.read_bytes() == document.text.encode("utf-8")
    logs.save_notes("Trainer", 1, "Changed after review")
    with pytest.raises(ValueError, match="changed since review"):
        logs.save_notes_many({"Falcon": {1: "New Falcon note"}, "Trainer": {1: "Stale"}}, fingerprints)
    assert logs.read_log("Falcon").rows[0][-1] == ""
    fingerprints["Trainer"] = logs.read_log("Trainer").fingerprint
    result = logs.save_notes_many({"Falcon": {1: "New Falcon note"}, "Trainer": {1: "Reviewed new note"}}, fingerprints)
    assert result["rows_updated"] == 2
    assert result["errors"] == []
    assert logs.read_log("Falcon").rows[0][-1] == "New Falcon note"
    assert logs.read_log("Trainer").rows[0][-1] == "Reviewed new note"


def test_existing_duplicate_flight_numbers_are_not_editable(tmp_path: Path) -> None:
    source = tmp_path / "Multi.csv"
    telemetry(source, windows=((30, 120), (270, 390)))
    logs = service(tmp_path)
    logs.generate(logs.preview({source: "Falcon"}))
    path = logs.read_log("Falcon").path
    text = path.read_text(encoding="utf-8").replace("| 2 |", "| 1 |")
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="ascending flight dates and numbers"):
        logs.save_notes("Falcon", 1, "Cannot choose between duplicate rows")
    assert path.read_bytes() == before


def test_find_sources_on_restart_handles_offset_multiflight_ambiguity_and_replaced_file(tmp_path: Path) -> None:
    path = tmp_path / "Multi.csv"
    telemetry(path, windows=((30, 120), (270, 390)))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    for row in rows[1:]:
        row[1] += "-04:00"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    logs = service(tmp_path)
    logs.generate(logs.preview({path: "Falcon"}))
    document = logs.save_notes("Falcon", 2, "Keep this pilot note")
    duplicate = tmp_path / "Copy.csv"
    shutil.copy2(path, duplicate)
    # Another installation can use the maintained Markdown without any local source index.
    reopened = service(tmp_path)
    key = (document.rows[1][1], document.rows[1][2])
    matches = reopened.find_flight_sources({path: "Falcon", duplicate: "Falcon"}, "Falcon", *key)
    assert {hit.path for hit in matches} == {path, duplicate}
    assert all(hit.start == datetime(2026, 7, 1, 13, 4, 30) for hit in matches)
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b"2026-07-01", b"2026-07-04"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    duplicate.unlink()
    assert not reopened.find_flight_sources({path: "Falcon", duplicate: "Falcon"}, "Falcon", *key)
    assert document.path.read_text(encoding="utf-8") == document.text


def test_cold_lookup_runs_detector_only_for_sources_containing_the_actual_timestamp(tmp_path: Path, monkeypatch) -> None:
    matching, unrelated = tmp_path / "mislabeled-2025-01-01.csv", tmp_path / "2026-07-01.csv"
    telemetry(matching)
    telemetry(unrelated, day=2)
    with matching.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    # Blank, malformed and out-of-order records cannot hide a matching sample.
    rows.insert(12, list(rows[-1]))
    rows.insert(30, ["unknown", "missing"])
    rows.insert(40, [])
    for row in rows[1:]:
        if row and row[0].startswith("2026"):
            row[1] += ".375-04:00"
    with matching.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    logs = service(tmp_path)
    sources = {matching: "Falcon", unrelated: "Falcon"}
    calls = []
    original = engine.analyze_file
    def record(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(engine, "analyze_file", record)
    hits = logs.find_flight_sources(sources, "Falcon", "2026-07-01 09:01:00", "00:02:00")
    assert len(hits) == 1 and hits[0].path == matching
    assert hits[0].start == datetime(2026, 7, 1, 13, 1, 0, 375000)
    assert calls == [matching]
    assert not logs.find_flight_sources(sources, "Falcon", "2024-12-05 10:00:00", "00:02:00")
    assert calls == [matching]


def test_archive_preserves_uncertain_failed_incomplete_overlapping_stale_and_colliding_files(tmp_path: Path) -> None:
    root = tmp_path / "library"
    sources = root / "Falcon"
    sources.mkdir(parents=True)
    paths = {name: sources / f"{name}.csv" for name in
             ("flight", "pending", "overlap", "incomplete", "unsupported", "good", "changed", "collision")}
    telemetry(paths["flight"])
    telemetry(paths["pending"], direct=False, windows=((30, 270),))
    for name in ("overlap", "good", "changed", "collision", "incomplete"):
        telemetry(paths[name], ground=True, day=1 if name == "overlap" else 2)
    paths["unsupported"].write_text("Date,Time\n2026-07-03,11:00:00\n", encoding="utf-8")
    with paths["incomplete"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    with paths["incomplete"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows[:2])
        writer.writerows([*row[:2], *([""] * (len(row) - 2))] for row in rows[2:])
    logs = service(tmp_path)
    logs.generate(logs.preview({paths["flight"]: "Falcon"}))
    preview = logs.preview(dict.fromkeys(paths.values(), "Falcon"))
    assert set(preview.archivable_sources(root)) == {paths[name] for name in ("good", "changed", "collision")}
    archived = root / NONFLIGHT_ARCHIVE / "Falcon"
    archived.mkdir(parents=True)
    (archived / "collision.csv").write_text("Do not overwrite", encoding="utf-8")
    paths["changed"].write_text("Changed after review", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        logs.archive_nonflights(preview, root, [paths["flight"]])
    radio = tmp_path / "radio"
    (radio / "Falcon").mkdir(parents=True)
    shutil.copy2(paths["good"], radio / "Falcon" / "good.csv")
    moved, errors = logs.archive_nonflights(preview, root, preview.archivable_sources(root))
    assert moved == {paths["good"]: archived / "good.csv"}
    assert len(errors) == 2
    assert (archived / "collision.csv").read_text() == "Do not overwrite"
    assert not (archived / "changed.csv").exists()
    assert {info.path for info in scan_library(root)} == set(paths.values()) - {paths["good"]}
    assert (archived / "good.csv").read_bytes() == (radio / "Falcon" / "good.csv").read_bytes()
    assert not discover_sync_candidates(radio, root)
    (radio / "Falcon" / "good.csv").write_bytes(b"A replacement recording")
    assert len(discover_sync_candidates(radio, root)) == 1
