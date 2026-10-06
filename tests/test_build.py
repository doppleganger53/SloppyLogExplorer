from __future__ import annotations

import importlib.util
import builtins
import os
import sys
import types
from pathlib import Path

import pytest


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


@pytest.mark.parametrize("target", ["minimal", "debug"])
@pytest.mark.parametrize("one_file", [False, True])
def test_all_windows_targets_embed_the_packaged_app_icon(tmp_path: Path, target: str, one_file: bool) -> None:
    args = build_module.build_pyinstaller_args(tmp_path, one_file=one_file, target=target)
    assets = tmp_path / "sloppy_log_explorer" / "assets"
    icon = assets / "app-icon.ico"
    if target == "minimal":
        spec = build_module._minimal_spec_path(tmp_path, one_file).read_text(encoding="utf-8")
        assert f"icon={[str(icon)]!r}," in spec
        assert repr((str(assets), "sloppy_log_explorer/assets")) in spec
    else:
        assert option_values(args, "--icon") == [str(icon)]
        assert build_module._add_data_arg(assets, "sloppy_log_explorer/assets") in option_values(args, "--add-data")


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


@pytest.mark.parametrize("existing_path", ["unrelated-poppler;unrelated-libheif", None])
def test_windows_build_path_has_only_environment_and_system_directories_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_path: str | None,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setattr(build_module, "_package_file", lambda *_: tmp_path / "PyQt6" / "Qt6" / "bin")
    if existing_path is None:
        monkeypatch.delenv("PATH", raising=False)
    else:
        monkeypatch.setenv("PATH", existing_path)
    with pytest.raises(RuntimeError, match="simulated failure"):
        with build_module._isolated_windows_build_path():
            assert os.environ["PATH"].split(os.pathsep) == [
                str(tmp_path / "venv" / "Scripts"),
                str(tmp_path / "python"),
                str(tmp_path / "python" / "DLLs"),
                str(tmp_path / "PyQt6" / "Qt6" / "bin"),
                str(tmp_path / "Windows" / "System32"),
                str(tmp_path / "Windows"),
            ]
            raise RuntimeError("simulated failure")
    assert os.environ.get("PATH") == existing_path


def test_non_windows_build_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setattr(build_module, "_package_file", lambda *_: pytest.fail("Qt directory discovery on non-Windows"))
    with build_module._isolated_windows_build_path():
        assert os.environ["PATH"] == "existing-path"
    assert os.environ["PATH"] == "existing-path"


@pytest.mark.parametrize("fail", [False, True])
def test_build_isolates_path_before_pyinstaller_import_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "unrelated-native-tool")
    monkeypatch.setattr(build_module, "__file__", str(tmp_path / "build.py"))
    monkeypatch.setattr(build_module, "_package_file", lambda *_: tmp_path / "Qt6" / "bin")
    monkeypatch.setattr(build_module, "build_pyinstaller_args", lambda *_args, **_kwargs: ["test.spec"])
    observed: list[str] = []

    def run(args: list[str]) -> None:
        observed.append("run")
        assert args == ["test.spec"]
        assert "unrelated-native-tool" not in os.environ["PATH"]
        if fail:
            raise OSError("simulated PyInstaller failure")

    package = types.ModuleType("PyInstaller")
    main = types.ModuleType("PyInstaller.__main__")
    main.run = run  # type: ignore[attr-defined]
    package.__main__ = main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "PyInstaller", package)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", main)
    original_import = builtins.__import__

    def checked_import(name: str, *args, **kwargs):
        if name == "PyInstaller.__main__":
            observed.append("import")
            assert "unrelated-native-tool" not in os.environ["PATH"]
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    assert build_module.build() == (1 if fail else 0)
    assert observed == ["import", "run"]
    assert os.environ["PATH"] == "unrelated-native-tool"
