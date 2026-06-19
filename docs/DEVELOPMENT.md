# Development Notes

## Repository Boundary

`SloppyLogExplorer/` is the implementation repository. The parent `EthosLua`
workspace is orchestration-only, and sibling repositories are read-only
references unless explicitly targeted.

## Core Commands

- Install for development:
  `python -m pip install -e .[dev]`
- Run tests:
  `python -m pytest`
- Run the app from source:
  `python main.py`
- Source smoke test:
  `python main.py --smoke-test`
- Validate a log from the package entry point:
  `python -m sloppy_log_explorer --validate-log "{LOG_PATH}"`
- Build Windows onedir executable:
  `.\build_windows.bat`

## GitHub Issue Workflow

- Issue-linked work must use a short-lived branch and must not mutate files on
  `main`.
- Run:
  `python tools/session_preflight.py --mode issue --issue-number {N} --issue-kind {kind} --slug {short-slug}`
- Use `tools/render_issue_prompt.py` to turn a GitHub issue into a local
  implementation prompt under `prompts/issues/`.
- PRs should target `main` and include `Closes #{N}`.

## Release Workflow

1. Update `pyproject.toml` and `CHANGELOG.md` on a release branch named
   `release/v{version}`.
2. Run:
   `python tools/session_preflight.py --mode release`
3. Run:
   `python -m pytest`
4. Build:
   `.\build_windows.bat`
5. Validate packaged executable when feasible:
   - `.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --smoke-test`
   - `.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --validate-log "{LOG_PATH}"`
6. Generate release notes:
   `python tools\write_release_notes.py --version {version} --output validation_artifacts\release-notes-{version}.md`
7. Create release archive:
   `python tools\package_release.py --version {version}`
8. Publish:
   `gh release create v{version} dist/SloppyLogExplorer-{version}-windows-x64.zip --title "Sloppy Log Explorer v{version}" --notes-file validation_artifacts\release-notes-{version}.md`

## Versioning

- `[project].version` in `pyproject.toml` is the only release version source.
- Use semantic versioning.
- Do not add a separate `VERSION` file.

## Generated Files

The following are local outputs and should remain ignored:

- `.venv/`, `build/`, `dist/`, `validation_artifacts/`
- `*.spec`, `*.log`, `*.zip`, `*.wav`, `*.egg-info/`
