# Contributing

## Workflow

1. Start from the latest `main`.
2. For issue-linked work, create a short-lived branch:
   - `feature/{issue-number}-{short-slug}` for enhancements
   - `fix/{issue-number}-{short-slug}` for bugs
   - `docs/{issue-number}-{short-slug}` for docs/process changes
   - `chore/{issue-number}-{short-slug}` for maintenance/tooling
3. Run issue preflight before editing:
   `python tools/session_preflight.py --mode issue --issue-number {N} --issue-kind {kind} --slug {short-slug}`
4. Keep changes focused on one concern.
5. Open a PR into `main` and include linked-closing keywords such as
   `Closes #123`.
6. Do not manually close linked issues before merge.

## Creating Issues

- Use the GitHub issue templates under `.github/ISSUE_TEMPLATE/`.
- Include acceptance criteria and validation notes.
- For bugs, include environment details and sanitized logs or screenshots.
- To create a local implementation prompt for an existing issue, run:
  `python tools/render_issue_prompt.py --issue-number {N} --issue-kind {kind}`

## Validation

Run the checks that match touched files:

- Python behavior: `python -m pytest`
- Typing-sensitive edits:
  `npx --yes pyright --pythonpath .\.venv\Scripts\python.exe {touched-files}`
- GPS/WebEngine behavior:
  `python tools\validate_gps_map_runtime.py ...`
- Real-log validation:
  `python tools\validate_real_log.py ...`
- Packaging/build changes:
  `.\build_windows.bat` or the documented build command.

## Release Flow

- Version source of truth is `[project].version` in `pyproject.toml`.
- Release branches use `release/v{version}`.
- Release notes come from `CHANGELOG.md`.
- Build Windows output, then archive it with:
  `python tools/package_release.py`
- Publish release notes with `gh release create --notes-file`.

## Git And Artifact Hygiene

- Keep generated files out of Git, including `.venv/`, `build/`, `dist/`,
  `validation_artifacts/`, logs, WAV files, ZIP files, and generated specs.
- Verify license and attribution before copying external code, docs, or assets.
- Update `NOTICE.md` when attribution changes.
