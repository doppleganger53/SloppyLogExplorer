from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sloppy_log_explorer.models import DetectedSiteCluster, ReceptionLogRecord
from sloppy_log_explorer.storage import AppStore


def reception_record(
    path: Path,
    *,
    flight_date: str = "2026-06-01",
    inferred: bool = False,
    latitude: float = 39.7744,
    longitude: float = -75.2049,
) -> dict[str, object]:
    return {
        "file_path": path,
        "file_size": 100,
        "mtime_ns": 1_000,
        "status": "gps",
        "flight_date": flight_date,
        "date_inferred": inferred,
        "center_latitude": latitude,
        "center_longitude": longitude,
        "channels": ["RSSI(dB)", "VFR 2.4G(%)"],
        "error": "",
    }


def cluster(paths: list[Path], latitude: float, longitude: float) -> dict[str, object]:
    return {
        "file_paths": paths,
        "center_latitude": latitude,
        "center_longitude": longitude,
    }


def test_reception_schema_migrates_idempotently_from_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("create table settings (key text primary key, value text not null)")
    connection.execute("insert into settings values ('existing', '\"preserved\"')")
    connection.commit()
    connection.close()

    first = AppStore(database)
    first.close()
    second = AppStore(database)

    tables = {
        row["name"]
        for row in second.conn.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    reception_columns = {
        row["name"]
        for row in second.conn.execute("pragma table_info(reception_logs)").fetchall()
    }

    assert second.get_setting("existing") == "preserved"
    assert {"flying_sites", "reception_logs"} <= tables
    assert {
        "library_root",
        "file_path",
        "file_size",
        "mtime_ns",
        "status",
        "flight_date",
        "date_inferred",
        "center_latitude",
        "center_longitude",
        "channels_json",
        "error",
        "site_id",
    } <= reception_columns
    second.close()


@dataclass(frozen=True)
class AliasReceptionRecord:
    path: Path
    size: int
    modified_ns: int
    status: str
    flight_date: str | None
    date_is_inferred: bool
    centroid_latitude: float | None
    centroid_longitude: float | None
    numeric_channels: tuple[str, ...]
    error_message: str = ""


def test_reception_cache_upsert_remove_and_library_isolation(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    first_root = tmp_path / "library-one"
    second_root = tmp_path / "library-two"
    first_a = first_root / "a.csv"
    first_b = first_root / "b.csv"
    second_a = second_root / "a.csv"

    store.upsert_reception_records(
        first_root,
        [
            reception_record(first_a),
            AliasReceptionRecord(
                path=first_b,
                size=25,
                modified_ns=250,
                status="no_gps",
                flight_date=None,
                date_is_inferred=True,
                centroid_latitude=None,
                centroid_longitude=None,
                numeric_channels=("RxBt(V)",),
            ),
        ],
    )
    store.upsert_reception_records(second_root, [reception_record(second_a)])

    store.upsert_reception_records(
        first_root,
        [
            {
                **reception_record(first_a, flight_date="2026-06-02", inferred=True),
                "file_size": 200,
                "mtime_ns": 2_000,
                "channels": ["RSSI(dB)", "RSSI(dB)", "VFR 2.4G(%)"],
            }
        ],
    )

    first_records = store.list_reception_records(first_root / ".")
    assert len(first_records) == 2
    assert first_records[0]["file_size"] == 200
    assert first_records[0]["mtime_ns"] == 2_000
    assert first_records[0]["flight_date"] == "2026-06-02"
    assert first_records[0]["date_inferred"] is True
    assert first_records[0]["channels"] == ["RSSI(dB)", "VFR 2.4G(%)"]
    assert first_records[1]["status"] == "no_gps"
    assert first_records[1]["channels"] == ["RxBt(V)"]

    assert store.remove_missing_reception_records(first_root, [first_a]) == 1
    assert [Path(record["file_path"]) for record in store.list_reception_records(first_root)] == [
        first_a.resolve()
    ]
    assert len(store.list_reception_records(second_root)) == 1

    assert store.remove_missing_reception_records(first_root, []) == 1
    assert store.list_reception_records(first_root) == []
    assert len(store.list_reception_records(second_root)) == 1
    store.close()


def test_storage_accepts_reception_domain_dataclasses(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    root = tmp_path / "library"
    path = root / "flight.csv"
    record = ReceptionLogRecord(
        library_root=str(root),
        file_path=str(path),
        file_size=123,
        mtime_ns=456,
        status="ok",
        flight_date=date(2026, 7, 4),
        date_inferred=False,
        center_latitude=39.7744,
        center_longitude=-75.2049,
        channels=("VFR 2.4G(%)",),
    )
    detected = DetectedSiteCluster(
        center_latitude=39.7744,
        center_longitude=-75.2049,
        file_paths=(str(path),),
        records=(record,),
    )

    store.upsert_reception_records(root, [record])
    site = store.apply_reception_clusters(root, [detected])[0]
    cached = store.list_flying_site_records(site["id"])[0]

    assert cached["status"] == "ok"
    assert cached["flight_date"] == "2026-07-04"
    assert cached["channels"] == ["VFR 2.4G(%)"]
    assert cached["site_id"] == site["id"]
    store.close()


def test_site_metadata_and_aggregates_survive_empty_rescan_and_center_match(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    root = tmp_path / "library"
    first = root / "first.csv"
    second = root / "second.csv"
    replacement = root / "replacement.csv"
    store.upsert_reception_records(
        root,
        [
            reception_record(first, flight_date="2026-05-01"),
            reception_record(second, flight_date="2026-06-20", inferred=True),
        ],
    )

    sites = store.apply_reception_clusters(
        root,
        [cluster([first, second], 39.7744, -75.2049)],
    )
    site_id = sites[0]["id"]
    store.update_flying_site(site_id, "WJRC Field", "North parking area")

    site = store.list_flying_sites(root)[0]
    assert site["log_count"] == 2
    assert site["earliest_flight_date"] == "2026-05-01"
    assert site["latest_flight_date"] == "2026-06-20"
    assert site["inferred_date_count"] == 1

    assert store.apply_reception_clusters(root, []) == []
    inactive = store.list_flying_sites(root, active_only=False)[0]
    assert inactive["id"] == site_id
    assert inactive["active"] is False
    assert inactive["name"] == "WJRC Field"
    assert inactive["notes"] == "North parking area"

    store.remove_missing_reception_records(root, [])
    store.upsert_reception_records(
        root,
        [reception_record(replacement, flight_date="2026-07-04")],
    )
    reactivated = store.apply_reception_clusters(
        root,
        [cluster([replacement], 39.7790, -75.2049)],
    )[0]

    assert reactivated["id"] == site_id
    assert reactivated["name"] == "WJRC Field"
    assert reactivated["notes"] == "North parking area"
    assert reactivated["active"] is True
    assert reactivated["log_count"] == 1
    store.close()


def test_flying_site_assignments_are_library_scoped(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    first_root = tmp_path / "first-library"
    second_root = tmp_path / "second-library"
    first_path = first_root / "flight.csv"
    second_path = second_root / "flight.csv"
    store.upsert_reception_records(first_root, [reception_record(first_path)])
    store.upsert_reception_records(second_root, [reception_record(second_path)])

    first_site = store.apply_reception_clusters(
        first_root,
        [cluster([first_path], 39.7744, -75.2049)],
    )[0]
    second_site = store.apply_reception_clusters(
        second_root,
        [cluster([second_path], 39.7744, -75.2049)],
    )[0]
    store.update_flying_site(first_site["id"], "First Library", "")
    store.update_flying_site(second_site["id"], "Second Library", "")

    store.apply_reception_clusters(first_root, [])

    assert store.list_flying_sites(first_root) == []
    assert store.list_flying_sites(first_root, active_only=False)[0]["name"] == "First Library"
    assert store.list_flying_sites(second_root)[0]["name"] == "Second Library"
    assert store.list_flying_sites(second_root)[0]["log_count"] == 1
    store.close()


def test_site_split_keeps_metadata_on_largest_overlap(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    root = tmp_path / "library"
    paths = [root / f"flight-{index}.csv" for index in range(4)]
    store.upsert_reception_records(root, [reception_record(path) for path in paths])

    original = store.apply_reception_clusters(
        root,
        [cluster(paths, 39.7744, -75.2049)],
    )[0]
    store.update_flying_site(original["id"], "Original Site", "Keep this metadata")

    split_sites = store.apply_reception_clusters(
        root,
        [
            cluster(paths[3:], 39.8044, -75.2049),
            cluster(paths[:3], 39.7744, -75.2049),
        ],
    )
    by_id = {site["id"]: site for site in split_sites}

    assert len(split_sites) == 2
    assert by_id[original["id"]]["name"] == "Original Site"
    assert by_id[original["id"]]["notes"] == "Keep this metadata"
    assert by_id[original["id"]]["log_count"] == 3
    new_site = next(site for site in split_sites if site["id"] != original["id"])
    assert new_site["name"] == ""
    assert new_site["log_count"] == 1
    store.close()


def test_site_merge_keeps_largest_overlap_site_and_inactivates_other(tmp_path: Path) -> None:
    store = AppStore(tmp_path / "state.sqlite3")
    root = tmp_path / "library"
    alpha_paths = [root / f"alpha-{index}.csv" for index in range(3)]
    beta_paths = [root / f"beta-{index}.csv" for index in range(2)]
    store.upsert_reception_records(
        root,
        [reception_record(path) for path in alpha_paths + beta_paths],
    )

    initial_sites = store.apply_reception_clusters(
        root,
        [
            cluster(alpha_paths, 39.7744, -75.2049),
            cluster(beta_paths, 39.8144, -75.2049),
        ],
    )
    alpha = next(site for site in initial_sites if site["log_count"] == 3)
    beta = next(site for site in initial_sites if site["log_count"] == 2)
    store.update_flying_site(alpha["id"], "Alpha", "winner")
    store.update_flying_site(beta["id"], "Beta", "inactive after merge")

    merged = store.apply_reception_clusters(
        root,
        [cluster(alpha_paths + beta_paths, 39.7900, -75.2049)],
    )
    all_sites = {site["id"]: site for site in store.list_flying_sites(root, active_only=False)}

    assert len(merged) == 1
    assert merged[0]["id"] == alpha["id"]
    assert merged[0]["name"] == "Alpha"
    assert merged[0]["notes"] == "winner"
    assert merged[0]["log_count"] == 5
    assert all_sites[beta["id"]]["active"] is False
    assert all_sites[beta["id"]]["name"] == "Beta"
    assert all_sites[beta["id"]]["log_count"] == 0
    assert {
        record["site_id"] for record in store.list_flying_site_records(alpha["id"])
    } == {alpha["id"]}
    store.close()
