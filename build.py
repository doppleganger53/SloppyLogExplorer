#!/usr/bin/env python3
"""PyInstaller build wrapper for the desktop executable."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__


APP_NAME = "SloppyLogExplorer"


def build(one_file: bool = False, clean: bool = False) -> int:
    project_root = Path(__file__).resolve().parent
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    assets_dir = project_root / "sloppy_log_explorer" / "assets"

    if clean:
        # Clean only the local build outputs so a stale spec or previous dist
        # tree does not mask packaging changes during a fresh run.
        shutil.rmtree(dist_dir, ignore_errors=True)
        shutil.rmtree(build_dir, ignore_errors=True)
        spec_file = project_root / f"{APP_NAME}.spec"
        if spec_file.exists():
            spec_file.unlink()

    args = [
        str(project_root / "main.py"),
        f"--name={APP_NAME}",
        "--onefile" if one_file else "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(project_root),
        "--collect-all",
        "plotly",
        # Kaleido and pyttsx3 both need extra data or submodule collection for
        # the packaged app to render charts and synthesize speech reliably.
        "--collect-data",
        "kaleido",
        "--add-data",
        f"{assets_dir}{os.pathsep}sloppy_log_explorer/assets",
        "--collect-submodules",
        "pyttsx3",
        "--hidden-import",
        "PyQt6.QtWebChannel",
        "--hidden-import",
        "PyQt6.QtWebEngineCore",
        "--hidden-import",
        "PyQt6.QtWebEngineWidgets",
        "--hidden-import",
        "PyQt6.QtPrintSupport",
        "--hidden-import",
        "pyttsx3.drivers",
        "--hidden-import",
        "pyttsx3.drivers.sapi5",
    ]

    icon = project_root / "icon.ico"
    if icon.exists():
        args.extend(["--icon", str(icon)])

    print("=" * 72)
    print(f"Building {APP_NAME}")
    print("=" * 72)
    print(f"Mode: {'single executable' if one_file else 'directory'}")
    print(f"Project: {project_root}")
    print(f"Output:  {dist_dir}")
    print()

    try:
        PyInstaller.__main__.run(args)
    except Exception as exc:
        print()
        print("BUILD FAILED")
        print(str(exc))
        return 1

    if one_file:
        exe_path = dist_dir / f"{APP_NAME}.exe"
    else:
        exe_path = dist_dir / APP_NAME / f"{APP_NAME}.exe"

    print()
    print("=" * 72)
    print("BUILD COMPLETE")
    print("=" * 72)
    print(f"Executable: {exe_path}")
    if exe_path.exists():
        print(f"Size:       {exe_path.stat().st_size / (1024 * 1024):.1f} MB")
    print()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Windows desktop executable with PyInstaller.")
    parser.add_argument("--onefile", action="store_true", help="Build one exe instead of a faster-starting dist folder.")
    parser.add_argument("--clean", action="store_true", help="Remove previous build artifacts before building.")
    return parser.parse_args()


if __name__ == "__main__":
    options = parse_args()
    raise SystemExit(build(one_file=options.onefile, clean=options.clean))
