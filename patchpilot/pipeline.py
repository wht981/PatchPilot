"""PatchPilot pipeline: the bounded LLM-driven repair loop.

Every step is traced; every repair attempt is verified by tests and
reverted if the suite does not pass.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from patchpilot.agent import LLMAgent
from patchpilot.llm import LLMConfig, LLMError, ModelClient, OpenAICompatibleClient
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
    RepairPlan,
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
MODEL_ERROR = "model_error"

SUCCESS_STATUSES = (FIXED, ALREADY_PASSING, DRY_RUN)

# Verified candidate patches tried per repair round: the initial round
# and each debug round may each consume up to this many candidates.
CANDIDATES_PER_ROUND = 6


def run_pipeline(
    repo_path: str,
    issue_path: str,
    output_path: str = "patchpilot_report.md",
    trace_path: str = "patchpilot_trace.json",
    max_debug_rounds: int = 1,
    dry_run: bool = False,
    test_timeout: float = 120.0,
    apply_mode: str = AUTO,
    patch_file: str = "patchpilot_fix.patch",
    engine: str = "llm",
    llm_config: Optional[LLMConfig] = None,
    model_client: Optional[ModelClient] = None,
    max_model_calls: int = 16,
) -> PatchPilotResult:
    if engine not in ("llm", "heuristic"):
        raise ValueError("engine must be llm or heuristic")
    if not 0 <= max_debug_rounds <= 20:
        raise ValueError("max debug rounds must be between 0 and 20")
    if not 1 <= max_model_calls <= 100:
        raise ValueError("max model calls must be between 1 and 100")
    if apply_mode not in (AUTO, IN_PLACE, NO_APPLY):
        raise ValueError("invalid apply mode")
    if engine == "llm" and model_client is None:
        llm_config = llm_config or LLMConfig.from_env()
    policy = SecurityPolicy(
        known_secrets=[llm_config.api_key] if llm_config else [],
        secret_env_vars=[llm_config.api_key_env] if llm_config else [],
    )
    tracer = Tracer(policy)

    issue = load_issue(issue_path)
    tracer.record("issue_loaded", path=issue.path, title=issue.title)
    logger.info("Loaded issue: %s", policy.mask_secrets(issue.title))

    context = build_repo_context(repo_path, issue)
    tracer.record(
        "repo_scanned",
        repo=context.repo_path,
        files=len(context.file_tree),
        candidate_files=context.candidate_files,
        keywords=context.keywords,
    )
    logger.info("Candidate files: %s", policy.sanitize(context.candidate_files))

    agent = None
    if engine == "llm":
        client = model_client or OpenAICompatibleClient(llm_config, policy)
        agent = LLMAgent(client, context, policy, tracer, max_model_calls)
    plan = HeuristicPlanner().create_repair_plan(issue, context) if engine == "heuristic" else RepairPlan(
        issue_summary=issue.title, suspected_root_cause="Awaiting model analysis.",
        candidate_files=[], proposed_changes="No patch proposed.",
        test_strategy="Run the original detected test commands.",
        risk_notes="Model edits are validated, test-verified and reverted on failure.",
    )
    result = PatchPilotResult(
        issue=issue, repo_context=context, plan=plan, dry_run=dry_run, engine=engine,
        model_usage=agent.usage if agent else None,
    )
    tracer.record("engine_selected", engine=engine, model=client.model if agent else "")

    if dry_run:
        if agent:
            try:
                result.plan = agent.create_repair_plan(issue, context)
            except LLMError as exc:
                _model_error(result, tracer, exc, policy)
                _finish(result, tracer, output_path, trace_path)
                return result
        tracer.record("plan_created", candidate_files=result.plan.candidate_files)
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
    if agent and commands and all(" -m compileall " in c for c in commands):
        commands = []  # Compilation alone cannot verify a behavioral repair.
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

    if agent:
        try:
            result.plan = agent.create_repair_plan(issue, context, failure_output(baseline))
            tracer.record("plan_created", candidate_files=result.plan.candidate_files)
            revert_fix = _run_llm_repair(
                agent, result, commands, policy, tracer, max_debug_rounds, test_timeout)
        except LLMError as exc:
            _model_error(result, tracer, exc, policy)
            revert_fix = None
    else:
        tracer.record("plan_created", candidate_files=result.plan.candidate_files)
        revert_fix = _run_heuristic_repair(
            result, commands, policy, tracer, max_debug_rounds, test_timeout)

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


def _model_error(result, tracer, exc, policy) -> None:
    result.final_status = MODEL_ERROR
    result.error = policy.mask_secrets(str(exc))
    tracer.record("final_status", status=MODEL_ERROR, reason=result.error)


def _run_llm_repair(agent, result, commands, policy, tracer,
                    max_debug_rounds, test_timeout):
    feedback = failure_output(result.baseline_test)
    attempted = False
    for attempt in range(1, max_debug_rounds + 2):
        transaction = agent.propose_patch(feedback)
        if transaction is None:
            result.final_status = NOT_FIXED if attempted else NO_SAFE_REPAIR
            result.error = agent.last_analysis
            return None
        transaction.apply()
        attempted = True
        tracer.record("patch_applied", attempt=attempt, files=[c.path for c in transaction.changes])
        try:
            test = run_tests(result.repo_context.repo_path, commands, policy, timeout=test_timeout)
        except BaseException:
            transaction.revert()
            tracer.record("patch_reverted", attempt=attempt, reason="verification interrupted")
            raise
        patch = PatchResult(transaction.changes, True, agent.last_analysis)
        if attempt == 1:
            result.initial_patch, result.initial_test = patch, test
        else:
            result.debug_rounds.append(DebugRound(attempt - 1, agent.last_analysis, patch, test))
        tracer.record("tests_run", attempt=attempt, passed=test.passed, summary=test.summary)
        if test.passed:
            result.final_status = FIXED
            return transaction.revert
        transaction.revert()
        tracer.record("patch_reverted", attempt=attempt)
        feedback = (
            "The previous patch failed and was reverted. Propose a revised patch against original source.\n"
            + failure_output(test) + "\nPrevious patch:\n"
            + "".join(c.diff for c in transaction.changes)[:12000])
    result.final_status = NOT_FIXED
    return None


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
        # --no-apply restores the workspace even when writing the artifact fails.
        try:
            return write_patch_file(kept, patch_file)
        finally:
            if revert_fix is not None:
                revert_fix()

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

    max_attempts = (1 + max(0, max_debug_rounds)) * CANDIDATES_PER_ROUND
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
    try:
        test = run_tests(repo_path, commands, policy, timeout=test_timeout)
    except BaseException:
        runner.revert(candidate)
        raise
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


def _finish(
    result: PatchPilotResult,
    tracer: Tracer,
    output_path: Optional[str],
    trace_path: Optional[str],
) -> None:
    try:
        if output_path:
            write_report(result, output_path, policy=tracer.policy)
    finally:
        if trace_path:
            tracer.save(trace_path)
