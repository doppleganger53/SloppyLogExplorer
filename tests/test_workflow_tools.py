from __future__ import annotations

import importlib.util
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest


def load_tool(name: str):
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_tool("session_preflight")
render_issue_prompt = load_tool("render_issue_prompt")
release_notes = load_tool("write_release_notes")
package_release = load_tool("package_release")


def test_preflight_branch_name_mapping() -> None:
    assert preflight.build_recommended_branch("enhancement", "12", "map-playback") == "feature/12-map-playback"
    assert preflight.build_recommended_branch("bug", "13", "bad-log-load") == "fix/13-bad-log-load"
    assert preflight.build_recommended_branch("docs", "14", "workflow-docs") == "docs/14-workflow-docs"
    assert preflight.build_recommended_branch("chore", "15", "release-prep") == "chore/15-release-prep"


def test_issue_mode_blocks_on_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(preflight, "get_current_branch", lambda: "main")
    monkeypatch.setattr(preflight, "is_worktree_dirty", lambda: True)
    args = Namespace(mode="issue", issue_number="12", issue_kind="enhancement", slug="map-playback", version=None)

    result = preflight.run_preflight(args)
    output = capsys.readouterr().out

    assert result == 2
    assert "Result: BLOCKED" in output
    assert "Issue-linked work cannot mutate on 'main'." in output
    assert "git checkout -b feature/12-map-playback" in output


def test_issue_mode_reports_dirty_worktree(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(preflight, "get_current_branch", lambda: "feature/12-map-playback")
    monkeypatch.setattr(preflight, "is_worktree_dirty", lambda: True)
    args = Namespace(mode="issue", issue_number="12", issue_kind="enhancement", slug="map-playback", version=None)

    result = preflight.run_preflight(args)
    output = capsys.readouterr().out

    assert result == 0
    assert "Result: PASS" in output
    assert "Worktree dirty: yes" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "issue"],
        ["--mode", "issue", "--issue-number", "x", "--issue-kind", "bug", "--slug", "bad-log"],
        ["--mode", "issue", "--issue-number", "1", "--issue-kind", "bug", "--slug", "BadLog"],
        ["--mode", "non-issue", "--issue-number", "1"],
        ["--mode", "release", "--issue-number", "1"],
    ],
)
def test_preflight_rejects_invalid_args(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        preflight.parse_args(argv)


def test_release_mode_requires_release_branch_and_matching_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(preflight, "get_current_branch", lambda: "release/v0.1.0")
    monkeypatch.setattr(preflight, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(preflight, "read_project_version", lambda: "0.1.0")
    args = Namespace(mode="release", issue_number=None, issue_kind=None, slug=None, version=None)

    result = preflight.run_preflight(args)
    output = capsys.readouterr().out

    assert result == 0
    assert "Result: PASS" in output
    assert "Project version: 0.1.0" in output


def test_release_mode_blocks_version_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(preflight, "get_current_branch", lambda: "release/v0.2.0")
    monkeypatch.setattr(preflight, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(preflight, "read_project_version", lambda: "0.1.0")
    args = Namespace(mode="release", issue_number=None, issue_kind=None, slug=None, version="0.2.0")

    result = preflight.run_preflight(args)
    output = capsys.readouterr().out

    assert result == 2
    assert "Requested version 0.2.0 does not match pyproject.toml version 0.1.0." in output


def test_render_issue_prompt_uses_issue_metadata(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text(
        "Issue {ISSUE_NUMBER}: {ISSUE_TITLE}\n"
        "Kind {ISSUE_KIND}\n"
        "Branch {TARGET_BRANCH}\n"
        "Labels {ISSUE_LABELS}\n"
        "Touched {TOUCHED_FILES}\n",
        encoding="utf-8",
    )
    issue = {
        "number": 42,
        "title": "[Bug] GPS playback jumps",
        "url": "https://github.com/example/repo/issues/42",
        "state": "OPEN",
        "labels": [{"name": "bug"}],
        "body": "Steps to reproduce.",
    }

    filename, rendered = render_issue_prompt.render_issue_prompt(
        issue,
        slug="gps-playback-jumps",
        snapshot_date="2026-06-19",
        template_path=template,
    )

    assert filename == "ISSUE-42-gps-playback-jumps.md"
    assert "Issue 42: [Bug] GPS playback jumps" in rendered
    assert "Kind bug" in rendered
    assert "Branch fix/42-gps-playback-jumps" in rendered
    assert "Labels bug" in rendered
    assert "Steps to reproduce." in rendered


def test_render_issue_prompt_fetches_with_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '{"number": 7, "title": "Docs update", "url": "u", "state": "OPEN", "labels": [{"name": "documentation"}], "body": "body"}'
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str]) -> str:
        calls.append(args)
        return payload

    monkeypatch.setattr(render_issue_prompt, "run_gh", fake_run_gh)

    issue = render_issue_prompt.fetch_issue("7")

    assert issue["number"] == 7
    assert calls == [["issue", "view", "7", "--json", "number,title,url,state,labels,body"]]


SAMPLE_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Future work.

## [0.1.0] - 2026-06-19

### Added

- Initial release.
"""


def test_extract_release_notes_strips_section_heading() -> None:
    notes = release_notes.extract_release_notes(SAMPLE_CHANGELOG, "v0.1.0")

    assert notes.startswith("[0.1.0] - 2026-06-19\n")
    assert "### Added" in notes
    assert "## [0.1.0]" not in notes


def test_extract_release_notes_raises_when_missing() -> None:
    with pytest.raises(ValueError, match="Release entry not found"):
        release_notes.extract_release_notes(SAMPLE_CHANGELOG, "9.9.9")


def test_package_release_writes_expected_archive(tmp_path: Path) -> None:
    dist_app = tmp_path / "dist" / "SloppyLogExplorer"
    dist_app.mkdir(parents=True)
    exe = dist_app / "SloppyLogExplorer.exe"
    internal = dist_app / "_internal" / "asset.txt"
    internal.parent.mkdir()
    exe.write_text("exe", encoding="utf-8")
    internal.write_text("asset", encoding="utf-8")
    output = tmp_path / "dist" / package_release.default_archive_name("0.1.0")

    result = package_release.package_release(dist_app, output)

    assert result == output
    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == [
            "SloppyLogExplorer/SloppyLogExplorer.exe",
            "SloppyLogExplorer/_internal/asset.txt",
        ]


def test_package_release_writes_onefile_and_release_documents(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    exe = dist / "SloppyLogExplorer.exe"
    exe.write_text("exe", encoding="utf-8")
    docs = tmp_path / "repo"
    docs.mkdir()
    for filename in package_release.RELEASE_DOCUMENTS:
        (docs / filename).write_text(filename, encoding="utf-8")
    output = dist / package_release.default_archive_name("0.1.0")

    result = package_release.package_release(exe, output, documentation_root=docs)

    assert result == output
    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == [
            "CHANGELOG.md",
            "LICENSE",
            "NOTICE.md",
            "README.md",
            "SloppyLogExplorer.exe",
        ]


def test_resolve_dist_source_prefers_onefile(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    onefile = dist / "SloppyLogExplorer.exe"
    onedir = dist / "SloppyLogExplorer"
    onedir.mkdir(parents=True)
    onefile.write_text("exe", encoding="utf-8")

    assert package_release.resolve_dist_source(dist) == onefile


def test_package_release_reads_pyproject_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "1.2.3"\n', encoding="utf-8")

    assert package_release.read_project_version(pyproject) == "1.2.3"
