"""GPS-paired AirWare TLM observations for the flying-site reception index."""

from __future__ import annotations

import csv
import re
import struct
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

RSSI_CHANNELS = ("Spektrum RSSI (dBm)", "Spektrum RSSI (%)", "Spektrum RSSI B (dBm)")
LOSS_CHANNELS = tuple(
    f"Spektrum {name} (/s)"
    for name in ("A fades", "B fades", "L fades", "R fades", "frame losses", "holds")
)
_MAX_PAIR_TICKS = 100  # TLM clock ticks are 10 ms.


def _gps_fix(payload: bytes, sensor: int) -> tuple[float, float] | None:
    if sensor == 0x26:
        if not 3 <= payload[15] < 0xFF:
            return None
        raw_lat, raw_lon = struct.unpack_from(">ii", payload, 4)
        latitude, longitude = raw_lat / 1e7, raw_lon / 1e7
    else:
        flags = payload[15]
        if not flags & 8:
            return None
        coords = []
        for offset in (4, 8):
            digits = payload[offset:offset + 4][::-1].hex()
            if not digits.isdecimal():
                return None
            minutes = int(digits[2:]) / 10000
            if minutes >= 60:
                return None
            coords.append(int(digits[:2]) + minutes / 60)
        latitude, longitude = coords
        if flags & 4:
            longitude += 100
        if not flags & 1:
            latitude = -latitude
        if not flags & 2:
            longitude = -longitude
    if abs(latitude) > 90 or abs(longitude) > 180 or (latitude, longitude) == (0, 0):
        return None
    return latitude, longitude


def recording_date(name: str, modified: float) -> tuple[date, str]:
    match = re.search(r"(?<!\d)((?:19|20)\d\d)[_-]?(\d\d)[_-]?(\d\d)(?!\d)", name)
    if match:
        try:
            return date(*(int(part) for part in match.groups())), "filename"
        except ValueError:
            pass
    return datetime.fromtimestamp(modified).date(), "mtime"


def _clock(tick: int) -> str:
    seconds, centiseconds = divmod(tick, 100)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}.{centiseconds:02}"


def write_reception_csv(
    source: BinaryIO, output: Path, day: date, check_cancelled: Callable[[], None],
) -> tuple[str, str]:
    """Convert paired frames to the normal CSV parser's input, using bounded memory."""
    model = ""
    gps = previous = None
    last_tick = -1
    saw_gps = wrote = False
    with output.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(["Date", "Time", "GPS Lat", "GPS Lon", *RSSI_CHANNELS, *LOSS_CHANNELS])
        while stamp := source.read(4):
            check_cancelled()
            if stamp == b"\xff" * 4:
                header = source.read(32)
                if len(header) != 32:
                    raise ValueError("Incomplete TLM session header")
                if header[0] == 0x12:
                    name = header[6:].split(b"\0")[0].decode("ascii", "replace").strip()
                    if model and name and name != model:
                        return "multiple_models", ""
                    model = name or model
                gps = previous = None
                last_tick = -1
                continue
            payload = source.read(16)
            if len(payload) != 16 or len(stamp) != 4:
                # Some transmitters leave a final clock word at EOF.
                if len(stamp) == 4 and not payload:
                    break
                raise ValueError("Incomplete TLM telemetry frame")
            tick = int.from_bytes(stamp, "little")
            if tick < last_tick:
                gps = previous = None
            last_tick = tick
            sensor = payload[1] or payload[0]
            if sensor in {0x16, 0x26}:
                fix = _gps_fix(payload, sensor)
                gps = (tick, fix) if fix else None
                saw_gps |= bool(fix)
                if fix is None:
                    previous = None
                else:
                    # Give the existing site/GPS validator the actual fixes even
                    # when this flight has just one paired reception reading.
                    writer.writerow([day, _clock(tick), *fix])
                continue
            if gps is None or tick - gps[0] > _MAX_PAIR_TICKS:
                previous = None
                continue
            values: list[int | float | None] = [None] * (len(RSSI_CHANNELS) + len(LOSS_CHANNELS))
            if sensor == 0x7E:
                a, b = struct.unpack_from("bb", payload, 8)
                if a < -1:
                    values[0] = a
                if b < -1:
                    values[2] = b
                percent = b if b > 0 else a
                if 0 < percent <= 100:
                    values[1] = percent
            elif sensor == 0x7F:
                counters = struct.unpack_from(">6H", payload, 2)
                if previous and 0 < tick - previous[0] <= _MAX_PAIR_TICKS:
                    seconds = (tick - previous[0]) / 100
                    for i, (old, new) in enumerate(zip(previous[1], counters)):
                        unavailable = 0xFFFE if i in (1, 2, 3) else 0xFFFF
                        if old <= new < unavailable:
                            values[len(RSSI_CHANNELS) + i] = (new - old) / seconds
                previous = tick, counters
            if any(v is not None for v in values):
                writer.writerow([day, _clock(tick), *gps[1], *values])
                wrote = True
    status = "ok" if wrote else ("no_reception" if saw_gps else "no_gps")
    return status, model
