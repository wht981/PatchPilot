"""Load a local markdown issue file into a typed Issue."""

from __future__ import annotations

from pathlib import Path

from patchpilot.models import Issue


class IssueLoadError(Exception):
    """Raised when the issue file cannot be loaded."""


def load_issue(issue_path: str) -> Issue:
    """Read a markdown issue file and return a typed Issue.

    The title is the first ``#`` heading if present, otherwise the first
    non-empty line. Everything after the title line is the body.
    """
    path = Path(issue_path)
    if not path.is_file():
        raise IssueLoadError(f"Issue file not found: {issue_path}")
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    if not raw_text.strip():
        raise IssueLoadError(f"Issue file is empty: {issue_path}")

    lines = raw_text.splitlines()
    title = ""
    title_index = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        title = stripped.lstrip("#").strip() if stripped.startswith("#") else stripped
        title_index = i
        break
    body = "\n".join(lines[title_index + 1 :]).strip()
    return Issue(title=title, body=body, raw_text=raw_text, path=str(path))
