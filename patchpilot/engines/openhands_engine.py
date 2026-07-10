"""LLM-backed repair engine built on the OpenHands Software Agent SDK.

Requires the optional dependencies (``pip install patchpilot[openhands]``,
Python >= 3.13) and an ``LLM_API_KEY`` environment variable. The engine
implements the same contract as the heuristic planner/patch runner, so
the pipeline can swap it in via ``--engine openhands``:

* ``create_repair_plan(issue, context)`` -> RepairPlan
* ``repair(context, plan, failure_text)`` -> PatchResult (agent edits
  files in the workspace; PatchPilot snapshots the repo before/after to
  compute diffs, and the pipeline still verifies with the test runner).
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Dict, List

from patchpilot.models import FileChange, Issue, PatchResult, RepairPlan, RepoContext


class OpenHandsEngineError(Exception):
    """Raised when the OpenHands engine cannot be used in this environment."""


def _require_sdk():
    try:
        from openhands.sdk import LLM, Agent, Conversation, Tool  # noqa: F401
        from openhands.tools.file_editor import FileEditorTool  # noqa: F401
        from openhands.tools.terminal import TerminalTool  # noqa: F401
    except ImportError as exc:
        raise OpenHandsEngineError(
            "The OpenHands SDK is not installed. Install the optional "
            "dependencies with `pip install patchpilot[openhands]` "
            "(requires Python >= 3.13), or use the default heuristic engine."
        ) from exc
    if not os.getenv("LLM_API_KEY"):
        raise OpenHandsEngineError(
            "LLM_API_KEY is not set. The OpenHands engine needs an LLM "
            "provider key; export LLM_API_KEY (and optionally LLM_MODEL)."
        )


class OpenHandsEngine:
    """Planner + patch runner backed by a real OpenHands agent."""

    def __init__(self, model: "str | None" = None) -> None:
        _require_sdk()
        self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-5")

    def create_repair_plan(self, issue: Issue, context: RepoContext) -> RepairPlan:
        return RepairPlan(
            issue_summary=issue.title,
            suspected_root_cause=(
                "To be localized by the OpenHands agent from the failing "
                f"tests; keyword ranking suggests: {', '.join(context.candidate_files) or 'unknown'}."
            ),
            candidate_files=list(context.candidate_files),
            proposed_changes=(
                "Delegate the repair to an OpenHands agent (file editor + "
                "terminal tools) constrained to the repo workspace; verify "
                "the result with the detected test commands."
            ),
            test_strategy=(
                "PatchPilot runs the detected test commands itself after the "
                "agent finishes; the repair is accepted only if they exit 0."
            ),
            risk_notes=(
                "Agent edits are limited to the repo workspace; all commands "
                "PatchPilot itself runs pass the security policy, and secrets "
                "are masked in traces and reports."
            ),
        )

    def repair(
        self, context: RepoContext, plan: RepairPlan, failure_text: str
    ) -> PatchResult:
        from openhands.sdk import LLM, Agent, Conversation, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.terminal import TerminalTool

        before = _snapshot(context.repo_path)

        llm = LLM(model=self.model, api_key=os.getenv("LLM_API_KEY"))
        agent = Agent(
            llm=llm,
            tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        )
        conversation = Conversation(agent=agent, workspace=context.repo_path)
        conversation.send_message(_repair_prompt(plan, failure_text))
        conversation.run()

        changed = _diff_snapshots(before, _snapshot(context.repo_path))
        return PatchResult(
            changed_files=changed,
            attempted=True,
            notes=(
                f"OpenHands agent ({self.model}) modified "
                f"{len(changed)} file(s); verification is performed by the "
                "PatchPilot test runner."
            ),
        )


def _repair_prompt(plan: RepairPlan, failure_text: str) -> str:
    return (
        "You are fixing a bug in this repository.\n\n"
        f"Issue: {plan.issue_summary}\n\n"
        f"Likely relevant files: {', '.join(plan.candidate_files) or 'unknown'}\n\n"
        "Failing test output:\n"
        f"{failure_text[-4000:]}\n\n"
        "Make the smallest change that fixes the failing tests. Do not "
        "modify the tests. Do not touch files outside this repository."
    )


_TEXT_SUFFIXES = {".py", ".js", ".ts", ".go", ".rs", ".java", ".txt", ".md", ".toml", ".cfg", ".json", ".yaml", ".yml"}


def _snapshot(repo_path: str) -> Dict[str, str]:
    from patchpilot.repo_context import SKIP_DIRS

    repo = Path(repo_path).resolve()
    snapshot: Dict[str, str] = {}
    for path in repo.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            try:
                snapshot[str(path.relative_to(repo))] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
    return snapshot


def _diff_snapshots(
    before: Dict[str, str], after: Dict[str, str]
) -> List[FileChange]:
    changes: List[FileChange] = []
    for relative in sorted(set(before) | set(after)):
        old, new = before.get(relative, ""), after.get(relative, "")
        if old == new:
            continue
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        changes.append(
            FileChange(
                path=relative,
                reason="edited by the OpenHands agent",
                diff=diff,
            )
        )
    return changes
