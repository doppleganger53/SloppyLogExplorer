from __future__ import annotations

from pathlib import Path

import pytest

from sloppy_log_explorer.analysis import calculate_internal_resistance, cursor_values
from sloppy_log_explorer.parser import load_log
from sloppy_log_explorer.sync import copy_candidates, discover_sync_candidates


def write_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,VFAS(V),Current(A),GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,16.80,0.5,39.0,-75.0,10",
                "2026-01-01,12:00:01,16.55,10.0,39.1,-75.1,12",
                "2026-01-01,12:00:02,16.30,20.0,39.2,-75.2,14",
                "2026-01-01,12:00:03,16.05,30.0,39.3,-75.3,16",
                "2026-01-01,12:00:04,15.80,40.0,39.4,-75.4,18",
                "2026-01-01,12:00:05,15.55,50.0,39.5,-75.5,20",
                "2026-01-01,12:00:06,15.30,60.0,39.6,-75.6,22",
                "2026-01-01,12:00:07,15.05,70.0,39.7,-75.7,24",
                "2026-01-01,12:00:08,14.80,80.0,39.8,-75.8,26",
            ]
        ),
        encoding="utf-8",
    )


def test_load_log_detects_time_numeric_and_gps(tmp_path: Path) -> None:
    path = tmp_path / "Panther" / "flight.csv"
    path.parent.mkdir()
    write_sample(path)

    log = load_log(path, tmp_path)

    assert log.info.model == "Panther"
    assert log.info.rows == 9
    assert log.info.has_gps is True
    assert "VFAS(V)" in log.parameter_columns
    assert log.info.duration_seconds == 8


def test_internal_resistance_regression(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    result = calculate_internal_resistance(log.dataframe, "VFAS(V)", "Current(A)", cells=4)

    assert result is not None
    assert 20 <= result.pack_milliohm <= 40
    assert result.health in {"Good", "Fair"}


def test_cursor_values_include_compare_delta(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_sample(first)
    write_sample(second)
    primary = load_log(first)
    compare = load_log(second)
    compare.dataframe["VFAS(V)"] = compare.dataframe["VFAS(V)"] - 0.1

    values = cursor_values(primary, 2, ["VFAS(V)"], compare)

    assert values[0].delta == pytest.approx(0.1)


def test_sync_candidates_copy_newer_logs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    write_sample(source / "flight.csv")

    candidates = discover_sync_candidates(source, target)
    copied = copy_candidates(candidates)

    assert copied == 1
    assert (target / "flight.csv").exists()
