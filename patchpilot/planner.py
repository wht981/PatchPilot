"""Repair planning.

The Planner is a pluggable interface: the default HeuristicPlanner is
deterministic and dependency-free; the OpenHands engine provides an
LLM-backed planner with the same contract (see patchpilot.engines).
"""

from __future__ import annotations

from patchpilot.models import Issue, RepairPlan, RepoContext


class HeuristicPlanner:
    """Deterministic planner built from issue keywords and file ranking."""

    def create_repair_plan(self, issue: Issue, context: RepoContext) -> RepairPlan:
        if context.candidate_files:
            root_cause = (
                "Keyword and content ranking points at: "
                + ", ".join(context.candidate_files)
                + ". The defect is most likely a small logic error inside a "
                "function named in the issue or in the failing tests."
            )
            proposed = (
                "Run the test suite to capture the failing behavior, then apply "
                "bounded single-operator mutations inside the suspect functions "
                f"of {context.candidate_files[0]} and re-verify with the tests. "
                "Revert any mutation that does not make the suite pass."
            )
        else:
            root_cause = (
                "No candidate file matched the issue keywords; PatchPilot "
                "cannot localize the defect confidently."
            )
            proposed = (
                "No modification will be attempted because PatchPilot could "
                "not identify a safe repair target."
            )

        if context.test_files:
            test_strategy = (
                f"Verify with the detected test suite ({len(context.test_files)} "
                "test file(s)); a repair is accepted only if all test commands "
                "exit 0."
            )
        else:
            test_strategy = (
                "No tests were detected; fall back to compile checks only, and "
                "flag the repair as unverified."
            )

        return RepairPlan(
            issue_summary=issue.title,
            suspected_root_cause=root_cause,
            candidate_files=list(context.candidate_files),
            proposed_changes=proposed,
            test_strategy=test_strategy,
            risk_notes=(
                "Heuristic engine: only candidate files may be modified, one "
                "operator per candidate patch, every patch is test-verified and "
                "reverted on failure. No file outside the repository is touched."
            ),
        )


def create_repair_plan(issue: Issue, context: RepoContext) -> RepairPlan:
    """Module-level convenience using the default heuristic planner."""
    return HeuristicPlanner().create_repair_plan(issue, context)
