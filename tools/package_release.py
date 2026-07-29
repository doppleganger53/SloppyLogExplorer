#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path


APP_NAME = "SloppyLogExplorer"
RELEASE_DOCUMENTS = ("README.md", "NOTICE.md", "LICENSE", "CHANGELOG.md")


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


def _archive_entries(package_source: Path, documentation_root: Path | None) -> list[tuple[Path, str]]:
    if package_source.is_file():
        entries = [(package_source, package_source.name)]
    elif package_source.is_dir():
        entries = [
            (path, str(path.relative_to(package_source.parent)))
            for path in sorted(package_source.rglob("*"))
            if path.is_file()
        ]
    else:
        raise FileNotFoundError(f"Packaged app source not found: {package_source}")

    if documentation_root is not None:
        for filename in RELEASE_DOCUMENTS:
            document = documentation_root / filename
            if not document.is_file():
                raise FileNotFoundError(f"Release document not found: {document}")
            entries.append((document, filename))
    return entries


def package_release(
    package_source: Path,
    output_path: Path,
    documentation_root: Path | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in _archive_entries(package_source, documentation_root):
            archive.write(path, archive_name)
    return output_path


def resolve_dist_source(dist_root: Path = Path("dist")) -> Path:
    onefile = dist_root / f"{APP_NAME}.exe"
    if onefile.is_file():
        return onefile
    onedir = dist_root / APP_NAME
    if onedir.is_dir():
        return onedir
    raise FileNotFoundError(f"No {APP_NAME} onefile executable or onedir package found under {dist_root}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Windows release ZIP from the minimal onefile or onedir build."
    )
    parser.add_argument("--version", help="Release version. Defaults to pyproject.toml.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        help="Packaged app file or directory. Defaults to dist/SloppyLogExplorer.exe, then dist/SloppyLogExplorer.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        version = args.version or read_project_version()
        output = args.output or Path("dist") / default_archive_name(version)
        package_source = args.dist_dir or resolve_dist_source()
        archive = package_release(package_source, output, documentation_root=repo_root())
    except (RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote release archive: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
