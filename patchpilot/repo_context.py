"""Build a compact repository context for planning.

Scans the repo while skipping VCS metadata, virtualenvs, caches, and
binary files, then ranks source files against issue keywords so the
planner sees a short candidate list instead of the whole repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from patchpilot.models import Issue, RepoContext


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "eval_results",
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".lock", ".sqlite", ".db",
}

CONFIG_FILE_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "Makefile", "tox.ini", "Cargo.toml", "go.mod",
}

MAX_FILE_TREE_ENTRIES = 500
MAX_CONTENT_SCAN_BYTES = 1_000_000
MAX_CANDIDATES = 5
README_SUMMARY_LINES = 40

_STOPWORDS = {
    "the", "and", "but", "not", "with", "this", "that", "should", "would",
    "please", "current", "behavior", "appears", "incorrect", "function",
    "returns", "return", "result", "wrong", "bug", "fix", "issue", "when",
    "tests", "test", "verify", "implementation", "expected", "actual",
}


def _iter_repo_files(repo: Path) -> List[Path]:
    files: List[Path] = []
    stack = [repo]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                if entry.suffix.lower() not in SKIP_EXTENSIONS:
                    files.append(entry)
    return sorted(files)


def extract_keywords(issue: Issue) -> List[str]:
    """Extract identifier-like keywords from the issue, code spans first."""
    code_spans = re.findall(r"`([^`\n]+)`", issue.raw_text)
    keywords: List[str] = []
    for span in code_spans:
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", span):
            if len(token) >= 2:
                keywords.append(token)
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", issue.raw_text):
        lowered = token.lower()
        if lowered not in _STOPWORDS and lowered not in keywords:
            keywords.append(lowered)
    # De-duplicate, preserving order (code-span tokens rank first).
    seen = set()
    unique = []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            unique.append(keyword)
    return unique[:20]


def _score_file(path: Path, relative: str, keywords: List[str]) -> int:
    score = 0
    name = path.name.lower()
    for keyword in keywords:
        if keyword.lower() in name:
            score += 5
        if keyword.lower() in relative.lower():
            score += 2
    try:
        if path.stat().st_size <= MAX_CONTENT_SCAN_BYTES:
            content = path.read_text(encoding="utf-8", errors="replace")
            for keyword in keywords:
                score += min(content.count(keyword), 5)
    except OSError:
        pass
    return score


def _is_test_file(relative: str) -> bool:
    name = Path(relative).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or "tests/" in relative.replace("\\", "/")
    )


def build_repo_context(repo_path: str, issue: Issue) -> RepoContext:
    """Scan ``repo_path`` and rank files relevant to ``issue``."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {repo_path}")

    files = _iter_repo_files(repo)
    relative_paths = [str(f.relative_to(repo)) for f in files]
    file_tree = relative_paths[:MAX_FILE_TREE_ENTRIES]

    readme_summary = "No README found."
    for f in files:
        if f.name.lower().startswith("readme"):
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            readme_summary = "\n".join(lines[:README_SUMMARY_LINES])
            break

    config_files = [r for r in relative_paths if Path(r).name in CONFIG_FILE_NAMES]
    test_files = [r for r in relative_paths if _is_test_file(r)]
    source_files = [
        r
        for r in relative_paths
        if Path(r).suffix in (".py", ".js", ".ts", ".go", ".rs", ".java")
        and r not in test_files
    ]

    keywords = extract_keywords(issue)
    scores: Dict[str, int] = {}
    for path, relative in zip(files, relative_paths):
        if relative in source_files:
            score = _score_file(path, relative, keywords)
            if score > 0:
                scores[relative] = score
    candidate_files = sorted(scores, key=lambda r: scores[r], reverse=True)
    candidate_files = candidate_files[:MAX_CANDIDATES]

    return RepoContext(
        repo_path=str(repo),
        file_tree=file_tree,
        readme_summary=readme_summary,
        config_files=config_files,
        test_files=test_files,
        source_files=source_files,
        candidate_files=candidate_files,
        keywords=keywords,
    )
