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

Choose checks using the guidance in [AGENTS.md](AGENTS.md#validation).

- Run a selected test with `python -m pytest {path}::{test_name}`, or the full
  suite with `python -m pytest`.
- Check types with
  `npx --yes pyright --pythonpath .\.venv\Scripts\python.exe {touched-files}`.
- Inspect runtime validator inputs with
  `python tools/validate_gps_map_runtime.py --help` or
  `python tools/validate_real_log.py --help`.
- Build commands are in the [README](README.md#build-a-windows-executable).

## Release Flow

- Version source of truth is `[project].version` in `pyproject.toml`.
- Release branches use `release/v{version}`.
- Release notes come from `CHANGELOG.md`.
- Follow the [Windows release procedure](prompts/templates/RELEASE_RESOLUTION_TEMPLATE.md)
  to prepare the verified onefile archive. Its publication section applies
  when external release actions have been authorized.

## Git And Artifact Hygiene

- Keep generated files out of Git, including `.venv/`, `build/`, `dist/`,
  `validation_artifacts/`, logs, WAV files, ZIP files, and generated specs.
- Verify license and attribution before copying external code, docs, or assets.
- Update `NOTICE.md` when attribution changes.
