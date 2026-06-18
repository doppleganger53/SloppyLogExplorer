"""Small SQLite-backed app state store."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def app_data_dir() -> Path:
    # Use the Windows roaming app-data folder when available so settings follow
    # the user profile; fall back to a hidden home-directory cache elsewhere.
    base = os.environ.get("APPDATA")
    if base:
        root = Path(base) / "SloppyLogExplorer"
    else:
        root = Path.home() / ".sloppy_log_explorer"
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
            """
        )
        self._ensure_column("flights", "model", "text")
        self._ensure_column("flights", "notes", "text default ''")
        self._ensure_column("flights", "video_path", "text default ''")
        self._ensure_column("flights", "updated_at", "text")
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
