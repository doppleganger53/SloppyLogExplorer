"""Sloppy Log Explorer."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re


def _project_version() -> str:
    # Source checkouts and frozen builds share the canonical project metadata.
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if project_file.is_file():
        project = re.search(
            r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)",
            project_file.read_text(encoding="utf-8"),
        )
        if project:
            match = re.search(r'^version\s*=\s*"([^\"]+)"', project[1], re.MULTILINE)
            if match:
                return match[1]
    try:
        return version("sloppy-log-explorer")
    except PackageNotFoundError:
        return "unknown"


__version__ = _project_version()

