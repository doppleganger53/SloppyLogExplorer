from __future__ import annotations

import importlib.util
from pathlib import Path


def load_build_module():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "build.py"
    spec = importlib.util.spec_from_file_location("build_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_module = load_build_module()


def option_values(args: list[str], option: str) -> list[str]:
    return [args[index + 1] for index, value in enumerate(args[:-1]) if value == option]


def test_minimal_target_uses_narrow_plotly_contract(tmp_path: Path) -> None:
    args = build_module.build_pyinstaller_args(tmp_path, target="minimal")
    spec_path = tmp_path / "SloppyLogExplorer-minimal-onedir.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    assert str(spec_path) in args
    assert "--collect-all" not in args
    assert "--collect-data" not in args
    assert "plotly/package_data" in spec_text
    assert "plotly.graph_objects" in spec_text
    assert "plotly.offline" in spec_text
    assert "plotly.matplotlylib" in spec_text
    assert "plotly.io.kaleido" in spec_text
    assert "kaleido" in spec_text
    assert "collect_submodules('pyttsx3')" in spec_text
    assert "PyQt6.QtWebEngineWidgets" in spec_text


def test_debug_target_keeps_broad_plotly_collection(tmp_path: Path) -> None:
    args = build_module.build_pyinstaller_args(tmp_path, target="debug")

    assert option_values(args, "--collect-all") == ["plotly"]
    assert option_values(args, "--exclude-module") == []
    assert "pyttsx3" in option_values(args, "--collect-submodules")
    assert "PyQt6.QtWebChannel" in option_values(args, "--hidden-import")


def test_onefile_mode_keeps_target_specific_args(tmp_path: Path) -> None:
    args = build_module.build_pyinstaller_args(tmp_path, one_file=True, target="minimal")
    spec_path = tmp_path / "SloppyLogExplorer-minimal-onefile.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    assert str(spec_path) in args
    assert "--onefile" not in args
    assert "--onedir" not in args
    assert "plotly.matplotlylib" in spec_text
    assert "a.binaries," in spec_text
    assert "a.datas," in spec_text
    assert "COLLECT(" not in spec_text


def test_minimal_onefile_spec_excludes_webengine_devtools_before_packaging(tmp_path: Path) -> None:
    build_module.build_pyinstaller_args(tmp_path, one_file=True, target="minimal")
    spec_path = tmp_path / "SloppyLogExplorer-minimal-onefile.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    assert "MINIMAL_DEVTOOLS_RESOURCE_FILENAMES" in spec_text
    assert "qtwebengine_devtools_resources.debug.pak" in spec_text
    assert "qtwebengine_devtools_resources.pak" in spec_text
    assert "a.datas = _without_minimal_devtools(a.datas)" in spec_text
    assert "a.binaries = _without_minimal_devtools(a.binaries)" in spec_text


def test_minimal_prune_removes_webengine_devtools_resources(tmp_path: Path) -> None:
    resources = tmp_path / "_internal" / "PyQt6" / "Qt6" / "resources"
    resources.mkdir(parents=True)
    debug_pak = resources / "qtwebengine_devtools_resources.debug.pak"
    devtools_pak = resources / "qtwebengine_devtools_resources.pak"
    runtime_pak = resources / "qtwebengine_resources.pak"
    debug_pak.write_text("debug", encoding="utf-8")
    devtools_pak.write_text("devtools", encoding="utf-8")
    runtime_pak.write_text("runtime", encoding="utf-8")

    removed = build_module._prune_minimal_onedir_dist(tmp_path)

    assert sorted(path.name for path in removed) == [
        "qtwebengine_devtools_resources.debug.pak",
        "qtwebengine_devtools_resources.pak",
    ]
    assert not debug_pak.exists()
    assert not devtools_pak.exists()
    assert runtime_pak.exists()
