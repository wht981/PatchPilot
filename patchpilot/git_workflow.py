"""Deliver a verified fix without dirtying the user's working tree.

Modes:
- auto (default): if the target repo is a clean git repository, commit
  the fix on a new ``patchpilot/fix-<slug>`` branch (the user's original
  branch stays untouched); otherwise fall back to in-place editing and
  say why in the report.
- patch_file (``--no-apply``): write the verified diff to a patch file
  and restore the repository to its original state.
- in_place (``--in-place``): leave the applied fix in the working tree.

All git commands run through the security-validated ``run_command``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from patchpilot.execution import run_command
from patchpilot.models import Delivery, FileChange
from patchpilot.security import SecurityPolicy


AUTO = "auto"
IN_PLACE = "in_place"
NO_APPLY = "no_apply"


@dataclass(frozen=True)
class GitState:
    """Git status of the target repo, captured before any repair."""

    is_repo: bool
    is_clean: bool
    current_branch: str


def detect_git_state(
    repo_path: str, policy: Optional[SecurityPolicy] = None
) -> GitState:
    """Detect whether ``repo_path`` is the root of a clean git repository.

    A directory *inside* someone else's git repository (e.g. an example
    project nested in a bigger repo) is treated as not-a-repo, so
    PatchPilot never creates branches or commits in the outer repository.
    """
    toplevel = run_command(
        "git rev-parse --show-toplevel", repo_path, timeout=10, policy=policy
    )
    if toplevel.exit_code != 0:
        return GitState(is_repo=False, is_clean=False, current_branch="")
    if Path(toplevel.stdout.strip()).resolve() != Path(repo_path).resolve():
        return GitState(is_repo=False, is_clean=False, current_branch="")

    status = run_command(
        "git status --porcelain", repo_path, timeout=10, policy=policy
    )
    branch = run_command(
        "git rev-parse --abbrev-ref HEAD", repo_path, timeout=10, policy=policy
    )
    return GitState(
        is_repo=True,
        is_clean=status.exit_code == 0 and not status.stdout.strip(),
        current_branch=branch.stdout.strip(),
    )


def slugify(title: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "issue"


def commit_fix_on_branch(
    repo_path: str,
    changes: List[FileChange],
    issue_title: str,
    policy: Optional[SecurityPolicy] = None,
) -> Delivery:
    """Create ``patchpilot/fix-<slug>`` and commit the fix there.

    The working tree already contains the fix; ``git checkout -b``
    carries it onto the new branch, so the original branch is left
    exactly as it was.
    """
    branch = _unique_branch_name(repo_path, slugify(issue_title), policy)
    checkout = run_command(
        f"git checkout -b {branch}", repo_path, timeout=15, policy=policy
    )
    if checkout.exit_code != 0:
        return Delivery(
            mode=IN_PLACE,
            note=f"could not create branch {branch}: {checkout.stderr.strip()}",
        )

    paths = " ".join(f'"{c.path}"' for c in changes)
    run_command(f"git add -- {paths}", repo_path, timeout=15, policy=policy)
    message = f"PatchPilot: fix {issue_title}".replace('"', "'")
    committed = run_command(
        f'git commit -m "{message}"', repo_path, timeout=15, policy=policy
    )
    if committed.exit_code != 0:
        return Delivery(
            mode=IN_PLACE,
            branch=branch,
            note=f"commit failed on {branch}: {committed.stderr.strip()}",
        )
    commit_hash = run_command(
        "git rev-parse --short HEAD", repo_path, timeout=10, policy=policy
    ).stdout.strip()
    return Delivery(
        mode="branch",
        branch=branch,
        commit=commit_hash,
        note=(
            f"fix committed on branch {branch} ({commit_hash}); "
            "the original branch is unchanged"
        ),
    )


def write_patch_file(changes: List[FileChange], patch_path: str) -> Delivery:
    """Write the kept diffs to ``patch_path`` (git-apply compatible)."""
    diff_text = "".join(
        change.diff if change.diff.endswith("\n") else change.diff + "\n"
        for change in changes
    )
    path = Path(patch_path)
    path.write_text(diff_text, encoding="utf-8")
    return Delivery(
        mode="patch_file",
        patch_path=str(path),
        note=(
            f"repository restored to its original state; apply the fix with "
            f"`git apply {path}`"
        ),
    )


def _unique_branch_name(
    repo_path: str, slug: str, policy: Optional[SecurityPolicy]
) -> str:
    base = f"patchpilot/fix-{slug}"
    name = base
    for suffix in range(2, 20):
        exists = run_command(
            f"git rev-parse --verify --quiet {name}",
            repo_path,
            timeout=10,
            policy=policy,
        )
        if exists.exit_code != 0:
            return name
        name = f"{base}-{suffix}"
    return name
