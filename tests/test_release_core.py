from __future__ import annotations

import os
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sloppy_log_explorer.analysis import basic_stats, calculate_internal_resistance, cursor_values, guess_cell_count
from sloppy_log_explorer.parser import _deduplicate_columns, load_log, relative_seconds
from sloppy_log_explorer.storage import app_data_dir
from sloppy_log_explorer.sync import copy_candidates, discover_sync_candidates
from sloppy_log_explorer.voice import VoiceItem, generate_voice_pack, load_voice_csv


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_log_delimiters_and_extra_trailing_fields_preserve_column_alignment(tmp_path: Path, delimiter: str) -> None:
    path = tmp_path / "flight.csv"
    path.write_text(
        delimiter.join(["Time", "Voltage"]) + "\n"
        + delimiter.join(["0", "16.8", ""]) + "\n"
        + delimiter.join(["1", "16.7", "", ""]) + "\n",
        encoding="utf-8",
    )
    log = load_log(path)
    assert log.dataframe["Voltage"].tolist() == [16.8, 16.7]
    assert relative_seconds(log) == [0.0, 1.0]


def test_normalized_duplicate_headers_keep_every_sensor(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time, Voltage ,Voltage,Voltage.1\n0,10,20,30\n1,11,21,31\n", encoding="utf-8")
    log = load_log(path)
    assert list(log.dataframe.columns) == ["Time", "Voltage", "Voltage.2", "Voltage.1"]
    assert log.dataframe.iloc[0].tolist() == [0, 10, 20, 30]
    assert _deduplicate_columns(["RX", "RX", "RX.1", "RX"]) == ["RX", "RX.2", "RX.1", "RX.3"]


def test_numeric_time_gaps_remain_missing_until_forward_fill(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time,Voltage\n100,16.8\n101,16.7\ninf,16.6\n103,16.5\n", encoding="utf-8")
    log = load_log(path)
    assert log.time is not None and pd.isna(log.time.iloc[2])
    assert relative_seconds(log) == [0, 1, 1, 3]


def test_out_of_bounds_numeric_timestamps_do_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time,Voltage\n0,16.8\n1e100,16.7\n2,16.6\n", encoding="utf-8")
    assert relative_seconds(load_log(path)) == [0, 0, 2]


def test_time_only_log_retains_midnight_and_fractional_seconds(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time,Voltage\n23:59:59,16.8\n00:00:00.500,16.7\n00:00:01,16.6\n", encoding="utf-8")
    log = load_log(path)
    assert log.info.duration_seconds == 2
    assert relative_seconds(log) == [0, 1.5, 2]


def test_mixed_datetime_formats_retain_every_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Timestamp,Voltage\n2026-01-01T12:00:00,16.8\n2026-01-01T12:00:01.250,16.7\n", encoding="utf-8")
    assert relative_seconds(load_log(path)) == [0, 1.25]


def test_nonfinite_telemetry_is_missing_for_statistics_and_battery_fitting(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time,Voltage\n0,16\n1,inf\n2,-inf\n3,14\n", encoding="utf-8")
    log = load_log(path)
    assert log.dataframe["Voltage"].isna().tolist() == [False, True, True, False]
    assert basic_stats(pd.DataFrame({"Voltage": [16, float("inf"), 14]}), ["Voltage"])["Voltage"]["mean"] == 15
    current = np.arange(1, 20, dtype=float)
    voltage = 16.8 - current * 0.025
    voltage[0] = float("inf")
    current[-1] = float("inf")
    result = calculate_internal_resistance(pd.DataFrame({"V": voltage, "A": current}), "V", "A", 4)
    assert result is not None
    assert result.pack_milliohm == pytest.approx(25)
    assert result.samples == 17
    assert guess_cell_count(pd.Series([float("inf"), 15.2])) == 4


@pytest.mark.parametrize("mode", ["absolute", "relative"])
def test_compare_cursor_aligns_different_sample_rates(tmp_path: Path, mode: str) -> None:
    primary_path = tmp_path / "primary.csv"
    primary_path.write_text("Time,Voltage\n0,16\n1,15\n2,14\n3,13\n", encoding="utf-8")
    compare_path = tmp_path / "compare.csv"
    compare_path.write_text("Time,Voltage\n0,16\n2,14\n4,12\n", encoding="utf-8")
    values = cursor_values(load_log(primary_path), 2, ["Voltage"], load_log(compare_path), time_mode=mode)
    assert values[0].compare_value == 14
    assert values[0].delta == 0


def test_compare_cursor_does_not_report_stale_value_outside_time_range(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("Time,Voltage\n0,16\n5,15\n", encoding="utf-8")
    second.write_text("Time,Voltage\n0,16\n1,15\n", encoding="utf-8")
    result = cursor_values(load_log(first), 1, ["Voltage"], load_log(second))
    assert result[0].compare_value is None and result[0].delta is None


def test_compare_cursor_reuses_timeline_cache_and_handles_replaced_out_of_order_times(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "flight.csv"
    path.write_text("Time,Voltage\n0,16\n1,15\n2,14\n", encoding="utf-8")
    primary, compare = load_log(path), load_log(path)
    assert cursor_values(primary, 1, ["Voltage"], compare)[0].delta == 0
    assert primary.cursor_timeline is not None and compare.cursor_timeline is not None
    cached = compare.cursor_timeline
    with monkeypatch.context() as patch:
        patch.setattr(pd.Series, "ffill", lambda *_args, **_kwargs: pytest.fail("Cursor rebuilt a cached timeline"))
        assert cursor_values(primary, 2, ["Voltage"], compare, time_mode="relative")[0].delta == 0
    assert compare.cursor_timeline is cached
    compare.time = pd.Series(pd.to_datetime([0, 2, 1], unit="s"))
    assert cursor_values(primary, 1, ["Voltage"], compare)[0].compare_value == 14
    assert compare.cursor_timeline is not cached


def test_relative_appdata_becomes_an_absolute_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APPDATA", "relative-state")
    assert app_data_dir() == tmp_path / "relative-state" / "SloppyLogExplorer"


@pytest.mark.parametrize("filename", ["voice.wav:secret", "CON", "NUL.wav", "com1.WAV", "LPT²", "bad?.wav", "bad\x00.wav", ".."])
def test_voice_rejects_windows_devices_streams_and_invalid_names(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        generate_voice_pack([VoiceItem("hello", filename)], tmp_path)
    assert not list(tmp_path.iterdir())


def test_voice_rejects_duplicate_names_before_starting_the_engine(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        generate_voice_pack([VoiceItem("one", "hello"), VoiceItem("two", "HELLO.WAV")], tmp_path)
    assert not list(tmp_path.iterdir())


def test_voice_csv_handles_bom_and_short_rows(tmp_path: Path) -> None:
    path = tmp_path / "voice.csv"
    path.write_text("Text to be Spoken,Target WAV Filename\nHello,hello\nDraft\n", encoding="utf-8-sig")
    assert load_voice_csv(path) == [VoiceItem("Hello", "hello"), VoiceItem("Draft", "")]


def test_voice_engine_failure_preserves_existing_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail():
        raise RuntimeError("No system voice")

    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=fail))
    existing = tmp_path / "hello.wav"
    existing.write_bytes(b"existing spoken audio")
    with pytest.raises(RuntimeError, match="could not start"):
        generate_voice_pack([VoiceItem("hello", "hello")], tmp_path)
    assert existing.read_bytes() == b"existing spoken audio"


class _TestSpeechEngine:
    def __init__(self, fail_after: int | None = None):
        self.fail_after = fail_after
        self.count = 0
        self.stopped = False

    def save_to_file(self, text: str, filename: str) -> None:
        self.count += 1
        if self.fail_after is not None and self.count > self.fail_after:
            return
        with wave.open(filename, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(22050)
            output.writeframes(b"\x01\x00" * 100)

    def runAndWait(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.parametrize("fail_after", [None, 1])
def test_voice_verifies_all_outputs_before_replacing_existing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_after: int | None) -> None:
    engine = _TestSpeechEngine(fail_after)
    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=lambda: engine))
    existing = tmp_path / "one.wav"
    existing.write_bytes(b"original")
    jobs = [VoiceItem("one", "one"), VoiceItem("two", "two")]
    if fail_after is None:
        assert generate_voice_pack(jobs, tmp_path) == [existing, tmp_path / "two.wav"]
        with wave.open(str(existing), "rb") as audio:
            assert audio.getnframes() == 100
    else:
        with pytest.raises(RuntimeError, match="could not create"):
            generate_voice_pack(jobs, tmp_path)
        assert existing.read_bytes() == b"original"
        assert not (tmp_path / "two.wav").exists()
    assert engine.stopped
    assert not list(tmp_path.glob(".sloppy-voice-*"))


def _sync_directories(tmp_path: Path) -> tuple[Path, Path]:
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "flight.csv").write_text("Time,Voltage\n0,16\n", encoding="utf-8")
    return source, target


def test_sync_rejects_missing_and_overlapping_roots(tmp_path: Path) -> None:
    source, target = _sync_directories(tmp_path)
    for invalid_source, invalid_target in [(tmp_path / "missing", target), (source, source), (source, source / "child"), (source, tmp_path)]:
        with pytest.raises(ValueError):
            discover_sync_candidates(invalid_source, invalid_target)


def test_sync_failure_cannot_truncate_an_existing_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target = _sync_directories(tmp_path)
    destination = target / "flight.csv"
    destination.write_text("original", encoding="utf-8")
    old = (source / "flight.csv").stat().st_mtime - 10
    os.utime(destination, (old, old))
    candidates = discover_sync_candidates(source, target)

    def interrupted_copy(_source: Path, output: Path):
        output.write_text("partial", encoding="utf-8")
        raise OSError("SD card disconnected")

    monkeypatch.setattr("sloppy_log_explorer.sync.shutil.copy2", interrupted_copy)
    with pytest.raises(OSError, match="disconnected"):
        copy_candidates(candidates)
    assert destination.read_text(encoding="utf-8") == "original"
    assert not list(target.glob(".sloppy-sync-*"))


def test_sync_refuses_to_replace_a_destination_created_after_scanning(tmp_path: Path) -> None:
    source, target = _sync_directories(tmp_path)
    candidates = discover_sync_candidates(source, target)
    destination = target / "flight.csv"
    destination.write_text("new user content", encoding="utf-8")
    with pytest.raises(ValueError, match="destination changed"):
        copy_candidates(candidates)
    assert destination.read_text(encoding="utf-8") == "new user content"


def test_sync_rejects_a_target_symlink_escaping_the_library(tmp_path: Path) -> None:
    source, target = _sync_directories(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text("private", encoding="utf-8")
    try:
        (target / "flight.csv").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or elevated privileges")
    with pytest.raises(ValueError, match="outside"):
        discover_sync_candidates(source, target)
    assert outside.read_text(encoding="utf-8") == "private"
