"""Telemetry log loading plus time and GPS column heuristics."""

from __future__ import annotations

import re
import csv
from pathlib import Path

import pandas as pd

from .models import GpsColumns, LoadedLog, LogFileInfo

LOG_EXTENSIONS = {".csv", ".log"}
# GPS parsing uses helper columns internally so the rest of the app can work
# with normalized numeric lat/lon values even when the source log is messy.
_GPS_HELPER_LAT = "__gps_latitude"
_GPS_HELPER_LON = "__gps_longitude"
_GPS_HELPER_ALT = "__gps_altitude"
_COORDINATE_DECIMAL_RE = re.compile(
    r"(?P<prefix>[NSEW])?\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<suffix>[NSEW])?",
    re.IGNORECASE,
)


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Remove pandas' auto-generated placeholder columns from ragged exports and
    # normalize names so later heuristics can compare them reliably.
    mask = df.columns.to_series().astype(str).str.match(r"^Unnamed", na=False)
    df = df.loc[:, ~mask]
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _coerce_numeric_series(series: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    numeric = pd.to_numeric(series, errors="coerce")
    return pd.Series(numeric, index=series.index)


def _coerce_datetime_series(series: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    parsed = pd.to_datetime(series, errors="coerce")
    return pd.Series(parsed, index=series.index)


def _deduplicate_columns(columns: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, column in enumerate(columns):
        name = str(column).strip()
        if not name:
            name = f"Unnamed: {index}"
        if name in counts:
            counts[name] += 1
            result.append(f"{name}.{counts[name]}")
        else:
            counts[name] = 0
            result.append(name)
    return result


def _read_ragged_csv(path: Path) -> pd.DataFrame:
    # FrSky-style exports sometimes have uneven rows or delimiter drift, so we
    # fall back to the stdlib csv reader when pandas cannot infer the layout.
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(handle, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return pd.DataFrame()

        width = len(header)
        rows: list[list[str]] = []
        for row in reader:
            if not row:
                continue
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                row = row[:width]
            rows.append(row)
    return pd.DataFrame(rows, columns=pd.Index(_deduplicate_columns(header)))


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return _read_ragged_csv(path)


def _detect_time(df: pd.DataFrame) -> pd.Series | None:
    columns = list(df.columns)
    lower = {c: c.lower().strip() for c in columns}
    date_cols = [c for c in columns if "date" in lower[c]]
    time_cols = [c for c in columns if "time" in lower[c]]

    if date_cols and time_cols:
        # Prefer a combined date/time parse when both fields exist because many
        # logs split the timestamp across two columns.
        series = _coerce_datetime_series(
            df[date_cols[0]].astype(str).str.strip() + " " + df[time_cols[0]].astype(str).str.strip(),
        )
        if series.notna().any():
            return series

    for col in time_cols + date_cols:
        raw = df[col]
        parsed = _coerce_datetime_series(raw)
        if parsed.notna().sum() >= max(1, len(df) // 3):
            return parsed
        numeric = _coerce_numeric_series(raw)
        if numeric.notna().sum() >= max(1, len(df) // 3):
            # Some exports store elapsed seconds as a bare number; convert them
            # to timestamps anchored at the Unix epoch so Plotly can format them.
            base = pd.Timestamp("1970-01-01")
            return pd.Series(base + pd.to_timedelta(numeric.fillna(0), unit="s"), index=numeric.index)

    return None


def _model_from_path(path: Path, library_root: Path | None = None) -> str:
    if library_root:
        try:
            # When scanning a library, the first folder below the root usually
            # names the aircraft/model better than the filename itself.
            rel = path.relative_to(library_root)
            if len(rel.parts) > 1:
                return rel.parts[0]
            stem = re.split(r"[-_ ]\d{4}", path.stem, maxsplit=1)[0].strip()
            if stem:
                return stem
        except ValueError:
            pass
    for parent in path.parents:
        name = parent.name
        if name.lower() not in {"logs", "log", "telemetry", "csv", "sd", "models"}:
            return name
    # Fall back to a stem prefix so root-level export filenames still group
    # sensibly even when there is no helpful directory structure.
    stem = path.stem
    return re.split(r"[-_ ]\d{4}", stem, maxsplit=1)[0] or "Unsorted"


def _numeric_columns(df: pd.DataFrame, time: pd.Series | None) -> list[str]:
    numeric: list[str] = []
    for col in df.columns:
        if time is not None and ("date" in col.lower() or "time" in col.lower()):
            continue
        converted = _coerce_numeric_series(df[col])
        if converted.notna().any():
            # Coerce in place so downstream plotting and GPS heuristics can use
            # a stable numeric dtype instead of re-parsing each column later.
            df[col] = converted
            numeric.append(col)
    return numeric


def _find_named_column(columns: list[str], includes: tuple[str, ...], excludes: tuple[str, ...] = ()) -> str | None:
    for col in columns:
        name = col.lower()
        if all(token in name for token in includes) and not any(token in name for token in excludes):
            return col
    return None


def _axis_name_bonus(column: str, axis: str) -> float:
    name = column.lower()
    score = 0.0
    if "gps" in name:
        score += 0.5
    if axis == "lat":
        if "latitude" in name:
            score += 4.0
        elif "lat" in name:
            score += 3.0
        if "north" in name:
            score += 1.0
    else:
        if "longitude" in name:
            score += 4.0
        elif "lon" in name or "lng" in name:
            score += 3.0
        if "east" in name:
            score += 1.0
    return score


def _has_coordinate_name_hint(column: str) -> bool:
    name = column.lower()
    return any(
        token in name
        for token in (
            "gps",
            "lat",
            "latitude",
            "lon",
            "lng",
            "longitude",
            "north",
            "south",
            "east",
            "west",
            "coord",
            "position",
        )
    )


def _signed_coordinate(value: float, hemisphere: str | None) -> float:
    if hemisphere is None:
        return value
    hemisphere = hemisphere.upper()
    if hemisphere in {"S", "W"}:
        return -abs(value)
    if hemisphere in {"N", "E"}:
        return abs(value)
    return value


def _parse_coordinate_text(text: str) -> tuple[float, float, float | None] | None:
    cleaned = text.strip().strip("()[]{}").replace("−", "-")
    if not cleaned:
        return None
    # Ignore strings that do not look like coordinate pairs/triples at all.
    if not any(sep in cleaned for sep in (",", ";", "|", "/")) and cleaned.count(" ") < 1:
        return None

    tokens: list[tuple[float, str | None]] = []
    for match in _COORDINATE_DECIMAL_RE.finditer(cleaned):
        raw_value = match.group("value")
        if raw_value is None:
            continue
        hemisphere = (match.group("prefix") or match.group("suffix") or "").upper() or None
        try:
            tokens.append((float(raw_value), hemisphere))
        except ValueError:
            continue
    if len(tokens) not in {2, 3}:
        return None

    lat_from_hemi = [_signed_coordinate(value, hemi) for value, hemi in tokens if hemi in {"N", "S"}]
    lon_from_hemi = [_signed_coordinate(value, hemi) for value, hemi in tokens if hemi in {"E", "W"}]
    if lat_from_hemi and lon_from_hemi:
        lat = lat_from_hemi[0]
        lon = lon_from_hemi[0]
        altitude = next((value for value, hemi in tokens if hemi is None), None)
    else:
        values = [_signed_coordinate(value, hemi) for value, hemi in tokens]
        first, second = values[0], values[1]
        if abs(first) > 90 and abs(second) <= 90:
            lon, lat = first, second
        else:
            lat, lon = first, second
        altitude = values[2] if len(values) == 3 else None

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon, altitude


def _series_axis_score(series: pd.Series, axis: str, column: str) -> float | None:
    valid = _coerce_numeric_series(series).dropna()
    if len(valid) < 2:
        return None
    if axis == "lat":
        in_range = valid.between(-90, 90)
        bound = 90.0
    else:
        in_range = valid.between(-180, 180)
        bound = 180.0
    coverage = float(in_range.mean())
    if coverage < 0.8:
        return None

    score = coverage * 10.0
    # Column-name hints help disambiguate latitude/longitude lookalikes when
    # the value ranges alone are not enough.
    score += _axis_name_bonus(column, axis)
    score += min(1.0, float(valid.abs().median()) / bound if bound else 0.0)
    return score


def _detect_coordinate_string_gps(df: pd.DataFrame, numeric_columns: list[str]) -> GpsColumns | None:
    best: tuple[float, str, pd.Series, pd.Series, pd.Series, str] | None = None
    numeric_set = set(numeric_columns)
    for column in df.columns:
        if column in numeric_set:
            continue
        series = df[column]
        if not _has_coordinate_name_hint(column) and not _series_has_coordinate_text_sample(series):
            continue
        lat_values: list[float] = []
        lon_values: list[float] = []
        alt_values: list[float | None] = []
        parsed_count = 0
        for value in series:
            if pd.isna(value):
                lat_values.append(float("nan"))
                lon_values.append(float("nan"))
                alt_values.append(None)
                continue
            text = str(value).strip().strip("()[]{}")
            parsed = _parse_coordinate_text(text)
            if parsed is None:
                lat_values.append(float("nan"))
                lon_values.append(float("nan"))
                alt_values.append(None)
                continue
            lat, lon, alt = parsed
            lat_values.append(lat)
            lon_values.append(lon)
            alt_values.append(alt)
            parsed_count += 1

        if parsed_count < 2:
            continue
        lat_series = pd.Series(lat_values, index=df.index, dtype="float64")
        lon_series = pd.Series(lon_values, index=df.index, dtype="float64")
        valid = lat_series.between(-90, 90) & lon_series.between(-180, 180)
        non_origin = valid & ((lat_series.abs() > 1e-9) | (lon_series.abs() > 1e-9))
        if non_origin.sum() < 2:
            continue
        score = parsed_count
        score += _axis_name_bonus(column, "lat")
        score += _axis_name_bonus(column, "lon")
        if best is None or score > best[0]:
            # Keep the parsed coordinate column as the user-visible label while
            # the helper columns carry the normalized numeric values.
            alt_series = pd.Series(alt_values, index=df.index, dtype="float64") if any(v is not None for v in alt_values) else pd.Series([pd.NA] * len(df), index=df.index)
            best = (score, column, lat_series, lon_series, alt_series, column)

    if best is None:
        return None

    _, column, lat_series, lon_series, alt_series, _ = best
    df[_GPS_HELPER_LAT] = lat_series
    df[_GPS_HELPER_LON] = lon_series
    altitude = None
    altitude_label = None
    if alt_series.notna().any():
        df[_GPS_HELPER_ALT] = alt_series
        altitude = _GPS_HELPER_ALT
        altitude_label = f"{column} (alt)"
    else:
        external_altitude = (
            _find_named_column(numeric_columns, ("gps", "alt"))
            or _find_named_column(numeric_columns, ("alt",))
            or _find_named_column(numeric_columns, ("height",))
            or _find_named_column(numeric_columns, ("gps", "z"))
        )
        if external_altitude:
            altitude = external_altitude
            altitude_label = external_altitude
    return GpsColumns(
        latitude=_GPS_HELPER_LAT,
        longitude=_GPS_HELPER_LON,
        altitude=altitude,
        latitude_label=f"{column} (lat)",
        longitude_label=f"{column} (lon)",
        altitude_label=altitude_label,
    )


def _series_has_coordinate_text_sample(series: pd.Series | pd.DataFrame, sample_size: int = 200) -> bool:
    if isinstance(series, pd.DataFrame):
        series = pd.Series(series.to_numpy()[:, 0], index=series.index)
    parsed = 0
    checked = 0
    iterator = iter(series)
    for value in iterator:
        if pd.isna(value):
            continue
        checked += 1
        if _parse_coordinate_text(str(value)) is not None:
            parsed += 1
            if parsed >= 2:
                return True
        if checked >= sample_size:
            break
    # If no GPS-like coordinates were found in the initial startup/status rows,
    # keep scanning the remaining rows so late-emitted coordinate strings can
    # still be discovered in unhinted text columns.
    for value in iterator:
        if pd.isna(value):
            continue
        if _parse_coordinate_text(str(value)) is not None:
            parsed += 1
            if parsed >= 2:
                return True
    return False


def _detect_split_gps_columns(df: pd.DataFrame, numeric_columns: list[str]) -> GpsColumns | None:
    coordinate_candidates = [column for column in numeric_columns if _has_coordinate_name_hint(column)]
    if len(coordinate_candidates) < 2:
        return None

    usable: dict[str, pd.Series] = {}
    for column in coordinate_candidates:
        series = _coerce_numeric_series(df[column])
        valid = series.dropna()
        if len(valid) < 2:
            continue
        lat_coverage = float(valid.between(-90, 90).mean())
        lon_coverage = float(valid.between(-180, 180).mean())
        if lat_coverage < 0.8 and lon_coverage < 0.8:
            continue
        usable[column] = series

    if not usable:
        return None

    # If there are obvious lat/lon names, trust them first before falling back
    # to range-based scoring.
    lat_named = _find_named_column(list(usable), ("lat",)) or _find_named_column(list(usable), ("gps", "la"))
    lon_named = (
        _find_named_column(list(usable), ("lon",))
        or _find_named_column(list(usable), ("lng",))
        or _find_named_column(list(usable), ("gps", "lo"))
    )
    if lat_named and lon_named and lat_named != lon_named:
        lat_series = usable[lat_named]
        lon_series = usable[lon_named]
        valid = lat_series.between(-90, 90) & lon_series.between(-180, 180)
        non_origin = valid & ((lat_series.abs() > 1e-9) | (lon_series.abs() > 1e-9))
        if non_origin.sum() >= 2:
            alt = (
                _find_named_column(numeric_columns, ("alt",))
                or _find_named_column(numeric_columns, ("height",))
                or _find_named_column(numeric_columns, ("gps", "z"))
            )
            return GpsColumns(
                latitude=lat_named,
                longitude=lon_named,
                altitude=alt,
                latitude_label=lat_named,
                longitude_label=lon_named,
                altitude_label=alt,
            )

    best: tuple[float, str, str] | None = None
    for lat_column, lat_series in usable.items():
        lat_score = _series_axis_score(lat_series, "lat", lat_column)
        if lat_score is None:
            continue
        lat_valid = lat_series.dropna()
        lat_median = float(lat_valid.median())
        for lon_column, lon_series in usable.items():
            if lon_column == lat_column:
                continue
            lon_score = _series_axis_score(lon_series, "lon", lon_column)
            if lon_score is None:
                continue
            lon_valid = lon_series.dropna()
            lon_median = float(lon_valid.median())
            joint = (lat_series.between(-90, 90) & lon_series.between(-180, 180)).sum()
            if joint < 2:
                continue
            score = lat_score + lon_score + joint * 0.1
            if lat_median * lon_median < 0:
                score += 1.5
            if abs(lat_median) <= abs(lon_median):
                score += 0.5
            if best is None or score > best[0]:
                best = (score, lat_column, lon_column)

    if best is None:
        return None

    _, lat_column, lon_column = best
    lat_non_origin = (usable[lat_column].abs() > 1e-9) | (usable[lon_column].abs() > 1e-9)
    lat_lon_valid = usable[lat_column].between(-90, 90) & usable[lon_column].between(-180, 180)
    if (lat_non_origin & lat_lon_valid).sum() < 2:
        return None
    lat_span = float((usable[lat_column].dropna().max() - usable[lat_column].dropna().min()))
    lon_span = float((usable[lon_column].dropna().max() - usable[lon_column].dropna().min()))
    # Reject wide-spanning pairs even with weak labels; those are often generic
    # telemetry channels rather than an actual position trace.
    if lat_span > 5.0 or lon_span > 5.0:
        return None
    alt = (
        _find_named_column(numeric_columns, ("alt",))
        or _find_named_column(numeric_columns, ("height",))
        or _find_named_column(numeric_columns, ("gps", "z"))
    )
    return GpsColumns(
        latitude=lat_column,
        longitude=lon_column,
        altitude=alt,
        latitude_label=lat_column,
        longitude_label=lon_column,
        altitude_label=alt,
    )


def detect_gps_columns(df: pd.DataFrame, numeric_columns: list[str]) -> GpsColumns | None:
    # Try a single text column first because some radios export "lat,lon,alt"
    # bundles; fall back to separate columns when that fails.
    gps = _detect_coordinate_string_gps(df, numeric_columns)
    if gps is not None:
        return gps
    return _detect_split_gps_columns(df, numeric_columns)


def load_log(path: str | Path, library_root: str | Path | None = None) -> LoadedLog:
    file_path = Path(path)
    root_path = Path(library_root) if library_root else None
    df = _clean_columns(_read_csv(file_path))
    time = _detect_time(df)
    numeric = _numeric_columns(df, time)
    df = df.copy()
    # GPS detection can inject helper columns, so rerun the numeric pass after
    # the detector has had a chance to normalize coordinate text.
    gps = detect_gps_columns(df, numeric)
    numeric = _numeric_columns(df, time)
    df = df.copy()

    if time is not None and time.notna().any():
        start = time.dropna().iloc[0]
        end = time.dropna().iloc[-1]
        duration = max(0.0, float((end - start).total_seconds()))
    else:
        # When there is no usable timestamp, treat row count as a simple elapsed
        # sample index so the UI still has a deterministic x-axis.
        start = None
        end = None
        duration = float(len(df) - 1) if len(df) else 0.0

    info = LogFileInfo(
        path=file_path,
        model=_model_from_path(file_path, root_path),
        name=file_path.name,
        rows=len(df),
        columns=len(df.columns),
        modified=file_path.stat().st_mtime,
        start_time=start,
        end_time=end,
        duration_seconds=duration,
        has_gps=gps is not None,
    )
    return LoadedLog(info=info, dataframe=df, time=time, numeric_columns=numeric, gps_columns=gps)


def relative_seconds(log: LoadedLog) -> list[float]:
    if log.time is not None and log.time.notna().any():
        # Forward/back fill gaps so the cursor and selection math stay monotonic
        # even when the source log has sparse timestamp holes.
        clean = log.time.ffill().bfill()
        start = clean.iloc[0]
        return [max(0.0, float((value - start).total_seconds())) for value in clean]
    return [float(i) for i in range(len(log.dataframe))]


def nearest_index(log: LoadedLog, x_value: float) -> int:
    xs = relative_seconds(log)
    if not xs:
        return 0
    return min(range(len(xs)), key=lambda idx: abs(xs[idx] - x_value))
