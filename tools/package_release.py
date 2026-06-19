#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path


APP_NAME = "SloppyLogExplorer"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_project_version(pyproject_path: Path | None = None) -> str:
    path = pyproject_path or repo_root() / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "[project]":
            in_project = True
            continue
        if in_project and line.startswith("[") and line.endswith("]"):
            break
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    raise RuntimeError(f"Could not find [project].version in {path}")


def default_archive_name(version: str) -> str:
    return f"{APP_NAME}-{version}-windows-x64.zip"


def package_release(dist_app_dir: Path, output_path: Path) -> Path:
    if not dist_app_dir.is_dir():
        raise FileNotFoundError(f"Packaged app directory not found: {dist_app_dir}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(dist_app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(dist_app_dir.parent))
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Windows release ZIP from dist/SloppyLogExplorer.")
    parser.add_argument("--version", help="Release version. Defaults to pyproject.toml.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist") / APP_NAME)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        version = args.version or read_project_version()
        output = args.output or Path("dist") / default_archive_name(version)
        archive = package_release(args.dist_dir, output)
    except (RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote release archive: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
