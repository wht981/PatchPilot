"""Load an issue from a local markdown file or a GitHub issue URL."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from patchpilot.models import Issue


GITHUB_ISSUE_URL = re.compile(
    r"^https?://github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)/?$"
)
GITHUB_API_TIMEOUT = 15.0


class IssueLoadError(Exception):
    """Raised when the issue cannot be loaded."""


def load_issue(issue_source: str) -> Issue:
    """Load an issue from a local markdown path or a GitHub issue URL.

    Accepted sources:
    - a local markdown file path
    - ``https://github.com/<owner>/<repo>/issues/<number>``
      (set ``GITHUB_TOKEN`` for private repositories)
    """
    match = GITHUB_ISSUE_URL.match(issue_source.strip())
    if match:
        return _load_github_issue(*match.groups(), url=issue_source.strip())
    return _load_local_issue(issue_source)


def _load_local_issue(issue_path: str) -> Issue:
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


def _load_github_issue(owner: str, repo: str, number: str, url: str) -> Issue:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    payload = _fetch_json(api_url)

    if "pull_request" in payload:
        raise IssueLoadError(
            f"{url} points to a pull request, not an issue. "
            "PatchPilot repairs from issues."
        )
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    if not title:
        raise IssueLoadError(f"GitHub issue has no title: {url}")
    raw_text = f"# {title}\n\n{body}".strip()
    return Issue(title=title, body=body, raw_text=raw_text, path=url)


def _fetch_json(api_url: str) -> dict:
    """GET a GitHub API URL and parse the JSON response.

    Kept as a module-level function so tests can substitute it.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "patchpilot",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=GITHUB_API_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IssueLoadError(
                f"GitHub issue not found (404): {api_url}. If the repository "
                "is private, set the GITHUB_TOKEN environment variable."
            ) from exc
        if exc.code in (401, 403):
            raise IssueLoadError(
                f"GitHub API access denied ({exc.code}): {api_url}. Check "
                "GITHUB_TOKEN or API rate limits."
            ) from exc
        raise IssueLoadError(f"GitHub API error {exc.code}: {api_url}") from exc
    except urllib.error.URLError as exc:
        raise IssueLoadError(
            f"Could not reach the GitHub API ({exc.reason}). Check your "
            "network or proxy settings."
        ) from exc
