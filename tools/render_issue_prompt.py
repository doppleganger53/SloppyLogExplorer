#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


KIND_TO_BRANCH_PREFIX = {
    "enhancement": "feature",
    "bug": "fix",
    "docs": "docs",
    "chore": "chore",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"gh {' '.join(args)} failed{suffix}")
    return result.stdout.strip()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "issue"


def label_names(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for item in labels:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str):
            names.append(item)
    return names


def infer_issue_kind(labels: list[str], title: str) -> str:
    normalized = {label.lower() for label in labels}
    if "bug" in normalized:
        return "bug"
    if "documentation" in normalized or "docs" in normalized or title.lower().startswith("[docs]"):
        return "docs"
    if "chore" in normalized or title.lower().startswith("[chore]"):
        return "chore"
    return "enhancement"


def fetch_issue(issue_number: str) -> dict[str, Any]:
    payload = run_gh(
        [
            "issue",
            "view",
            issue_number,
            "--json",
            "number,title,url,state,labels,body",
        ]
    )
    return json.loads(payload)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def render_issue_prompt(
    issue: dict[str, Any],
    issue_kind: str | None = None,
    slug: str | None = None,
    snapshot_date: str | None = None,
    template_path: Path | None = None,
) -> tuple[str, str]:
    labels = label_names(issue.get("labels"))
    title = str(issue.get("title", "")).strip() or "(untitled)"
    number = str(issue.get("number", "")).strip()
    if not number:
        raise ValueError("Issue metadata is missing a number.")
    resolved_kind = issue_kind or infer_issue_kind(labels, title)
    resolved_slug = slug or slugify(title)
    branch = f"{KIND_TO_BRANCH_PREFIX[resolved_kind]}/{number}-{resolved_slug}"
    template_file = template_path or repo_root() / "prompts" / "templates" / "ISSUE_RESOLUTION_TEMPLATE.md"
    template = template_file.read_text(encoding="utf-8")
    body = str(issue.get("body", "")).strip() or "(no issue body)"
    rendered = render_template(
        template,
        {
            "ISSUE_NUMBER": number,
            "ISSUE_TITLE": title,
            "ISSUE_URL": str(issue.get("url", "")).strip() or "(url unavailable)",
            "ISSUE_STATE": str(issue.get("state", "")).strip() or "(state unavailable)",
            "ISSUE_LABELS": ", ".join(labels) if labels else "(none)",
            "ISSUE_KIND": resolved_kind,
            "SHORT_SLUG": resolved_slug,
            "YYYY-MM-DD": snapshot_date or date.today().isoformat(),
            "TARGET_BRANCH": branch,
            "TARGET_FILES": "(identify during startup)",
            "RELATED_TEST_FILES": "(identify during startup)",
            "RELATED_DOC_FILES": "(identify during startup)",
            "RUNTIME_ARTIFACTS": "(none known at prompt render time)",
            "IN_SCOPE_1": "Acceptance criteria from the GitHub issue",
            "IN_SCOPE_2": "Tests and docs required by the implementation",
            "OUT_SCOPE_1": "Unrelated cleanup",
            "OUT_SCOPE_2": "Live issue closure before PR merge",
            "TOUCHED_FILES": "{TOUCHED_FILES}",
        },
    )
    rendered += "\n## Issue Body Snapshot\n\n"
    rendered += body + "\n"
    filename = f"ISSUE-{number}-{resolved_slug}.md"
    return filename, rendered


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a local issue-resolution prompt from GitHub issue metadata.")
    parser.add_argument("--issue-number", required=True)
    parser.add_argument("--issue-kind", choices=sorted(KIND_TO_BRANCH_PREFIX))
    parser.add_argument("--slug", help="Override the generated lowercase kebab-case slug.")
    parser.add_argument("--output-dir", type=Path, default=Path("prompts/issues"))
    parser.add_argument("--dry-run", action="store_true", help="Print the rendered prompt instead of writing it.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+", args.issue_number):
        parser.error("--issue-number must contain digits only.")
    if args.slug and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug):
        parser.error("--slug must be lowercase kebab-case (a-z, 0-9, hyphen).")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        issue = fetch_issue(args.issue_number)
        filename, rendered = render_issue_prompt(issue, issue_kind=args.issue_kind, slug=args.slug)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.dry_run:
        print(rendered)
        return 0
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote issue prompt: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
