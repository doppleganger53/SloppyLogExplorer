#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def normalize_version(raw_version: str) -> str:
    version = raw_version.strip()
    if version.startswith("v") and len(version) > 1 and version[1].isdigit():
        return version[1:]
    return version


def extract_release_notes(changelog_text: str, version: str, keep_heading: bool = False) -> str:
    target = normalize_version(version)
    lines = changelog_text.splitlines()
    start_index: int | None = None
    end_index = len(lines)
    for index, line in enumerate(lines):
        if line.startswith("## ["):
            if start_index is None:
                if line.startswith(f"## [{target}] - "):
                    start_index = index
            else:
                end_index = index
                break
    if start_index is None:
        raise ValueError(f"Release entry not found in CHANGELOG.md for '{target}'.")
    section_lines = lines[start_index:end_index]
    if not keep_heading:
        section_lines[0] = section_lines[0][3:]
    return "\n".join(section_lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract one CHANGELOG.md release entry for gh release --notes-file.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--output", required=True)
    parser.add_argument("--keep-heading", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    changelog_path = Path(args.changelog)
    output_path = Path(args.output)
    if not changelog_path.exists():
        raise SystemExit(f"Changelog file not found: {changelog_path}")
    try:
        notes = extract_release_notes(
            changelog_path.read_text(encoding="utf-8"),
            version=args.version,
            keep_heading=args.keep_heading,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(notes, encoding="utf-8")
    print(f"Wrote release notes: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
