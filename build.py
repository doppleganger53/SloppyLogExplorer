#!/usr/bin/env python3
"""PyInstaller build wrapper for the desktop executable."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import textwrap
from pathlib import Path
from typing import Literal


APP_NAME = "SloppyLogExplorer"
BUILD_TARGETS = ("minimal", "debug")
BuildTarget = Literal["minimal", "debug"]

QT_WEBENGINE_HIDDEN_IMPORTS = (
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtPrintSupport",
)
PYTTSX3_HIDDEN_IMPORTS = (
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
)
MINIMAL_PLOTLY_HIDDEN_IMPORTS = (
    "plotly.graph_objects",
    "plotly.offline",
    "plotly.io",
    "plotly.io._templates",
)
MINIMAL_EXCLUDED_MODULES = (
    "IPython",
    "ipywidgets",
    "jupyterlab",
    "kaleido",
    "matplotlib",
    "notebook",
    "plotly.io.kaleido",
    "plotly.matplotlylib",
)
MINIMAL_PRUNE_FILENAMES = frozenset(
    {
        "qtwebengine_devtools_resources.debug.pak",
        "qtwebengine_devtools_resources.pak",
    }
)
MINIMAL_SPEC_FILENAMES = {
    False: f"{APP_NAME}-minimal-onedir.spec",
    True: f"{APP_NAME}-minimal-onefile.spec",
}


def _flag_values(flag: str, values: tuple[str, ...]) -> list[str]:
    args: list[str] = []
    for value in values:
        args.extend([flag, value])
    return args


def _add_data_arg(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def _add_data_spec(source: Path, destination: str) -> tuple[str, str]:
    return (str(source), destination)


def _package_file(package: str, relative_path: Path) -> Path:
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"Package is not importable: {package}")
    package_root = Path(spec.origin).resolve().parent
    path = package_root / relative_path
    if not path.exists():
        raise RuntimeError(f"Package data file not found: {path}")
    return path


def _base_pyinstaller_args(project_root: Path, one_file: bool) -> list[str]:
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    assets_dir = project_root / "sloppy_log_explorer" / "assets"

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
        "--add-data",
        _add_data_arg(assets_dir, "sloppy_log_explorer/assets"),
    ]

    icon = project_root / "icon.ico"
    if icon.exists():
        args.extend(["--icon", str(icon)])
    return args


def _qt_webengine_args() -> list[str]:
    return _flag_values("--hidden-import", QT_WEBENGINE_HIDDEN_IMPORTS)


def _voice_args() -> list[str]:
    return [
        "--collect-submodules",
        "pyttsx3",
        *_flag_values("--hidden-import", PYTTSX3_HIDDEN_IMPORTS),
    ]


def _minimal_plotly_args() -> list[str]:
    plotly_js = _package_file("plotly", Path("package_data") / "plotly.min.js")
    return [
        "--add-data",
        _add_data_arg(plotly_js, "plotly/package_data"),
        *_flag_values("--hidden-import", MINIMAL_PLOTLY_HIDDEN_IMPORTS),
        *_flag_values("--exclude-module", MINIMAL_EXCLUDED_MODULES),
    ]


def _minimal_spec_path(project_root: Path, one_file: bool) -> Path:
    return project_root / MINIMAL_SPEC_FILENAMES[one_file]


def _spec_build_args(project_root: Path, spec_path: Path) -> list[str]:
    return [
        "--noconfirm",
        "--clean",
        "--distpath",
        str(project_root / "dist"),
        "--workpath",
        str(project_root / "build"),
        str(spec_path),
    ]


def _repr_list(values: object) -> str:
    return repr(values)


def _minimal_spec_text(project_root: Path, one_file: bool) -> str:
    assets_dir = project_root / "sloppy_log_explorer" / "assets"
    plotly_js = _package_file("plotly", Path("package_data") / "plotly.min.js")
    datas = [
        _add_data_spec(assets_dir, "sloppy_log_explorer/assets"),
        _add_data_spec(plotly_js, "plotly/package_data"),
    ]
    hidden_imports = [
        *MINIMAL_PLOTLY_HIDDEN_IMPORTS,
        *PYTTSX3_HIDDEN_IMPORTS,
        *QT_WEBENGINE_HIDDEN_IMPORTS,
    ]
    icon = project_root / "icon.ico"
    icon_arg = f"icon={[str(icon)]!r}," if icon.exists() else ""

    exe_inputs = "a.binaries,\n    a.datas,\n    []," if one_file else "[],\n    exclude_binaries=True,"
    collect = ""
    if not one_file:
        collect = f"""
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name={APP_NAME!r},
)
"""

    spec = f"""
# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = {_repr_list(hidden_imports)}
hiddenimports += collect_submodules('pyttsx3')

MINIMAL_DEVTOOLS_RESOURCE_FILENAMES = {sorted(MINIMAL_PRUNE_FILENAMES)!r}


def _is_minimal_devtools_resource(entry):
    for value in entry[:2]:
        if os.path.basename(str(value)).lower() in MINIMAL_DEVTOOLS_RESOURCE_FILENAMES:
            return True
    return False


def _without_minimal_devtools(entries):
    return type(entries)(entry for entry in entries if not _is_minimal_devtools_resource(entry))


a = Analysis(
    [{str(project_root / "main.py")!r}],
    pathex=[{str(project_root)!r}],
    binaries=[],
    datas={_repr_list(datas)},
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={_repr_list(list(MINIMAL_EXCLUDED_MODULES))},
    noarchive=False,
    optimize=0,
)
a.datas = _without_minimal_devtools(a.datas)
a.binaries = _without_minimal_devtools(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    {exe_inputs}
    name={APP_NAME!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    {"runtime_tmpdir=None," if one_file else ""}
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    {icon_arg}
)
{collect}
"""
    return textwrap.dedent(spec).lstrip()


def _write_minimal_spec(project_root: Path, one_file: bool) -> Path:
    spec_path = _minimal_spec_path(project_root, one_file)
    spec_path.write_text(_minimal_spec_text(project_root, one_file), encoding="utf-8")
    return spec_path


def _debug_plotly_args() -> list[str]:
    # Keep the broad Plotly collection available for packaging diagnostics.
    # It can surface optional-import warnings such as plotly.matplotlylib when
    # matplotlib is absent; the minimal target avoids that path.
    return ["--collect-all", "plotly"]


def _target_pyinstaller_args(target: BuildTarget) -> list[str]:
    if target == "minimal":
        return [
            *_minimal_plotly_args(),
            *_voice_args(),
            *_qt_webengine_args(),
        ]
    if target == "debug":
        return [
            *_debug_plotly_args(),
            *_voice_args(),
            *_qt_webengine_args(),
        ]
    raise ValueError(f"Unsupported build target: {target}")


def build_pyinstaller_args(project_root: Path, one_file: bool = False, target: BuildTarget = "minimal") -> list[str]:
    if target == "minimal":
        spec_path = _write_minimal_spec(project_root, one_file)
        return _spec_build_args(project_root, spec_path)
    return [
        *_base_pyinstaller_args(project_root, one_file),
        *_target_pyinstaller_args(target),
    ]


def _clean_build_outputs(project_root: Path) -> None:
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    spec_file = project_root / f"{APP_NAME}.spec"

    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)
    if spec_file.exists():
        spec_file.unlink()
    for filename in MINIMAL_SPEC_FILENAMES.values():
        generated_spec = project_root / filename
        if generated_spec.exists():
            generated_spec.unlink()


def _prune_minimal_onedir_dist(app_dir: Path) -> list[Path]:
    if not app_dir.exists():
        return []
    removed: list[Path] = []
    for path in app_dir.rglob("*"):
        if path.is_file() and path.name in MINIMAL_PRUNE_FILENAMES:
            path.unlink()
            removed.append(path)
    return removed


def _exe_path(project_root: Path, one_file: bool) -> Path:
    dist_dir = project_root / "dist"
    if one_file:
        return dist_dir / f"{APP_NAME}.exe"
    return dist_dir / APP_NAME / f"{APP_NAME}.exe"


def build(one_file: bool = False, clean: bool = False, target: BuildTarget = "minimal") -> int:
    project_root = Path(__file__).resolve().parent
    dist_dir = project_root / "dist"

    if clean:
        # Clean only the local build outputs so a stale spec or previous dist
        # tree does not mask packaging changes during a fresh run.
        _clean_build_outputs(project_root)

    args = build_pyinstaller_args(project_root, one_file=one_file, target=target)

    print("=" * 72)
    print(f"Building {APP_NAME}")
    print("=" * 72)
    print(f"Target: {target}")
    print(f"Mode: {'single executable' if one_file else 'directory'}")
    print(f"Project: {project_root}")
    print(f"Output:  {dist_dir}")
    print()

    try:
        import PyInstaller.__main__

        PyInstaller.__main__.run(args)
    except Exception as exc:
        print()
        print("BUILD FAILED")
        print(str(exc))
        return 1

    pruned: list[Path] = []
    if target == "minimal" and not one_file:
        pruned = _prune_minimal_onedir_dist(dist_dir / APP_NAME)

    exe_path = _exe_path(project_root, one_file)

    print()
    print("=" * 72)
    print("BUILD COMPLETE")
    print("=" * 72)
    print(f"Executable: {exe_path}")
    if exe_path.exists():
        print(f"Size:       {exe_path.stat().st_size / (1024 * 1024):.1f} MB")
    if pruned:
        print(f"Pruned:     {len(pruned)} minimal-target devtools resource file(s)")
    print()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Windows desktop executable with PyInstaller.")
    parser.add_argument(
        "--target",
        choices=BUILD_TARGETS,
        default="minimal",
        help="Build profile: minimal end-user package or debug diagnostics package.",
    )
    parser.add_argument("--onefile", action="store_true", help="Build one exe instead of a faster-starting dist folder.")
    parser.add_argument("--clean", action="store_true", help="Remove previous build artifacts before building.")
    return parser.parse_args()


if __name__ == "__main__":
    options = parse_args()
    raise SystemExit(build(one_file=options.onefile, clean=options.clean, target=options.target))
