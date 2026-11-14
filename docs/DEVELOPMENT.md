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
- Build the Windows release executable:
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

Update `pyproject.toml` and `CHANGELOG.md` on `release/v{version}`.
Follow the [Windows release procedure](../prompts/templates/RELEASE_RESOLUTION_TEMPLATE.md)
for local preparation, including recording the clean source, exercising the
packaged executable, and verifying the ZIP. It also covers readback after
authorized publication.

For a local onedir archive, pass the directory explicitly:
`python tools/package_release.py --dist-dir dist/SloppyLogExplorer`.

## Versioning

- `[project].version` in `pyproject.toml` is the only release version source.
- Use semantic versioning.
- Do not add a separate `VERSION` file.

## Generated Files

The following are local outputs and should remain ignored:

- `.venv/`, `build/`, `dist/`, `validation_artifacts/`
- `*.spec`, `*.log`, `*.zip`, `*.wav`, `*.egg-info/`
