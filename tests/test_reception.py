from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

import sloppy_log_explorer.reception as reception
from sloppy_log_explorer.library import scan_library
from sloppy_log_explorer.models import LibraryLogInfo, ReceptionLogRecord
from sloppy_log_explorer.reception import (
    build_reception_heatmap,
    cluster_reception_records,
    filter_site_records,
    index_log,
    refresh_reception_index,
    telemetry_channel_coverage,
)


def _write_gps_log(
    path: Path,
    values: list[float],
    *,
    latitude: float = 39.75,
    longitude: float = -75.25,
    channel: str = "VFR 2.4G(%)",
    dated: bool = True,
    latitude_step: float = 0.0,
) -> None:
    header = "Date,Time" if dated else "Time"
    rows = [f"{header},GPS Lat,GPS Lon,{channel}"]
    for index, value in enumerate(values):
        timestamp = f"2026-04-01,12:00:{index % 60:02d}" if dated else str(index)
        rows.append(
            f"{timestamp},{latitude + latitude_step * index:.8f},{longitude:.8f},{value}"
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def _record(path: Path, latitude: float, longitude: float, *, site_id: int | None = None) -> ReceptionLogRecord:
    stat = path.stat() if path.exists() else None
    return ReceptionLogRecord(
        library_root=path.parent,
        file_path=path,
        file_size=stat.st_size if stat else 0,
        mtime_ns=stat.st_mtime_ns if stat else 0,
        status="ok",
        flight_date=date(2026, 4, 1),
        date_inferred=False,
        center_latitude=latitude,
        center_longitude=longitude,
        channels=("VFR 2.4G(%)",),
        site_id=site_id,
    )


def test_index_log_samples_every_twentieth_row_and_uses_recorded_date(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    values = list(range(41))
    _write_gps_log(path, values, latitude=39.0, latitude_step=0.0001)
    # This unsampled position would significantly move a full-row centroid.
    lines = path.read_text(encoding="utf-8").splitlines()
    fields = lines[2].split(",")
    fields[2] = "45.00000000"
    lines[2] = ",".join(fields)
    path.write_text("\n".join(lines), encoding="utf-8")

    record = index_log(path, tmp_path, sample_stride=20)

    assert record.status == "ok"
    assert record.center_latitude == pytest.approx(39.002, abs=1e-5)
    assert record.center_longitude == pytest.approx(-75.25, abs=1e-6)
    assert record.flight_date == date(2026, 4, 1)
    assert record.date_inferred is False
    assert record.channels == ("VFR 2.4G(%)",)


def test_index_log_uses_file_modified_date_for_elapsed_only_log(tmp_path: Path) -> None:
    path = tmp_path / "undated.csv"
    _write_gps_log(path, [80.0, 81.0], dated=False)
    modified = datetime(2025, 12, 31, 14, 30).timestamp()
    os.utime(path, (modified, modified))

    record = index_log(path, tmp_path)

    assert record.status == "ok"
    assert record.flight_date == date(2025, 12, 31)
    assert record.date_inferred is True


def test_index_log_scans_position_hinted_logs_past_the_probe_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "late-gps.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,0,0,90",
                "2026-04-01,12:00:01,0,0,89",
                "2026-04-01,12:00:02,0,0,88",
                "2026-04-01,12:00:03,39.0000,-75.0000,87",
                "2026-04-01,12:00:04,39.0005,-75.0005,86",
            ]
        ),
        encoding="utf-8",
    )

    sampled_csv = reception._sampled_csv
    limits: list[int | None] = []

    def recording_sample(
        sampled_path: Path,
        sample_stride: int,
        *,
        max_records: int | None = None,
        full_scan_if_position_hint: bool = False,
    ):
        limits.append(max_records)
        return sampled_csv(
            sampled_path,
            sample_stride,
            max_records=max_records,
            full_scan_if_position_hint=full_scan_if_position_hint,
        )

    monkeypatch.setattr(reception, "_sampled_csv", recording_sample)
    bounded = index_log(path, tmp_path, sample_stride=1, gps_probe_records=3)
    bounded_limits = limits.copy()
    limits.clear()
    complete = index_log(path, tmp_path, sample_stride=1, gps_probe_records=5)

    assert bounded.status == "ok"
    assert bounded_limits == [3]
    assert complete.status == "ok"
    assert limits == [5]


def test_index_log_keeps_unhinted_non_gps_probe_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "plain.csv"
    path.write_text(
        "Date,Time,VFAS(V)\n"
        "2026-04-01,12:00:00,16.8\n"
        "2026-04-01,12:00:01,16.7\n"
        "2026-04-01,12:00:02,16.6\n"
        "2026-04-01,12:00:03,16.5\n",
        encoding="utf-8",
    )
    sampled_csv = reception._sampled_csv
    calls: list[tuple[int, int | None, bool]] = []

    def recording_sample(
        sampled_path: Path,
        sample_stride: int,
        *,
        max_records: int | None = None,
        full_scan_if_position_hint: bool = False,
    ):
        calls.append((sample_stride, max_records, full_scan_if_position_hint))
        return sampled_csv(
            sampled_path,
            sample_stride,
            max_records=max_records,
            full_scan_if_position_hint=full_scan_if_position_hint,
        )

    monkeypatch.setattr(reception, "_sampled_csv", recording_sample)

    record = index_log(path, tmp_path, sample_stride=20, gps_probe_records=3)

    assert record.status == "no_gps"
    assert calls == [(20, 3, True)]


def test_index_log_supports_coordinate_text_and_split_gps_formats(tmp_path: Path) -> None:
    coordinate_path = tmp_path / "coordinate.csv"
    coordinate_path.write_text(
        "\n".join(
            [
                "Date,Time,Position,VFR 2.4G(%)",
                '2026-04-01,12:00:00,"39.0000N 75.0000W",90',
                '2026-04-01,12:00:01,"39.0005 N, 75.0005 W",80',
                '2026-04-01,12:00:02,"N39.0010 W75.0010",70',
            ]
        ),
        encoding="utf-8",
    )
    split_path = tmp_path / "split.csv"
    split_path.write_text(
        "\n".join(
            [
                "Date,Time,Northing,Easting,VFR 2.4G(%)",
                "2026-04-01,12:00:00,39.0000,-75.0000,90",
                "2026-04-01,12:00:01,39.0005,-75.0005,80",
                "2026-04-01,12:00:02,39.0010,-75.0010,70",
            ]
        ),
        encoding="utf-8",
    )

    coordinate = index_log(coordinate_path, tmp_path, sample_stride=1)
    split = index_log(split_path, tmp_path, sample_stride=1)

    assert coordinate.status == "ok"
    assert coordinate.center_latitude == pytest.approx(39.00025, abs=1e-5)
    assert coordinate.center_longitude == pytest.approx(-75.00025, abs=1e-5)
    assert coordinate.channels == ("VFR 2.4G(%)",)
    assert split.status == "ok"
    assert split.center_latitude == pytest.approx(39.0005, abs=1e-5)
    assert split.center_longitude == pytest.approx(-75.0005, abs=1e-5)


def test_index_and_heatmap_share_origin_and_isolated_outlier_cleanup(tmp_path: Path) -> None:
    origin_path = tmp_path / "origin.csv"
    origin_path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,0,0,5",
                "2026-04-01,12:00:01,39.0000,-75.0000,90",
                "2026-04-01,12:00:02,39.0005,-75.0005,80",
                "2026-04-01,12:00:03,0,0,5",
            ]
        ),
        encoding="utf-8",
    )
    outlier_path = tmp_path / "outlier.csv"
    outlier_path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,5.0000,-10.0000,1",
                "2026-04-01,12:00:01,39.0000,-75.0000,90",
                "2026-04-01,12:00:02,39.0005,-75.0005,80",
                "2026-04-01,12:00:03,4.0000,-12.0000,2",
                "2026-04-01,12:00:04,39.0010,-75.0010,70",
                "2026-04-01,12:00:05,39.0015,-75.0015,60",
                "2026-04-01,12:00:06,6.0000,-11.0000,3",
            ]
        ),
        encoding="utf-8",
    )

    origin = index_log(origin_path, tmp_path, sample_stride=1)
    outlier = index_log(outlier_path, tmp_path, sample_stride=1)
    origin_payload = build_reception_heatmap(
        [origin], "VFR 2.4G(%)", (39.00025, -75.00025), cell_size_m=5.0
    )
    outlier_payload = build_reception_heatmap(
        [outlier], "VFR 2.4G(%)", (39.00075, -75.00075), cell_size_m=5.0
    )

    assert origin.status == "ok"
    assert origin.center_latitude == pytest.approx(39.00025, abs=1e-5)
    assert sum(cell["sample_count"] for cell in origin_payload["cells"]) == 2
    assert outlier.status == "ok"
    assert outlier.center_latitude == pytest.approx(39.00075, abs=1e-5)
    assert outlier.center_longitude == pytest.approx(-75.00075, abs=1e-5)
    assert sum(cell["sample_count"] for cell in outlier_payload["cells"]) == 4


def test_index_uses_dominant_local_gps_cloud_and_ignores_partial_lock_rows(tmp_path: Path) -> None:
    path = tmp_path / "startup-gps-errors.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,46.663220,-66.232376,10",
                "2026-04-01,12:00:01,46.662421,-66.212856,20",
                "2026-04-01,12:00:02,0.0,-75.204900,30",
                "2026-04-01,12:00:03,0.0,-75.204900,40",
                "2026-04-01,12:00:04,39.774300,-75.204900,90",
                "2026-04-01,12:00:05,39.774400,-75.204800,80",
                "2026-04-01,12:00:06,39.774500,-75.204700,70",
            ]
        ),
        encoding="utf-8",
    )

    record = index_log(path, tmp_path, sample_stride=1)

    assert record.status == "ok"
    assert record.center_latitude == pytest.approx(39.7744, abs=1e-5)
    assert record.center_longitude == pytest.approx(-75.2048, abs=1e-5)


def test_index_rejects_gps_samples_without_a_dominant_local_cloud(tmp_path: Path) -> None:
    path = tmp_path / "incoherent-gps.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,10.0,-20.0,90",
                "2026-04-01,12:00:01,30.0,-50.0,80",
                "2026-04-01,12:00:02,50.0,-80.0,70",
            ]
        ),
        encoding="utf-8",
    )

    record = index_log(path, tmp_path, sample_stride=1)

    assert record.status == "no_gps"


def test_refresh_reindexes_records_from_an_older_index_algorithm(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    _write_gps_log(path, [90.0, 80.0])
    logs = scan_library(tmp_path)
    current = index_log(path, tmp_path, sample_stride=1)
    stale = replace(current, index_version=0)

    result = refresh_reception_index(logs, [stale], tmp_path, sample_stride=1)

    assert result.scanned_count == 1
    assert result.cached_count == 0
    assert result.records[0].index_version == reception.RECEPTION_INDEX_VERSION


def test_refresh_reception_index_reuses_unchanged_fingerprints_and_cancels(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    _write_gps_log(path, [90.0, 91.0])
    logs = scan_library(tmp_path)

    first = refresh_reception_index(logs, [], tmp_path)
    second = refresh_reception_index(logs, first.records, tmp_path)
    cancelled = refresh_reception_index(logs, first.records, tmp_path, is_cancelled=lambda: True)

    assert first.scanned_count == 1 and first.cached_count == 0
    assert second.scanned_count == 0 and second.cached_count == 1
    assert second.records == first.records
    assert cancelled.cancelled is True
    assert cancelled.records == ()


def test_refresh_caches_failures_detects_changes_and_rejects_foreign_roots(tmp_path: Path) -> None:
    no_gps_path = tmp_path / "no-gps.csv"
    no_gps_path.write_text(
        "Date,Time,VFAS(V)\n2026-04-01,12:00:00,16.8\n2026-04-01,12:00:01,16.7\n",
        encoding="utf-8",
    )
    malformed_path = tmp_path / "malformed.csv"
    malformed_path.write_text("not,a,telemetry,table\n", encoding="utf-8")
    logs = scan_library(tmp_path)

    first = refresh_reception_index(logs, [], tmp_path)
    second = refresh_reception_index(logs, first.records, tmp_path)

    assert {record.status for record in first.records} == {"no_gps", "malformed"}
    assert first.scanned_count == 2
    assert second.cached_count == 2

    no_gps_path.write_text(
        no_gps_path.read_text(encoding="utf-8") + "2026-04-01,12:00:02,16.6\n",
        encoding="utf-8",
    )
    changed_logs = scan_library(tmp_path)
    changed = refresh_reception_index(changed_logs, second.records, tmp_path)
    assert changed.scanned_count == 1
    assert changed.cached_count == 1

    matching = next(record for record in changed.records if record.file_path == no_gps_path)
    foreign = replace(matching, library_root=tmp_path / "different-library")
    foreign_result = refresh_reception_index(
        [next(log for log in changed_logs if log.path == no_gps_path)],
        [foreign],
        tmp_path,
    )
    assert foreign_result.scanned_count == 1
    assert foreign_result.cached_count == 0

    offline_log = LibraryLogInfo(tmp_path / "offline.csv", "offline", "offline.csv", 123.456, 321)
    offline_first = refresh_reception_index([offline_log], [], tmp_path)
    offline_second = refresh_reception_index([offline_log], offline_first.records, tmp_path)
    assert offline_first.records[0].status == "io_error"
    assert offline_first.records[0].file_size == 321
    assert offline_first.records[0].mtime_ns == int(123.456 * 1_000_000_000)
    assert offline_second.cached_count == 1


def test_center_constrained_clustering_prevents_neighbor_chain_and_is_deterministic(tmp_path: Path) -> None:
    records = [
        _record(tmp_path / f"{index}.csv", 0.0, index * 0.013)
        for index in range(4)
    ]

    clusters = cluster_reception_records(records, tolerance_km=2.0)
    reversed_clusters = cluster_reception_records(reversed(records), tolerance_km=2.0)

    assert len(clusters) >= 2
    assert [[Path(path).name for path in cluster.file_paths] for cluster in clusters] == [
        [Path(path).name for path in cluster.file_paths] for cluster in reversed_clusters
    ]
    for cluster in clusters:
        for record in cluster.records:
            # At the equator one longitude degree is approximately 111.2 km.
            assert abs(record.center_longitude - cluster.center_longitude) * 111.2 <= 2.01


def test_filtering_and_channel_coverage_use_inclusive_dates_and_exact_headers(tmp_path: Path) -> None:
    base = _record(tmp_path / "one.csv", 39.0, -75.0, site_id=7)
    second = ReceptionLogRecord(
        **{**base.__dict__, "file_path": tmp_path / "two.csv", "flight_date": date(2026, 4, 3), "channels": ("Rx VFR(%)",)}
    )
    records = [base, second]

    filtered = filter_site_records(records, site_id=7, date_start=date(2026, 4, 1), date_end=date(2026, 4, 3))
    coverage = telemetry_channel_coverage(filtered)

    assert filtered == records
    assert [(item.channel, item.count, item.total) for item in coverage] == [
        ("Rx VFR(%)", 1, 2),
        ("VFR 2.4G(%)", 1, 2),
    ]


def test_heatmap_uses_all_rows_five_meter_cells_and_equal_flight_medians(tmp_path: Path) -> None:
    long_path = tmp_path / "long.csv"
    short_path = tmp_path / "short.csv"
    missing_path = tmp_path / "missing.csv"
    _write_gps_log(long_path, [100.0] * 101)
    _write_gps_log(short_path, [0.0, 0.0])
    _write_gps_log(missing_path, [12.0, 12.0], channel="Current(A)")
    records = [index_log(path, tmp_path) for path in (long_path, short_path, missing_path)]

    payload = build_reception_heatmap(records, "VFR 2.4G(%)", (39.75, -75.25), cell_size_m=5.0)

    assert payload["status"] == "ok"
    assert payload["cell_size_m"] == 5.0
    assert payload["logs_considered"] == 3
    assert payload["logs_used"] == 2
    assert payload["missing_channel_count"] == 1
    cells = payload["cells"]
    assert isinstance(cells, list) and len(cells) == 1
    assert cells[0]["value"] == pytest.approx(50.0)
    assert cells[0]["flight_count"] == 2
    assert cells[0]["sample_count"] == 103
    assert len(cells[0]["polygon"]) == 5
    assert cells[0]["polygon"][0] == cells[0]["polygon"][-1]
    json.dumps(payload, allow_nan=False)


def test_heatmap_splits_observed_samples_across_five_meter_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "boundary.csv"
    # Roughly six metres north between samples at this latitude.
    _write_gps_log(path, [10.0, 20.0], latitude_step=6.0 / 111_320.0)
    record = index_log(path, tmp_path, sample_stride=1)

    payload = build_reception_heatmap([record], "VFR 2.4G(%)", (39.75, -75.25), cell_size_m=5.0)

    assert payload["status"] == "ok"
    assert len(payload["cells"]) == 2
    assert sorted(cell["value"] for cell in payload["cells"]) == [10.0, 20.0]


def test_heatmap_removes_repeated_gps_dropouts_far_from_selected_site(tmp_path: Path) -> None:
    path = tmp_path / "gps-dropout.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%)",
                "2026-04-01,12:00:00,39.77430,-75.20490,90",
                "2026-04-01,12:00:01,39.77440,-75.20480,80",
                "2026-04-01,12:00:02,0.0,-75.204908,100",
                "2026-04-01,12:00:03,0.0,-75.204908,100",
            ]
        ),
        encoding="utf-8",
    )
    record = _record(path, 39.77435, -75.20485)

    payload = build_reception_heatmap(
        [record],
        "VFR 2.4G(%)",
        (39.77435, -75.20485),
        cell_size_m=5.0,
    )

    cells = payload["cells"]
    assert isinstance(cells, list)
    assert sum(cell["sample_count"] for cell in cells) == 2
    assert payload["off_site_sample_count"] == 2
    assert all(float(cell["latitude"]) > 39.0 for cell in cells)
