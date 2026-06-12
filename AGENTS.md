# AGENTS.md

Repo-local guidance for `SloppyLogExplorer`.

## Scope

- Treat `SloppyLogExplorer/` as the active implementation repo.
- Keep changes scoped to the user request. Avoid broad refactors and unrelated cleanup.
- Preserve unrelated local edits and do not overwrite user work.

## Working Rules

- When changing behavior, packaging, or commands, check `README.md` and `pyproject.toml` first.
- Prefer root-cause fixes over compatibility shims unless the user asks for compatibility explicitly.
- Prefer efficient, high performance code over compatibility and low-probability fallback chains.
- Keep generated and machine-local files out of Git, including `.venv/`, `build/`, `dist/`, `validation_artifacts/`, `*.log`, `*.zip`, `*.wav`, and `*.egg-info/`.
- If you are working from the parent `EthosLua` workspace, check `git -C SloppyLogExplorer status --short --branch` before editing.

## Validation

- For Python behavior changes, run `python -m pytest`.
- For packaging or build changes, run the relevant build command from `README.md` or `build_windows.bat`.
- For docs-only changes, review the diff and repo status; extra test runs are optional.
