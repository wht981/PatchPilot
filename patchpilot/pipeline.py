"""PatchPilot pipeline: the bounded autonomous repair loop.

Issue Loader -> Repo Context Builder -> Planner -> Patch Runner
-> Test Runner -> Debug Loop (bounded) -> Report Writer.

Every step is traced; every repair attempt is verified by tests and
reverted if the suite does not pass.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from patchpilot.git_workflow import (
    AUTO,
    IN_PLACE,
    NO_APPLY,
    GitState,
    commit_fix_on_branch,
    detect_git_state,
    write_patch_file,
)
from patchpilot.issue_loader import load_issue
from patchpilot.models import (
    DebugRound,
    Delivery,
    PatchPilotResult,
    PatchResult,
    TestResult,
)
from patchpilot.patch_runner import CandidatePatch, PatchRunner
from patchpilot.planner import HeuristicPlanner
from patchpilot.repo_context import build_repo_context
from patchpilot.report_writer import write_report
from patchpilot.security import SecurityPolicy
from patchpilot.test_runner import detect_test_commands, failure_output, run_tests
from patchpilot.tracing import Tracer


logger = logging.getLogger(__name__)

FIXED = "fixed"
ALREADY_PASSING = "already_passing"
NOT_FIXED = "not_fixed"
NO_SAFE_REPAIR = "no_safe_repair"
NO_TESTS = "no_tests"
DRY_RUN = "dry_run"

SUCCESS_STATUSES = (FIXED, ALREADY_PASSING, DRY_RUN)


def run_pipeline(
    repo_path: str,
    issue_path: str,
    output_path: str = "patchpilot_report.md",
    trace_path: str = "patchpilot_trace.json",
    max_debug_rounds: int = 1,
    dry_run: bool = False,
    engine: str = "heuristic",
    test_timeout: float = 120.0,
    apply_mode: str = AUTO,
    patch_file: str = "patchpilot_fix.patch",
) -> PatchPilotResult:
    policy = SecurityPolicy()
    tracer = Tracer(policy)

    issue = load_issue(issue_path)
    tracer.record("issue_loaded", path=issue.path, title=issue.title)
    logger.info("Loaded issue: %s", issue.title)

    context = build_repo_context(repo_path, issue)
    tracer.record(
        "repo_scanned",
        repo=context.repo_path,
        files=len(context.file_tree),
        candidate_files=context.candidate_files,
        keywords=context.keywords,
    )
    logger.info("Candidate files: %s", context.candidate_files)

    planner = _make_planner(engine)
    plan = planner.create_repair_plan(issue, context)
    tracer.record("plan_created", candidate_files=plan.candidate_files)

    result = PatchPilotResult(
        issue=issue, repo_context=context, plan=plan, dry_run=dry_run
    )

    if dry_run:
        result.final_status = DRY_RUN
        tracer.record("final_status", status=DRY_RUN, reason="dry run requested")
        _finish(result, tracer, output_path, trace_path)
        return result

    git_state = detect_git_state(context.repo_path, policy)
    tracer.record(
        "git_state_detected",
        is_repo=git_state.is_repo,
        is_clean=git_state.is_clean,
        branch=git_state.current_branch,
    )

    commands = detect_test_commands(context.repo_path)
    tracer.record("tests_detected", commands=commands)
    if not commands:
        result.final_status = NO_TESTS
        tracer.record(
            "final_status",
            status=NO_TESTS,
            reason="no test command detected; repair cannot be verified",
        )
        _finish(result, tracer, output_path, trace_path)
        return result

    baseline = run_tests(context.repo_path, commands, policy, timeout=test_timeout)
    result.baseline_test = baseline
    tracer.record("baseline_tests", passed=baseline.passed, summary=baseline.summary)

    if baseline.passed:
        result.final_status = ALREADY_PASSING
        tracer.record(
            "final_status",
            status=ALREADY_PASSING,
            reason="test suite already passes; nothing to repair",
        )
        _finish(result, tracer, output_path, trace_path)
        return result

    revert_fix = None
    if engine == "openhands":
        _run_openhands_repair(
            result, commands, policy, tracer, max_debug_rounds, test_timeout
        )
    else:
        revert_fix = _run_heuristic_repair(
            result, commands, policy, tracer, max_debug_rounds, test_timeout
        )

    if result.final_status == FIXED:
        result.delivery = _deliver_fix(
            result, git_state, apply_mode, patch_file, revert_fix, policy
        )
        tracer.record(
            "fix_delivered",
            mode=result.delivery.mode,
            branch=result.delivery.branch,
            patch_path=result.delivery.patch_path,
            note=result.delivery.note,
        )

    tracer.record("final_status", status=result.final_status)
    _finish(result, tracer, output_path, trace_path)
    return result


def _deliver_fix(
    result: PatchPilotResult,
    git_state: GitState,
    apply_mode: str,
    patch_file: str,
    revert_fix,
    policy: SecurityPolicy,
) -> Delivery:
    """Hand the verified fix to the user according to ``apply_mode``."""
    repo_path = result.repo_context.repo_path
    kept = result.kept_changes

    if apply_mode == NO_APPLY:
        delivery = write_patch_file(kept, patch_file)
        if revert_fix is not None:
            revert_fix()
        elif git_state.is_repo:
            from patchpilot.execution import run_command

            run_command("git checkout -- .", repo_path, timeout=15, policy=policy)
        else:
            delivery.note += (
                " (warning: the fix could not be reverted automatically; "
                "the working tree still contains it)"
            )
        return delivery

    if apply_mode == IN_PLACE:
        return Delivery(mode=IN_PLACE, note="fix applied to the working tree")

    # auto mode
    if git_state.is_repo and git_state.is_clean:
        return commit_fix_on_branch(
            repo_path, kept, result.issue.title, policy=policy
        )
    if git_state.is_repo:
        reason = "the working tree had uncommitted changes before the repair"
    else:
        reason = "the target is not the root of a git repository"
    return Delivery(
        mode=IN_PLACE,
        note=f"fix applied in place ({reason}, so no fix branch was created)",
    )


def _run_heuristic_repair(
    result: PatchPilotResult,
    commands: List[str],
    policy: SecurityPolicy,
    tracer: Tracer,
    max_debug_rounds: int,
    test_timeout: float,
):
    """Bounded candidate loop: one verified mutation per attempt.

    Returns a zero-argument revert callable for the winning candidate
    when the repair succeeds (used by ``--no-apply``), else None.
    """
    context, plan = result.repo_context, result.plan
    assert result.baseline_test is not None
    failure_text = failure_output(result.baseline_test)

    runner = PatchRunner(context.repo_path)
    candidates = runner.propose_candidates(plan, failure_text)
    tracer.record("candidates_proposed", count=len(candidates))

    if not candidates:
        result.final_status = NO_SAFE_REPAIR
        tracer.record(
            "final_status_reason",
            reason="no safe candidate patch could be proposed",
        )
        return None

    max_attempts = 1 + max(0, max_debug_rounds)
    for attempt, candidate in enumerate(candidates[:max_attempts], start=1):
        patch, test = _try_candidate(
            runner, candidate, context.repo_path, commands, policy,
            tracer, attempt, test_timeout,
        )
        if attempt == 1:
            result.initial_patch, result.initial_test = patch, test
        else:
            result.debug_rounds.append(
                DebugRound(
                    round_number=attempt - 1,
                    failure_analysis=_analyze_failure(failure_text, candidate),
                    patch=patch,
                    test=test,
                )
            )
        if test.passed:
            result.final_status = FIXED
            return lambda: runner.revert(candidate)
        failure_text = failure_output(test) or failure_text

    result.final_status = NOT_FIXED
    return None


def _try_candidate(
    runner: PatchRunner,
    candidate: CandidatePatch,
    repo_path: str,
    commands: List[str],
    policy: SecurityPolicy,
    tracer: Tracer,
    attempt: int,
    test_timeout: float,
) -> "tuple[PatchResult, TestResult]":
    change = runner.apply(
        candidate, reason=f"attempt {attempt}: {candidate.description}"
    )
    tracer.record("patch_applied", attempt=attempt, description=candidate.description)
    test = run_tests(repo_path, commands, policy, timeout=test_timeout)
    tracer.record(
        "tests_run", attempt=attempt, passed=test.passed, summary=test.summary
    )
    if test.passed:
        patch = PatchResult(
            changed_files=[change],
            attempted=True,
            notes=f"Verified repair on attempt {attempt}: {candidate.description}",
        )
    else:
        runner.revert(candidate)
        tracer.record("patch_reverted", attempt=attempt)
        patch = PatchResult(
            changed_files=[change],
            attempted=True,
            notes=(
                f"Attempt {attempt} did not pass the tests and was reverted: "
                f"{candidate.description}"
            ),
        )
    return patch, test


def _analyze_failure(failure_text: str, next_candidate: CandidatePatch) -> str:
    lines = [l for l in failure_text.splitlines() if l.strip()][-8:]
    excerpt = "\n".join(lines) if lines else "(no failure output captured)"
    return (
        "Previous attempt did not make the suite pass. Failing output excerpt:\n"
        f"{excerpt}\n"
        f"Next hypothesis: {next_candidate.description}"
    )


def _run_openhands_repair(
    result: PatchPilotResult,
    commands: List[str],
    policy: SecurityPolicy,
    tracer: Tracer,
    max_debug_rounds: int,
    test_timeout: float,
) -> None:
    from patchpilot.engines.openhands_engine import OpenHandsEngine

    engine = OpenHandsEngine()
    assert result.baseline_test is not None
    failure_text = failure_output(result.baseline_test)

    max_attempts = 1 + max(0, max_debug_rounds)
    for attempt in range(1, max_attempts + 1):
        patch = engine.repair(result.repo_context, result.plan, failure_text)
        tracer.record(
            "patch_applied",
            attempt=attempt,
            engine="openhands",
            files=[c.path for c in patch.changed_files],
        )
        test = run_tests(
            result.repo_context.repo_path, commands, policy, timeout=test_timeout
        )
        tracer.record(
            "tests_run", attempt=attempt, passed=test.passed, summary=test.summary
        )
        if attempt == 1:
            result.initial_patch, result.initial_test = patch, test
        else:
            result.debug_rounds.append(
                DebugRound(
                    round_number=attempt - 1,
                    failure_analysis=failure_text[-2000:],
                    patch=patch,
                    test=test,
                )
            )
        if test.passed:
            result.final_status = FIXED
            return
        failure_text = failure_output(test) or failure_text
    result.final_status = NOT_FIXED


def _make_planner(engine: str):
    if engine == "openhands":
        from patchpilot.engines.openhands_engine import OpenHandsEngine

        return OpenHandsEngine()
    return HeuristicPlanner()


def _finish(
    result: PatchPilotResult,
    tracer: Tracer,
    output_path: Optional[str],
    trace_path: Optional[str],
) -> None:
    if output_path:
        write_report(result, output_path)
    if trace_path:
        tracer.save(trace_path)
