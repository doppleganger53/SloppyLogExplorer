#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ISSUE_KIND_TO_PREFIX = {
    "enhancement": "feature",
    "bug": "fix",
    "docs": "docs",
    "chore": "chore",
}


def run_git(args: list[str]) -> str:
    result = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"git {' '.join(args)} failed{suffix}")
    return result.stdout.strip()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_project_version(pyproject_path: Path | None = None) -> str:
    path = pyproject_path or repo_root() / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "[project]":
            in_project = True
            continue
        if in_project and line.startswith("[") and line.endswith("]"):
            break
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    raise RuntimeError(f"Could not find [project].version in {path}")


def get_current_branch() -> str:
    return run_git(["branch", "--show-current"])


def is_worktree_dirty() -> bool:
    return bool(run_git(["status", "--porcelain"]))


def build_recommended_branch(issue_kind: str, issue_number: str, slug: str) -> str:
    return f"{ISSUE_KIND_TO_PREFIX[issue_kind]}/{issue_number}-{slug}"


def build_release_branch(version: str) -> str:
    return f"release/v{version}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Session branch-policy preflight checks.")
    parser.add_argument("--mode", choices=["issue", "non-issue", "release"], required=True)
    parser.add_argument("--issue-number")
    parser.add_argument("--issue-kind", choices=sorted(ISSUE_KIND_TO_PREFIX))
    parser.add_argument("--slug")
    parser.add_argument("--version", help="Expected release version. Defaults to pyproject.toml.")
    args = parser.parse_args(argv)

    if args.mode == "issue":
        missing = [
            name
            for name, value in (
                ("--issue-number", args.issue_number),
                ("--issue-kind", args.issue_kind),
                ("--slug", args.slug),
            )
            if not value
        ]
        if missing:
            parser.error(f"--mode issue requires {' '.join(missing)}")
        if not re.fullmatch(r"[0-9]+", args.issue_number):
            parser.error("--issue-number must contain digits only.")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug):
            parser.error("--slug must be lowercase kebab-case (a-z, 0-9, hyphen).")
        if args.version:
            parser.error("--version is only valid with --mode release.")
    elif args.mode == "release":
        if args.issue_number or args.issue_kind or args.slug:
            parser.error("--mode release does not accept issue-specific arguments.")
        if args.version and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?", args.version):
            parser.error("--version must look like a semantic version, for example 1.2.3.")
    else:
        if args.issue_number or args.issue_kind or args.slug or args.version:
            parser.error("--mode non-issue does not accept issue or release fields.")
    return args


def run_preflight(args: argparse.Namespace) -> int:
    try:
        branch = get_current_branch()
        dirty = is_worktree_dirty()
    except RuntimeError as exc:
        print(f"Result: ERROR\n{exc}")
        return 1

    if args.mode == "issue":
        recommended = build_recommended_branch(args.issue_kind, args.issue_number, args.slug)
        print("Mode: issue")
        print(f"Current branch: {branch}")
        print(f"Worktree dirty: {'yes' if dirty else 'no'}")
        print(f"Recommended issue branch: {recommended}")
        if branch == "main":
            print("Result: BLOCKED")
            print("Issue-linked work cannot mutate on 'main'.")
            print(f"Create/switch branch: git checkout -b {recommended}")
            print(f"If branch exists: git checkout {recommended}")
            return 2
        if branch != recommended:
            print("Result: PASS_WITH_WARNING")
            print(f"Current branch differs from recommended branch '{recommended}'.")
            return 0
        print("Result: PASS")
        return 0

    if args.mode == "release":
        try:
            project_version = read_project_version()
        except RuntimeError as exc:
            print(f"Result: ERROR\n{exc}")
            return 1
        expected_version = args.version or project_version
        expected_branch = build_release_branch(expected_version)
        print("Mode: release")
        print(f"Current branch: {branch}")
        print(f"Worktree dirty: {'yes' if dirty else 'no'}")
        print(f"Project version: {project_version}")
        print(f"Expected release branch: {expected_branch}")
        if project_version != expected_version:
            print("Result: BLOCKED")
            print(f"Requested version {expected_version} does not match pyproject.toml version {project_version}.")
            return 2
        if branch != expected_branch:
            print("Result: BLOCKED")
            print(f"Release work must run on '{expected_branch}'.")
            print(f"Create/switch branch: git checkout -b {expected_branch}")
            return 2
        print("Result: PASS")
        return 0

    print("Mode: non-issue")
    print(f"Current branch: {branch}")
    print(f"Worktree dirty: {'yes' if dirty else 'no'}")
    print("Result: PASS")
    if branch == "main":
        print("Reminder: Non-issue work on 'main' is allowed, but ask the user before mutating files.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_preflight(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
