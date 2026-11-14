#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable


APP_NAME = "SloppyLogExplorer"
RELEASE_DOCUMENTS = ("README.md", "NOTICE.md", "LICENSE", "CHANGELOG.md")
ONEFILE_NAME = f"{APP_NAME}.exe"


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


def default_archive_name(version: str) -> str:
    return f"{APP_NAME}-{version}-windows-x64.zip"


def _archive_entries(package_source: Path, documentation_root: Path | None) -> list[tuple[Path, str]]:
    if package_source.is_file():
        entries = [(package_source, package_source.name)]
    elif package_source.is_dir():
        entries = [
            (path, str(path.relative_to(package_source.parent)))
            for path in sorted(package_source.rglob("*"))
            if path.is_file()
        ]
    else:
        raise FileNotFoundError(f"Packaged app source not found: {package_source}")

    if documentation_root is not None:
        for filename in RELEASE_DOCUMENTS:
            document = documentation_root / filename
            if not document.is_file():
                raise FileNotFoundError(f"Release document not found: {document}")
            entries.append((document, filename))
    return entries


def package_release(
    package_source: Path,
    output_path: Path,
    documentation_root: Path | None = None,
    *,
    verify: Callable[[Path], None] | None = None,
) -> Path:
    # Validate before touching an existing archive or creating output inside a
    # directory that is about to be archived.
    entries = _archive_entries(package_source, documentation_root)
    resolved_output = output_path.resolve()
    if package_source.is_dir() and resolved_output.is_relative_to(package_source.resolve()):
        raise ValueError("Release archive must be outside the packaged app directory")
    if any(path.resolve() == resolved_output for path, _ in entries):
        raise ValueError("Release archive must not overwrite a packaged input file")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, archive_name in entries:
                archive.write(path, archive_name)
        if verify is not None:
            verify(temporary_path)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not verify release checkout")
    return result.stdout.strip()


def release_output(root: Path, output: Path) -> Path:
    # Use the same canonical destination for checks and replacement. In
    # particular, do not replace a tracked symlink even if its target is safe.
    path = output.resolve()
    if output.is_symlink():
        raise ValueError("Release output cannot replace a symbolic link")
    if path.suffix.lower() != ".zip":
        raise ValueError("Public release output must be a .zip file")
    if os.name == "nt":
        if path.is_reserved() or any(":" in part or part.endswith((" ", ".")) for part in path.parts[1:]):
            raise ValueError("Ambiguous Windows release output path")
    metadata = [
        Path(directory).resolve() for directory in
        git(root, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir").splitlines()
    ]
    if any(part.casefold() == ".git" for part in (*output.parts, *path.parts)) or any(
        path.is_relative_to(directory) for directory in metadata
    ):
        raise ValueError("Release output cannot be inside Git metadata")
    # Probe from an existing parent to recognize sibling and nested worktrees,
    # even when the requested archive's own directory has not been created.
    parent = next((directory for directory in path.parents if directory.is_dir()), None)
    if parent is None:
        raise ValueError("Release output needs an accessible parent directory")
    owner = subprocess.run(
        ["git", "-C", str(parent), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True
    )
    if path.is_relative_to(root):
        if owner.returncode or Path(owner.stdout.strip()).resolve() != metadata[0]:
            raise ValueError("Release output belongs to a different checkout")
        relative = path.relative_to(root).as_posix()
        if git(root, "ls-files", "--", f":(literal,icase){relative}"):
            raise ValueError("Release output cannot replace a tracked path")
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "--quiet", "--", relative])
        if ignored.returncode != 0:
            raise ValueError("Use a Git-ignored release ZIP path or an external staging directory")
    elif owner.returncode == 0:
        raise ValueError("External release staging must be outside other Git repositories")
    return path


def verify_source(root: Path, source_commit: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", source_commit):
        raise ValueError("Supply the full source commit recorded before the clean onefile build")

    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Release source has uncommitted changes")
    tested_tree, current_tree = git(
        root, "rev-parse", f"{source_commit}^{{commit}}^{{tree}}", "HEAD^{tree}"
    ).splitlines()
    if current_tree != tested_tree:
        raise ValueError("Release source tree differs from the recorded build; rebuild and revalidate")
    return tested_tree


def verify_onefile_zip(archive_path: Path, tested_exe_sha256: str, documentation_root: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        if sorted(archive.namelist()) != sorted((ONEFILE_NAME, *RELEASE_DOCUMENTS)):
            raise ValueError("ZIP must contain the named onefile executable and all release documents")
        digest = hashlib.sha256()
        with archive.open(ONEFILE_NAME) as member:
            for chunk in iter(lambda: member.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != tested_exe_sha256.lower():
            raise ValueError("Archived executable SHA-256 differs from the tested executable")
        for filename in RELEASE_DOCUMENTS:
            if archive.read(filename) != (documentation_root / filename).read_bytes():
                raise ValueError(f"Archived release document differs from its source: {filename}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package the verified Windows onefile release, or an explicit local build."
    )
    parser.add_argument("--version", help="Release version. Defaults to pyproject.toml.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        help="Package an explicit local file or onedir directory without public release verification.",
    )
    parser.add_argument("--source-commit", help="Full Git commit ID recorded before building the tested onefile.")
    parser.add_argument("--tested-exe-sha256", help="SHA-256 recorded for the onefile that passed packaged tests.")
    parser.add_argument("--output", type=Path, help="Override the archive path. Public output requires an untracked .zip file.")
    args = parser.parse_args(argv)
    if args.dist_dir is not None:
        if args.source_commit or args.tested_exe_sha256:
            parser.error("--dist-dir is for local packaging and cannot take release evidence")
    elif not args.source_commit or not re.fullmatch(r"[0-9a-fA-F]{64}", args.tested_exe_sha256 or ""):
        parser.error("onefile release requires --source-commit and a 64-digit --tested-exe-sha256")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = repo_root()
        dist = root / "dist"
        project_version = read_project_version()
        version = args.version or project_version
        if args.dist_dir is not None:
            output = args.output or dist / f"{APP_NAME}-{version}-local.zip"
            archive = package_release(args.dist_dir, output, documentation_root=root)
            print(f"Wrote local archive: {archive}")
            return 0
        if version != project_version:
            raise ValueError("Requested release version differs from pyproject.toml")
        package_source = dist / ONEFILE_NAME
        if not package_source.is_file():
            raise FileNotFoundError(f"Required onefile executable missing: {package_source}")
        tree = verify_source(root, args.source_commit)
        output = release_output(root, args.output or dist / default_archive_name(version))

        def verify(archive_path: Path) -> None:
            verify_onefile_zip(archive_path, args.tested_exe_sha256, root)
            verify_source(root, args.source_commit)
            if release_output(root, output) != output:
                raise ValueError("Release output moved during packaging")

        archive = package_release(package_source, output, documentation_root=root, verify=verify)
    except (RuntimeError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote release archive: {archive}\nBuild commit: {args.source_commit}\nSource tree: {tree}")
    print(f"Archived EXE SHA-256: {args.tested_exe_sha256.lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
