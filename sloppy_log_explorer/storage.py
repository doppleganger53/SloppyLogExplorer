"""Small SQLite-backed app state store."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import RECEPTION_INDEX_VERSION


_MISSING = object()
_FLYING_SITE_MATCH_RADIUS_METERS = 2_000.0


def _normalized_path(path: str | Path, *, relative_to: str | Path | None = None) -> str:
    candidate = Path(path).expanduser()
    if relative_to is not None and not candidate.is_absolute():
        candidate = Path(relative_to) / candidate
    return os.path.normcase(str(candidate.resolve(strict=False)))


def _item_value(item: Mapping[str, Any] | object, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    if default is not _MISSING:
        return default
    joined = ", ".join(names)
    raise ValueError(f"reception record is missing required field: {joined}")


def _date_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    date_method = getattr(value, "date", None)
    if callable(date_method):
        converted = date_method()
        if isinstance(converted, date):
            return converted.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return text


def _channel_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [value]
        value = decoded if isinstance(decoded, (list, tuple, set)) else [decoded]
    try:
        values = list(value)
    except TypeError:
        values = [value]
    return list(dict.fromkeys(str(name) for name in values))


def _haversine_meters(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    radius_m = 6_371_008.8
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = math.sin(delta_lat / 2.0) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2.0) ** 2
    return 2.0 * radius_m * math.asin(min(1.0, math.sqrt(haversine)))


def app_data_dir() -> Path:
    # Use the Windows roaming app-data folder when available so settings follow
    # the user profile; fall back to a hidden home-directory cache elsewhere.
    base = os.environ.get("APPDATA")
    if base:
        root = Path(base) / "SloppyLogExplorer"
    else:
        root = Path.home() / ".sloppy_log_explorer"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class AppStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else app_data_dir() / "state.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        # Create tables idempotently so the app can open an empty database or an
        # older version without a separate migration tool.
        cur.executescript(
            """
            create table if not exists settings (
                key text primary key,
                value text not null
            );
            create table if not exists flights (
                file_path text primary key,
                model text,
                notes text default '',
                video_path text default '',
                updated_at text default current_timestamp
            );
            create table if not exists batteries (
                id integer primary key autoincrement,
                name text not null,
                cells integer not null default 4,
                active integer not null default 1,
                created_at text default current_timestamp
            );
            create table if not exists battery_history (
                id integer primary key autoincrement,
                battery_id integer not null references batteries(id) on delete cascade,
                file_path text not null,
                measured_at text default current_timestamp,
                pack_milliohm real not null,
                cell_milliohm real not null,
                health text not null
            );
            create table if not exists aliases (
                profile text not null,
                hardware text not null,
                alias text not null,
                primary key(profile, hardware)
            );
            create table if not exists flying_sites (
                id integer primary key autoincrement,
                library_root text not null,
                name text not null default '',
                notes text not null default '',
                active integer not null default 1,
                center_latitude real not null,
                center_longitude real not null,
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            create index if not exists flying_sites_library_active_idx
                on flying_sites(library_root, active);
            create table if not exists reception_logs (
                library_root text not null,
                file_path text not null,
                file_size integer not null,
                mtime_ns integer not null,
                status text not null,
                flight_date text,
                date_inferred integer not null default 0,
                center_latitude real,
                center_longitude real,
                channels_json text not null default '[]',
                error text not null default '',
                site_id integer references flying_sites(id) on delete set null,
                index_version integer not null default 0,
                updated_at text not null default current_timestamp,
                primary key(library_root, file_path)
            );
            create index if not exists reception_logs_library_site_idx
                on reception_logs(library_root, site_id);
            create index if not exists reception_logs_site_date_idx
                on reception_logs(site_id, flight_date);
            """
        )
        self._ensure_column("flights", "model", "text")
        self._ensure_column("flights", "notes", "text default ''")
        self._ensure_column("flights", "video_path", "text default ''")
        self._ensure_column("flights", "updated_at", "text")
        reception_columns = {
            row["name"]
            for row in self.conn.execute("pragma table_info(reception_logs)").fetchall()
        }
        reception_index_version_added = "index_version" not in reception_columns
        self._ensure_column("reception_logs", "index_version", "integer not null default 0")
        if reception_index_version_added:
            # Malformed and unavailable files are unaffected by GPS indexing
            # changes. Successful and prior no-GPS records are deliberately
            # refreshed because the header-aware scan can recover late fixes.
            self.conn.execute(
                """
                update reception_logs set index_version = ?
                where status in ('malformed', 'io_error')
                """,
                (RECEPTION_INDEX_VERSION,),
            )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f"pragma table_info({table})").fetchall()
        if column in {row["name"] for row in rows}:
            return
        self.conn.execute(f"alter table {table} add column {column} {definition}")

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("select value from settings where key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            # Preserve legacy plain-text values if they were written before the
            # store switched to JSON encoding.
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "insert into settings(key, value) values(?, ?) on conflict(key) do update set value = excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def save_flight(self, file_path: str, model: str, notes: str, video_path: str) -> None:
        self.conn.execute(
            """
            insert into flights(file_path, model, notes, video_path, updated_at)
            values(?, ?, ?, ?, current_timestamp)
            on conflict(file_path) do update set
              model = excluded.model,
              notes = excluded.notes,
              video_path = excluded.video_path,
              updated_at = current_timestamp
            """,
            (file_path, model, notes, video_path),
        )
        self.conn.commit()

    def get_flight(self, file_path: str) -> dict[str, str]:
        row = self.conn.execute("select * from flights where file_path = ?", (file_path,)).fetchone()
        if row is None:
            return {"notes": "", "video_path": ""}
        return dict(row)

    def add_battery(self, name: str, cells: int) -> int:
        cur = self.conn.execute("insert into batteries(name, cells) values(?, ?)", (name, cells))
        self.conn.commit()
        lastrowid = cur.lastrowid
        if lastrowid is None:
            raise RuntimeError("battery insert did not return a row id")
        return lastrowid

    def list_batteries(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("select * from batteries order by active desc, name collate nocase").fetchall()
        return [dict(row) for row in rows]

    def set_battery_active(self, battery_id: int, active: bool) -> None:
        self.conn.execute("update batteries set active = ? where id = ?", (1 if active else 0, battery_id))
        self.conn.commit()

    def add_battery_history(
        self,
        battery_id: int,
        file_path: str,
        pack_milliohm: float,
        cell_milliohm: float,
        health: str,
    ) -> None:
        self.conn.execute(
            """
            insert into battery_history(battery_id, file_path, pack_milliohm, cell_milliohm, health)
            values(?, ?, ?, ?, ?)
            """,
            (battery_id, file_path, pack_milliohm, cell_milliohm, health),
        )
        self.conn.commit()

    def list_battery_history(self, battery_id: int | None = None) -> list[dict[str, Any]]:
        if battery_id:
            rows = self.conn.execute(
                """
                select h.*, b.name as battery_name
                from battery_history h join batteries b on b.id = h.battery_id
                where battery_id = ?
                order by measured_at desc
                """,
                (battery_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                select h.*, b.name as battery_name
                from battery_history h join batteries b on b.id = h.battery_id
                order by measured_at desc
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def save_aliases(self, profile: str, aliases: dict[str, str]) -> None:
        # Replace the profile atomically so deleted aliases disappear instead of
        # lingering after a rename or a shorter edit pass.
        with self.conn:
            self.conn.execute("delete from aliases where profile = ?", (profile,))
            self.conn.executemany(
                "insert into aliases(profile, hardware, alias) values(?, ?, ?)",
                [(profile, hardware, alias) for hardware, alias in aliases.items()],
            )

    def load_aliases(self, profile: str) -> dict[str, str]:
        rows = self.conn.execute("select hardware, alias from aliases where profile = ? order by hardware", (profile,)).fetchall()
        return {row["hardware"]: row["alias"] for row in rows}

    def alias_profiles(self) -> list[str]:
        rows = self.conn.execute("select distinct profile from aliases order by profile collate nocase").fetchall()
        return [row["profile"] for row in rows]

    def list_reception_records(self, library_root: str | Path) -> list[dict[str, Any]]:
        root = _normalized_path(library_root)
        rows = self.conn.execute(
            "select * from reception_logs where library_root = ? order by file_path collate nocase",
            (root,),
        ).fetchall()
        return [self._reception_record_dict(row) for row in rows]

    def upsert_reception_records(
        self,
        library_root: str | Path,
        records: Iterable[Mapping[str, Any] | object],
    ) -> None:
        root = _normalized_path(library_root)
        values: list[tuple[Any, ...]] = []
        for record in records:
            file_path = _normalized_path(
                _item_value(record, "file_path", "path"),
                relative_to=root,
            )
            file_size = int(_item_value(record, "file_size", "size"))
            mtime_ns = int(_item_value(record, "mtime_ns", "modified_ns"))
            status = str(_item_value(record, "status"))
            flight_date = _date_text(_item_value(record, "flight_date", default=None))
            date_inferred = bool(
                _item_value(
                    record,
                    "date_inferred",
                    "date_is_inferred",
                    "flight_date_inferred",
                    default=False,
                )
            )
            center_latitude = self._optional_coordinate(
                _item_value(
                    record,
                    "center_latitude",
                    "centroid_latitude",
                    "latitude",
                    default=None,
                ),
                minimum=-90.0,
                maximum=90.0,
            )
            center_longitude = self._optional_coordinate(
                _item_value(
                    record,
                    "center_longitude",
                    "centroid_longitude",
                    "longitude",
                    default=None,
                ),
                minimum=-180.0,
                maximum=180.0,
            )
            channels = _channel_names(
                _item_value(
                    record,
                    "channels",
                    "numeric_channels",
                    "telemetry_channels",
                    default=(),
                )
            )
            error = str(_item_value(record, "error", "error_message", default="") or "")
            index_version = int(
                _item_value(record, "index_version", default=RECEPTION_INDEX_VERSION)
            )
            values.append(
                (
                    root,
                    file_path,
                    file_size,
                    mtime_ns,
                    status,
                    flight_date,
                    int(date_inferred),
                    center_latitude,
                    center_longitude,
                    json.dumps(channels, ensure_ascii=False),
                    error,
                    index_version,
                )
            )

        with self.conn:
            self.conn.executemany(
                """
                insert into reception_logs(
                    library_root, file_path, file_size, mtime_ns, status,
                    flight_date, date_inferred, center_latitude,
                    center_longitude, channels_json, error, index_version
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(library_root, file_path) do update set
                    file_size = excluded.file_size,
                    mtime_ns = excluded.mtime_ns,
                    status = excluded.status,
                    flight_date = excluded.flight_date,
                    date_inferred = excluded.date_inferred,
                    center_latitude = excluded.center_latitude,
                    center_longitude = excluded.center_longitude,
                    channels_json = excluded.channels_json,
                    error = excluded.error,
                    index_version = excluded.index_version,
                    updated_at = current_timestamp
                """,
                values,
            )

    def remove_missing_reception_records(
        self,
        library_root: str | Path,
        present_paths: Iterable[str | Path],
    ) -> int:
        root = _normalized_path(library_root)
        normalized_paths = {
            _normalized_path(path, relative_to=root)
            for path in present_paths
        }
        with self.conn:
            self.conn.execute(
                "create temp table if not exists current_reception_paths (file_path text primary key)"
            )
            self.conn.execute("delete from current_reception_paths")
            self.conn.executemany(
                "insert into current_reception_paths(file_path) values(?)",
                [(path,) for path in sorted(normalized_paths)],
            )
            cursor = self.conn.execute(
                """
                delete from reception_logs
                where library_root = ?
                  and not exists (
                      select 1 from current_reception_paths current
                      where current.file_path = reception_logs.file_path
                  )
                """,
                (root,),
            )
        return max(cursor.rowcount, 0)

    def list_flying_sites(
        self,
        library_root: str | Path,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        root = _normalized_path(library_root)
        active_clause = "and site.active = 1" if active_only else ""
        rows = self.conn.execute(
            f"""
            select
                site.*,
                count(log.file_path) as log_count,
                min(log.flight_date) as earliest_flight_date,
                max(log.flight_date) as latest_flight_date,
                coalesce(sum(log.date_inferred), 0) as inferred_date_count
            from flying_sites site
            left join reception_logs log
              on log.library_root = site.library_root and log.site_id = site.id
            where site.library_root = ? {active_clause}
            group by site.id
            order by site.active desc, site.name collate nocase, site.id
            """,
            (root,),
        ).fetchall()
        result = [dict(row) for row in rows]
        for site in result:
            site["active"] = bool(site["active"])
        return result

    def update_flying_site(self, site_id: int, name: str, notes: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                update flying_sites
                set name = ?, notes = ?, updated_at = current_timestamp
                where id = ?
                """,
                (str(name), str(notes), int(site_id)),
            )

    def list_flying_site_records(self, site_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "select * from reception_logs where site_id = ? order by flight_date, file_path collate nocase",
            (int(site_id),),
        ).fetchall()
        return [self._reception_record_dict(row) for row in rows]

    def apply_reception_clusters(
        self,
        library_root: str | Path,
        clusters: Iterable[Mapping[str, Any] | object],
    ) -> list[dict[str, Any]]:
        root = _normalized_path(library_root)
        prepared = self._prepare_reception_clusters(root, clusters)

        with self.conn:
            site_rows = self.conn.execute(
                "select * from flying_sites where library_root = ? order by id",
                (root,),
            ).fetchall()
            old_sites = {int(row["id"]): dict(row) for row in site_rows}
            membership_rows = self.conn.execute(
                """
                select file_path, site_id from reception_logs
                where library_root = ? and site_id is not null
                """,
                (root,),
            ).fetchall()
            old_members: dict[int, set[str]] = {site_id: set() for site_id in old_sites}
            for row in membership_rows:
                old_members.setdefault(int(row["site_id"]), set()).add(str(row["file_path"]))

            known_paths = {
                str(row["file_path"])
                for row in self.conn.execute(
                    "select file_path from reception_logs where library_root = ?",
                    (root,),
                ).fetchall()
            }
            clustered_paths = {
                path
                for cluster in prepared
                for path in cluster["file_paths"]
            }
            unknown_paths = clustered_paths - known_paths
            if unknown_paths:
                first = sorted(unknown_paths)[0]
                raise ValueError(f"reception cluster references an uncached file: {first}")

            assignments: dict[int, int] = {}
            used_sites: set[int] = set()
            overlap_candidates: list[tuple[int, float, int, int]] = []
            for cluster_index, cluster in enumerate(prepared):
                members = set(cluster["file_paths"])
                for site_id, site in old_sites.items():
                    overlap = len(members & old_members.get(site_id, set()))
                    if overlap:
                        distance = _haversine_meters(
                            float(cluster["center_latitude"]),
                            float(cluster["center_longitude"]),
                            float(site["center_latitude"]),
                            float(site["center_longitude"]),
                        )
                        if distance > _FLYING_SITE_MATCH_RADIUS_METERS:
                            continue
                        overlap_candidates.append((-overlap, distance, site_id, cluster_index))
            for _negative_overlap, _distance, site_id, cluster_index in sorted(overlap_candidates):
                if cluster_index in assignments or site_id in used_sites:
                    continue
                assignments[cluster_index] = site_id
                used_sites.add(site_id)

            distance_candidates: list[tuple[float, int, int]] = []
            for cluster_index, cluster in enumerate(prepared):
                if cluster_index in assignments:
                    continue
                for site_id, site in old_sites.items():
                    if site_id in used_sites:
                        continue
                    distance = _haversine_meters(
                        float(cluster["center_latitude"]),
                        float(cluster["center_longitude"]),
                        float(site["center_latitude"]),
                        float(site["center_longitude"]),
                    )
                    if distance <= _FLYING_SITE_MATCH_RADIUS_METERS:
                        distance_candidates.append((distance, site_id, cluster_index))
            for _distance, site_id, cluster_index in sorted(distance_candidates):
                if cluster_index in assignments or site_id in used_sites:
                    continue
                assignments[cluster_index] = site_id
                used_sites.add(site_id)

            for cluster_index, cluster in enumerate(prepared):
                if cluster_index in assignments:
                    continue
                cursor = self.conn.execute(
                    """
                    insert into flying_sites(
                        library_root, center_latitude, center_longitude
                    ) values(?, ?, ?)
                    """,
                    (
                        root,
                        cluster["center_latitude"],
                        cluster["center_longitude"],
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("flying-site insert did not return a row id")
                assignments[cluster_index] = int(cursor.lastrowid)

            self.conn.execute(
                "update flying_sites set active = 0, updated_at = current_timestamp where library_root = ?",
                (root,),
            )
            self.conn.execute(
                "update reception_logs set site_id = null where library_root = ?",
                (root,),
            )
            for cluster_index, cluster in enumerate(prepared):
                site_id = assignments[cluster_index]
                self.conn.execute(
                    """
                    update flying_sites
                    set active = 1,
                        center_latitude = ?,
                        center_longitude = ?,
                        updated_at = current_timestamp
                    where id = ? and library_root = ?
                    """,
                    (
                        cluster["center_latitude"],
                        cluster["center_longitude"],
                        site_id,
                        root,
                    ),
                )
                self.conn.executemany(
                    """
                    update reception_logs set site_id = ?
                    where library_root = ? and file_path = ?
                    """,
                    [(site_id, root, path) for path in cluster["file_paths"]],
                )

        return self.list_flying_sites(root)

    @staticmethod
    def _optional_coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
        if value is None or value == "":
            return None
        number = float(value)
        if not math.isfinite(number) or not minimum <= number <= maximum:
            return None
        return number

    @staticmethod
    def _reception_record_dict(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record.pop("updated_at", None)
        try:
            channels = json.loads(record.pop("channels_json"))
        except (json.JSONDecodeError, TypeError):
            channels = []
        record["channels"] = _channel_names(channels)
        record["date_inferred"] = bool(record["date_inferred"])
        return record

    @staticmethod
    def _prepare_reception_clusters(
        root: str,
        clusters: Iterable[Mapping[str, Any] | object],
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        used_paths: set[str] = set()
        for cluster in clusters:
            raw_members = _item_value(
                cluster,
                "file_paths",
                "member_paths",
                "log_paths",
                "members",
                "records",
            )
            members: set[str] = set()
            for member in raw_members:
                if isinstance(member, (str, Path)):
                    path = member
                else:
                    path = _item_value(member, "file_path", "path")
                members.add(_normalized_path(path, relative_to=root))
            if not members:
                continue
            duplicates = members & used_paths
            if duplicates:
                first = sorted(duplicates)[0]
                raise ValueError(f"reception file appears in more than one cluster: {first}")
            used_paths.update(members)

            latitude = float(
                _item_value(
                    cluster,
                    "center_latitude",
                    "centroid_latitude",
                    "latitude",
                )
            )
            longitude = float(
                _item_value(
                    cluster,
                    "center_longitude",
                    "centroid_longitude",
                    "longitude",
                )
            )
            if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
                raise ValueError("reception cluster latitude must be a finite value from -90 to 90")
            if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
                raise ValueError("reception cluster longitude must be a finite value from -180 to 180")
            prepared.append(
                {
                    "file_paths": tuple(sorted(members)),
                    "center_latitude": latitude,
                    "center_longitude": longitude,
                }
            )

        prepared.sort(
            key=lambda cluster: (
                cluster["file_paths"],
                cluster["center_latitude"],
                cluster["center_longitude"],
            )
        )
        return prepared
