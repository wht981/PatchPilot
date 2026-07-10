"""Render a PatchPilotResult as a human-readable markdown report."""

from __future__ import annotations

from pathlib import Path
from typing import List

from patchpilot.models import PatchPilotResult


_STATUS_LINES = {
    "fixed": "✅ Fixed — the repair was applied and the test suite passes.",
    "already_passing": "✅ Already passing — the test suite passed before any change; nothing to repair.",
    "not_fixed": "❌ Not fixed — no attempted patch made the test suite pass; all attempts were reverted.",
    "no_safe_repair": "⚠️ No safe repair — PatchPilot could not identify a safe repair candidate, so no files were modified.",
    "no_tests": "⚠️ No tests — no test command was detected, so a repair could not be verified and none was attempted.",
    "dry_run": "ℹ️ Dry run — analysis and planning only; no files were modified and no tests were run.",
}


def write_report(result: PatchPilotResult, output_path: str) -> None:
    Path(output_path).write_text(render_report(result), encoding="utf-8")


def render_report(result: PatchPilotResult) -> str:
    sections: List[str] = ["# PatchPilot Repair Report", ""]

    _add(sections, "Issue Summary", _issue_summary(result))
    _add(sections, "Repository Summary", _repo_summary(result))
    _add(sections, "Files Inspected", _files_inspected(result))
    _add(sections, "Modification Plan", _plan(result))
    _add(sections, "Files Changed", _files_changed(result))
    _add(sections, "Patch Diff", _patch_diff(result))
    _add(sections, "Commands Executed", _commands(result))
    _add(sections, "Test Results", _test_results(result))
    _add(sections, "Failure Analysis", _failure_analysis(result))
    _add(sections, "Final Status", _final_status(result))
    _add(sections, "PR Description Draft", _pr_draft(result))

    return "\n".join(sections)


def _add(sections: List[str], title: str, body: str) -> None:
    sections.append(f"## {title}")
    sections.append("")
    sections.append(body.rstrip() or "(empty)")
    sections.append("")


def _issue_summary(result: PatchPilotResult) -> str:
    issue = result.issue
    body = issue.body.strip() or "(the issue has no body)"
    return f"**{issue.title}**\n\n{body}\n\n_Source: `{issue.path}`_"


def _repo_summary(result: PatchPilotResult) -> str:
    context = result.repo_context
    lines = [
        f"- Repository: `{context.repo_path}`",
        f"- Files scanned (after skip rules): {len(context.file_tree)}",
        f"- Test files: {len(context.test_files)}",
        f"- Config files: {', '.join(f'`{c}`' for c in context.config_files) or 'none'}",
        f"- Issue keywords: {', '.join(f'`{k}`' for k in context.keywords[:8]) or 'none'}",
        "",
        "README excerpt:",
        "",
        "```",
        context.readme_summary[:800],
        "```",
    ]
    return "\n".join(lines)


def _files_inspected(result: PatchPilotResult) -> str:
    context = result.repo_context
    if not context.candidate_files:
        return (
            "No candidate files matched the issue keywords.\n\n"
            "Full scanned tree:\n"
            + "\n".join(f"- `{f}`" for f in context.file_tree[:30])
        )
    lines = ["Candidate files, ranked by issue relevance:", ""]
    lines += [f"1. `{f}`" for f in context.candidate_files]
    lines += ["", f"(out of {len(context.file_tree)} scanned files)"]
    return "\n".join(lines)


def _plan(result: PatchPilotResult) -> str:
    plan = result.plan
    return "\n".join(
        [
            f"- **Issue summary:** {plan.issue_summary}",
            f"- **Suspected root cause:** {plan.suspected_root_cause}",
            f"- **Candidate files:** {', '.join(f'`{c}`' for c in plan.candidate_files) or 'none'}",
            f"- **Proposed changes:** {plan.proposed_changes}",
            f"- **Test strategy:** {plan.test_strategy}",
            f"- **Risk notes:** {plan.risk_notes}",
        ]
    )


def _kept_changes(result: PatchPilotResult) -> list:
    if result.final_status != "fixed":
        return []
    kept = []
    for change in result.all_changed_files:
        if "reverted" not in change.reason:
            kept.append(change)
    # The last verified change is the one that made the suite pass.
    return kept[-1:] if kept else []


def _files_changed(result: PatchPilotResult) -> str:
    if result.dry_run:
        return "No files were modified (dry run)."
    kept = _kept_changes(result)
    if not kept:
        if not result.all_changed_files:
            return (
                "No files were modified because PatchPilot could not identify "
                "a safe repair."
            )
        return (
            "No files remain modified: every attempted patch failed test "
            "verification and was reverted."
        )
    return "\n".join(f"- `{c.path}` — {c.reason}" for c in kept)


def _patch_diff(result: PatchPilotResult) -> str:
    kept = _kept_changes(result)
    if not kept:
        return "No patch was kept."
    blocks = []
    for change in kept:
        blocks.append(f"```diff\n{change.diff.rstrip()}\n```")
    return "\n\n".join(blocks)


def _commands(result: PatchPilotResult) -> str:
    commands = result.all_commands
    if not commands:
        return "No commands were executed."
    lines = ["| Command | Exit | Duration (s) |", "|---|---|---|"]
    for execution in commands:
        lines.append(
            f"| `{execution.command}` | {execution.exit_code} | "
            f"{execution.duration_seconds} |"
        )
    return "\n".join(lines)


def _test_results(result: PatchPilotResult) -> str:
    parts = []
    if result.baseline_test is not None:
        parts.append(f"- **Baseline (before repair):** {result.baseline_test.summary}")
    if result.initial_test is not None:
        parts.append(f"- **After initial repair:** {result.initial_test.summary}")
    for round_ in result.debug_rounds:
        if round_.test is not None:
            parts.append(
                f"- **Debug round {round_.round_number}:** {round_.test.summary}"
            )
    if not parts:
        if result.dry_run:
            return "No tests were run (dry run)."
        return "No tests were detected."
    return "\n".join(parts)


def _failure_analysis(result: PatchPilotResult) -> str:
    if not result.debug_rounds:
        if result.final_status == "fixed":
            return "The initial repair attempt passed; no debug rounds were needed."
        return "No debug rounds were executed."
    blocks = []
    for round_ in result.debug_rounds:
        blocks.append(f"### Debug round {round_.round_number}\n\n{round_.failure_analysis}")
    return "\n\n".join(blocks)


def _final_status(result: PatchPilotResult) -> str:
    return _STATUS_LINES.get(result.final_status, result.final_status)


def _pr_draft(result: PatchPilotResult) -> str:
    kept = _kept_changes(result)
    if not kept:
        return (
            "Not applicable: no verified patch was produced, so there is "
            "nothing to open a pull request for."
        )
    files = ", ".join(f"`{c.path}`" for c in kept)
    baseline = (
        result.baseline_test.summary if result.baseline_test else "not available"
    )
    return "\n".join(
        [
            f"### Fix: {result.issue.title}",
            "",
            f"This PR fixes the issue described in `{result.issue.path}`.",
            "",
            f"- **Root cause:** {result.plan.suspected_root_cause}",
            f"- **Change:** {kept[0].reason} ({files})",
            f"- **Verification:** baseline was failing ({baseline}); after the "
            "change, all detected test commands pass.",
            "",
            "Generated by PatchPilot (test-verified autonomous repair).",
        ]
    )
