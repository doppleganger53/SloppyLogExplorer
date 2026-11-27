import csv
from datetime import date
from io import BytesIO
import os
from pathlib import Path
import struct
from zipfile import ZipFile, ZipInfo

import pytest

from sloppy_log_explorer.history_sources import build_site_history
from sloppy_log_explorer.library import scan_library
from sloppy_log_explorer.reception import build_reception_heatmap, refresh_reception_index
from sloppy_log_explorer.spektrum_history import write_reception_csv
from sloppy_log_explorer.storage import AppStore


def _flight(day, lat, value):
    return (
        "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%),RSSI 900M(dB)\n"
        f"{day},12:00:00,{lat:.6f},-75.0,{value},51\n"
        f"{day},12:00:01,{lat + 0.000001:.6f},-75.0,{value},50\n"
    )


def test_index_and_history_include_two_sites_archives_dates_and_refresh(tmp_path: Path):
    first = tmp_path / "Glider-2026-01-01.csv"
    first.write_bytes(_flight("2026-01-01", 40, 40).encode())
    archive_path = tmp_path / "old.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.write(first, "backup/renamed.log")
        archive.writestr("Glider/flight.log", _flight("2026-04-01", 40, 100))
        archive.writestr("Glider/FLIGHT.LOG", _flight("2026-04-01", 42, 60))
        archive.writestr("other.txt", "unrelated")
    (tmp_path / "broken.zip").write_bytes(b"broken")
    result = refresh_reception_index(scan_library(tmp_path), [], tmp_path)
    assert len(result.clusters) == 2
    assert result.error_count == 1
    assert sorted(r.status for r in result.records) == ["duplicate", "io_error", "ok", "ok", "ok"]
    store = AppStore(tmp_path / "library.db")
    try:
        store.upsert_reception_records(tmp_path, result.records)
        store.apply_reception_clusters(tmp_path, result.clusters)
        saved = store.list_reception_records(tmp_path)
        cached = refresh_reception_index(scan_library(tmp_path), saved, tmp_path)
        assert cached.cached_count == len(saved) and cached.scanned_count == 0
        sites = store.list_flying_sites(tmp_path)
        first_site = min(sites, key=lambda s: s["center_latitude"])
        stored_rows = store.list_flying_site_records(first_site["id"])
        assert [r["model"] for r in build_site_history(stored_rows, (40, -75))["logs"]] == ["Glider", "Glider"]
    finally:
        store.close()
    site = result.clusters[0]
    assert len(site.records) == 2
    history = build_site_history(site.records, (40, -75))
    assert [(r["model"], r["date"]) for r in history["logs"]] == [
        ("Glider", "2026-01-01"), ("Glider", "2026-04-01"),
    ]
    for row, expected in zip(history["logs"], (40, 100)):
        assert row["grids"]["5"]["VFR 2.4G(%)"] == [[0, 0, expected, 2]]
        assert row["grids"]["2"]["RSSI 900M(dB)"] == [[0, 0, 50.5, 2]]
    static = build_reception_heatmap(site.records, "VFR 2.4G(%)", (40, -75))
    assert static["logs_used"] == 2
    assert static["cells"][0]["value"] == 70

    (tmp_path / "Cub-2026-10-30.log").write_text(_flight("2026-10-30", 40, 80))
    refreshed = refresh_reception_index(scan_library(tmp_path), result.records, tmp_path)
    assert refreshed.scanned_count == 1
    assert [r["date"] for r in build_site_history(refreshed.clusters[0].records, (40, -75))["logs"]] == [
        "2026-01-01", "2026-04-01", "2026-10-30",
    ]
    # Removing the preferred loose copy promotes the archived recording.
    first.unlink()
    restored = refresh_reception_index(scan_library(tmp_path), refreshed.records, tmp_path)
    assert len(restored.clusters[0].records) == 3
    assert all(record.status != "duplicate" for record in restored.records)


def test_replaced_zip_member_requires_refresh_in_both_maps(tmp_path: Path):
    archive_path = tmp_path / "backups.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("Plane/March.log", _flight("2026-03-01", 40, 30))
    original = refresh_reception_index([], [], tmp_path).records
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("Plane/April.log", _flight("2026-04-01", 40, 80))
    new_time = original[0].mtime_ns + 2_000_000_000
    os.utime(archive_path, ns=(new_time, new_time))

    static = build_reception_heatmap(original, "VFR 2.4G(%)", (40, -75))
    assert not static["cells"] and static["error_count"] == 1
    assert build_site_history(original, (40, -75))["counts"] == {"changed_since_index": 1}
    refreshed = refresh_reception_index([], original, tmp_path).records
    assert refreshed[0].flight_date == date(2026, 4, 1)
    assert build_reception_heatmap(refreshed, "VFR 2.4G(%)", (40, -75))["cells"][0]["value"] == 80


def test_explicit_refresh_recovers_transient_read_failure(tmp_path: Path, monkeypatch):
    from sloppy_log_explorer import reception

    path = tmp_path / "Model.csv"
    path.write_text(_flight("2026-04-15", 40, 80))
    logs = scan_library(tmp_path)
    with monkeypatch.context() as patch:
        def unavailable(*_args, **_kwargs):
            raise PermissionError("Temporarily unavailable")
        patch.setattr(reception, "_sampled_csv", unavailable)
        initial = refresh_reception_index(logs, [], tmp_path)
    assert initial.records[0].status == "io_error"
    assert refresh_reception_index(logs, initial.records, tmp_path).cached_count == 1
    recovered = refresh_reception_index(logs, initial.records, tmp_path, retry_io_errors=True)
    assert recovered.scanned_count == 1 and recovered.records[0].status == "ok"


def _signal(a=-67, b=85):
    payload = bytearray(16)
    payload[0] = 0x7E
    struct.pack_into("bb", payload, 8, a, b)
    return payload


def _qos(count, remote=0xFFFE):
    return struct.pack(">BB7H", 0x7F, 0, count, remote, 0xFFFF, 0xFFFE, count, count, 500)


def _frames(pairs):
    return b"".join(struct.pack("<I", tick) + payload for tick, payload in pairs)


_PACKED_FIX = bytes.fromhex("16 00 0000 96454639 28291275 0000 00 19")


@pytest.mark.parametrize("fix", [
    _PACKED_FIX,
    struct.pack(">BBHiiHBB", 0x26, 0, 1000, 397743267, -752048800, 0, 0, 8),
])
def test_spektrum_avoids_stale_gps_unavailable_counters_and_resets(tmp_path: Path, fix):
    source = _frames([
        (100, fix), (101, _signal()), (102, _qos(3)), (152, _qos(5)),
        (153, _qos(0)), (240, _signal()), (241, _qos(7)),
        (250, fix), (251, _qos(12)), (252, _signal()),
        (255, _qos(3)), (256, _signal(-1, 0)),
    ])
    path = tmp_path / "scratch.csv"
    assert write_reception_csv(BytesIO(source), path, date(2026, 1, 1), lambda: None) == ("ok", "")
    with path.open() as stream:
        rows = [row for row in csv.DictReader(stream) if any(
            value for name, value in row.items() if name.startswith("Spektrum")
        )]
    assert [(float(r["GPS Lat"]), float(r["GPS Lon"])) for r in rows] == [
        (pytest.approx(39.77432667), pytest.approx(-75.20488))
    ] * 3
    assert [r["Spektrum RSSI (%)"] for r in rows if r["Spektrum RSSI (%)"]] == ["85", "85"]
    assert [r["Spektrum frame losses (/s)"] for r in rows if r["Spektrum frame losses (/s)"]] == ["4.0"]
    assert all(not r["Spektrum B fades (/s)"] for r in rows)


def test_zipped_tlm_and_undated_log_use_member_metadata(tmp_path: Path):
    header = bytearray(32)
    header[0] = 0x12
    header[6:12] = b"Cirrus"
    frame_bytes = b"\xff" * 4 + header + _frames([(0, _PACKED_FIX), (2, _signal())])
    archive_path = tmp_path / "Spektrum.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("Logs/Cirrus_20250119.tlm", frame_bytes)
        archive.writestr(ZipInfo("undated.log", (2024, 5, 12, 12, 0, 0)),
                         "Time,GPS Lat,GPS Lon,VFR(%)\n12:00:00,39.7743,-75.20488,90\n"
                         "12:00:01,39.77431,-75.20488,88\n")
    result = refresh_reception_index(scan_library(tmp_path), [], tmp_path)
    assert len(result.clusters) == 1
    logs = build_site_history(result.records, (39.7743, -75.20488))["logs"]
    assert [(r["date"], r["dateSource"], r["inferred"]) for r in logs] == [
        ("2024-05-12", "mtime", True), ("2025-01-19", "filename", True),
    ]
    assert logs[1]["model"] == "Cirrus"
    assert "Spektrum RSSI (dBm)" in logs[1]["grids"]["5"]
