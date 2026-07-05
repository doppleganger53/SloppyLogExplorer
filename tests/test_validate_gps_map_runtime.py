from __future__ import annotations

from pathlib import Path

from sloppy_log_explorer.parser import load_log, relative_seconds
from tools.validate_gps_map_runtime import _gps_runtime_options, _gps_validation_scope


def write_delayed_gps_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,Current(A),GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,0.5,0,0,0",
                "2026-01-01,12:00:01,10.0,0,0,0",
                "2026-01-01,12:00:02,20.0,0,0,0",
                "2026-01-01,12:00:03,30.0,0,0,0",
                "2026-01-01,12:00:04,40.0,0,0,0",
                "2026-01-01,12:00:05,50.0,39.5,-75.5,20",
                "2026-01-01,12:00:06,60.0,39.6,-75.6,22",
                "2026-01-01,12:00:07,70.0,39.7,-75.7,24",
                "2026-01-01,12:00:08,80.0,39.8,-75.8,26",
            ]
        ),
        encoding="utf-8",
    )


def write_sparse_gps_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,Current(A),GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,0.5,0,0,0",
                "2026-01-01,12:00:01,10.0,0,0,0",
                "2026-01-01,12:00:02,20.0,0,0,0",
                "2026-01-01,12:00:03,30.0,0,0,0",
                "2026-01-01,12:00:04,40.0,0,0,0",
                "2026-01-01,12:00:05,50.0,39.5,-75.5,20",
                "2026-01-01,12:00:06,60.0,0,0,0",
                "2026-01-01,12:00:07,70.0,0,0,0",
                "2026-01-01,12:00:08,80.0,0,0,0",
                "2026-01-01,12:00:09,90.0,0,0,0",
                "2026-01-01,12:00:10,100.0,39.8,-75.8,26",
            ]
        ),
        encoding="utf-8",
    )


def test_gps_runtime_options_preserve_scoped_bounds() -> None:
    options = _gps_runtime_options(
        color_column="Current(A)",
        scope_start_seconds=1.0,
        scope_end_seconds=4.0,
    )

    assert options.color_column == "Current(A)"
    assert options.scope_start_seconds == 1.0
    assert options.scope_end_seconds == 4.0


def test_gps_validation_scope_uses_delayed_valid_coordinate_rows(tmp_path: Path) -> None:
    path = tmp_path / "delayed-gps.csv"
    write_delayed_gps_sample(path)
    log = load_log(path)

    scope_start, scope_end = _gps_validation_scope(log, relative_seconds(log))

    assert scope_start == 5.0
    assert scope_end == 8.0


def test_gps_validation_scope_falls_back_to_sparse_valid_coordinate_span(tmp_path: Path) -> None:
    path = tmp_path / "sparse-gps.csv"
    write_sparse_gps_sample(path)
    log = load_log(path)

    scope_start, scope_end = _gps_validation_scope(log, relative_seconds(log))

    assert scope_start == 5.0
    assert scope_end == 10.0
