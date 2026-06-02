from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .models import GpsColumns, LoadedLog, LogFileInfo

LOG_EXTENSIONS = {".csv", ".log"}


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed")]
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.read_csv(path, sep=None, engine="python")


def _detect_time(df: pd.DataFrame) -> pd.Series | None:
    columns = list(df.columns)
    lower = {c: c.lower().strip() for c in columns}
    date_cols = [c for c in columns if "date" in lower[c]]
    time_cols = [c for c in columns if "time" in lower[c]]

    if date_cols and time_cols:
        series = pd.to_datetime(
            df[date_cols[0]].astype(str).str.strip() + " " + df[time_cols[0]].astype(str).str.strip(),
            errors="coerce",
        )
        if series.notna().any():
            return series

    for col in time_cols + date_cols:
        raw = df[col]
        parsed = pd.to_datetime(raw, errors="coerce")
        if parsed.notna().sum() >= max(1, len(df) // 3):
            return parsed
        numeric = pd.to_numeric(raw, errors="coerce")
        if numeric.notna().sum() >= max(1, len(df) // 3):
            base = pd.Timestamp("1970-01-01")
            return base + pd.to_timedelta(numeric.fillna(0), unit="s")

    return None


def _model_from_path(path: Path, library_root: Path | None = None) -> str:
    if library_root:
        try:
            rel = path.relative_to(library_root)
            if len(rel.parts) > 1:
                return rel.parts[0]
        except ValueError:
            pass
    for parent in path.parents:
        name = parent.name
        if name.lower() not in {"logs", "log", "telemetry", "csv", "sd", "models"}:
            return name
    stem = path.stem
    return re.split(r"[-_ ]\d{4}", stem, maxsplit=1)[0] or "Unsorted"


def _numeric_columns(df: pd.DataFrame, time: pd.Series | None) -> list[str]:
    numeric: list[str] = []
    for col in df.columns:
        if time is not None and ("date" in col.lower() or "time" in col.lower()):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any():
            df[col] = converted
            numeric.append(col)
    return numeric


def _find_named_column(columns: list[str], includes: tuple[str, ...], excludes: tuple[str, ...] = ()) -> str | None:
    for col in columns:
        name = col.lower()
        if all(token in name for token in includes) and not any(token in name for token in excludes):
            return col
    return None


def detect_gps_columns(df: pd.DataFrame, numeric_columns: list[str]) -> GpsColumns | None:
    lat = _find_named_column(numeric_columns, ("lat",)) or _find_named_column(numeric_columns, ("gps", "la"))
    lon = (
        _find_named_column(numeric_columns, ("lon",))
        or _find_named_column(numeric_columns, ("lng",))
        or _find_named_column(numeric_columns, ("gps", "lo"))
    )
    if not lat or not lon:
        return None

    lat_values = pd.to_numeric(df[lat], errors="coerce")
    lon_values = pd.to_numeric(df[lon], errors="coerce")
    valid = lat_values.between(-90, 90) & lon_values.between(-180, 180)
    if valid.sum() < 2:
        return None

    alt = (
        _find_named_column(numeric_columns, ("alt",))
        or _find_named_column(numeric_columns, ("height",))
        or _find_named_column(numeric_columns, ("gps", "z"))
    )
    return GpsColumns(latitude=lat, longitude=lon, altitude=alt)


def load_log(path: str | Path, library_root: str | Path | None = None) -> LoadedLog:
    file_path = Path(path)
    root_path = Path(library_root) if library_root else None
    df = _clean_columns(_read_csv(file_path))
    time = _detect_time(df)
    numeric = _numeric_columns(df, time)
    gps = detect_gps_columns(df, numeric)

    if time is not None and time.notna().any():
        start = time.dropna().iloc[0]
        end = time.dropna().iloc[-1]
        duration = max(0.0, float((end - start).total_seconds()))
    else:
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
        clean = log.time.ffill().bfill()
        start = clean.iloc[0]
        return [max(0.0, float((value - start).total_seconds())) for value in clean]
    return [float(i) for i in range(len(log.dataframe))]


def nearest_index(log: LoadedLog, x_value: float) -> int:
    xs = relative_seconds(log)
    if not xs:
        return 0
    return min(range(len(xs)), key=lambda idx: abs(xs[idx] - x_value))
