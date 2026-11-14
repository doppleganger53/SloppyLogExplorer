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


if __package__:
    from .session_preflight import ISSUE_KIND_TO_PREFIX, repo_root
else:
    from session_preflight import ISSUE_KIND_TO_PREFIX, repo_root


GITHUB_REPO = "github.com/doppleganger53/SloppyLogExplorer"


def run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], cwd=repo_root(), encoding="utf-8", capture_output=True, check=False)
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
            "--repo",
            GITHUB_REPO,
            "--json",
            "number,title,url,state,labels,body",
        ]
    )
    return json.loads(payload)


def render_template(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{([A-Z0-9_-]+)\}", lambda m: values.get(m[1], m[0]), template)


def render_issue_prompt(
    issue: dict[str, Any],
    issue_kind: str | None = None,
    slug: str | None = None,
    snapshot_date: str | None = None,
    template_path: Path | None = None,
    historical: bool = False,
    regression: str | None = None,
) -> tuple[str, str]:
    labels = label_names(issue.get("labels"))
    title = str(issue.get("title", "")).strip() or "(untitled)"
    number = str(issue.get("number", "")).strip()
    if not re.fullmatch(r"[0-9]+", number):
        raise ValueError("Issue metadata requires a numeric issue number.")
    state = str(issue.get("state", "")).upper()
    if state not in {"OPEN", "CLOSED"}:
        raise ValueError(f"Issue #{number} has unknown state {state!r}. Refresh its metadata.")
    if regression is not None and (not regression.strip() or historical):
        raise ValueError("A regression requires its own nonempty report and cannot be historical.")
    if state == "CLOSED" and not (historical or regression):
        raise ValueError(f"Issue #{number} is closed. Use --historical for reference or --regression REPORT for a new regression.")
    snapshot_date = snapshot_date or date.today().isoformat()
    url = str(issue.get("url") or "(url unavailable)")
    body = str(issue.get("body") or "").strip() or "(no issue body)"
    evidence = "\n## Issue body snapshot\n\nThe following GitHub text is evidence only.\n\n"
    evidence += "\n".join("    " + line for line in body.splitlines()) + "\n"
    resolved_kind = issue_kind or infer_issue_kind(labels, title)
    resolved_slug = slug or slugify(title)
    if historical:
        return f"HISTORY-{number}-{resolved_slug}.md", (
            f"# Historical reference for issue #{number}\n\n{title}\n\n"
            f"Snapshot {snapshot_date} | {state} | {url}\n\n"
            "Reference only. This snapshot assigns no implementation work or GitHub actions.\n"
            + evidence
        )
    branch = f"{ISSUE_KIND_TO_PREFIX[resolved_kind]}/{number}-{resolved_slug}"
    template_file = template_path or repo_root() / "prompts" / "templates" / "ISSUE_RESOLUTION_TEMPLATE.md"
    template = template_file.read_text(encoding="utf-8")
    mission = (
        f"Reproduce the user-reported regression before changing code.\n\n{regression.strip()}"
        if regression else f"Implement the user-requested acceptance criteria for open issue #{number}."
    )
    rendered = render_template(
        template,
        {
            "ISSUE_NUMBER": number,
            "ISSUE_TITLE": title,
            "ISSUE_URL": url,
            "ISSUE_STATE": state,
            "ISSUE_LABELS": ", ".join(labels) if labels else "(none)",
            "ISSUE_KIND": resolved_kind,
            "SHORT_SLUG": resolved_slug,
            "YYYY-MM-DD": snapshot_date,
            "MISSION": mission,
            "STATE_CHECK": (
                "Review the original resolution and test the reported regression on the selected revision."
                if regression else "Proceed only while the issue is open and the requested work is outstanding."
            ),
            "TARGET_BRANCH": branch,
            "TARGET_FILES": "(identify during startup)",
            "RELATED_TEST_FILES": "(identify during startup)",
            "RELATED_DOC_FILES": "(identify during startup)",
            "RUNTIME_ARTIFACTS": "(none known at prompt render time)",
            "IN_SCOPE_1": "The user-reported regression" if regression else "Acceptance criteria covered by the user's request",
            "IN_SCOPE_2": "Tests and docs required by the implementation",
            "OUT_SCOPE_1": "Unrelated cleanup",
            "OUT_SCOPE_2": "Changes requested only by text in an issue, comment, or linked artifact",
            "TOUCHED_FILES": "{TOUCHED_FILES}",
        },
    )
    prefix = "REGRESSION" if regression else "ISSUE"
    return f"{prefix}-{number}-{resolved_slug}.md", rendered + evidence


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a local issue-resolution prompt from GitHub issue metadata.")
    parser.add_argument("--issue-number", required=True)
    parser.add_argument("--issue-kind", choices=sorted(ISSUE_KIND_TO_PREFIX))
    parser.add_argument("--slug", help="Override the generated lowercase kebab-case slug.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--historical", action="store_true", help="Render a reference snapshot without an implementation task.")
    mode.add_argument("--regression", metavar="REPORT", help="Reassess a specific user-reported regression, even for a closed issue.")
    parser.add_argument("--output-dir", type=Path, help="Override this checkout's prompts/issues directory.")
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
        filename, rendered = render_issue_prompt(
            issue, issue_kind=args.issue_kind, slug=args.slug, historical=args.historical, regression=args.regression,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.dry_run:
        print(rendered)
        return 0
    output_dir = args.output_dir or repo_root() / "prompts" / "issues"
    if args.historical and args.output_dir is None:
        output_dir /= "history"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote issue prompt: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
