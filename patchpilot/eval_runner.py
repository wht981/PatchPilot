"""Evaluation harness: run PatchPilot over a directory of repair tasks.

Each task directory contains:
    repo/                the buggy repository
    issue.md             the issue to fix
    expected_tests.txt   (optional) test names that must pass after repair

Task repos are copied to a temporary workspace before each run, so the
task fixtures themselves are never modified.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from patchpilot.llm import LLMConfig
from patchpilot.models import PatchPilotResult
from patchpilot.pipeline import run_pipeline


@dataclass
class TaskResult:
    task_id: str
    success: bool
    final_status: str
    tests_passed: bool
    expected_tests_verified: Optional[bool]
    runtime_seconds: float
    commands_executed: int
    files_changed: int
    patch_size_lines: int
    failure_reason: str
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class EvalSummary:
    total: int
    passed: int
    results: List[TaskResult] = field(default_factory=list)


def run_eval(
    tasks_dir: str,
    output_dir: str = "eval_results",
    max_debug_rounds: int = 1,
    engine: str = "llm",
    llm_config: Optional[LLMConfig] = None,
    max_model_calls: int = 16,
) -> EvalSummary:
    tasks_root = Path(tasks_dir)
    if not tasks_root.is_dir():
        raise NotADirectoryError(f"Tasks directory not found: {tasks_dir}")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    results: List[TaskResult] = []
    for task_dir in sorted(p for p in tasks_root.iterdir() if p.is_dir()):
        if not (task_dir / "repo").is_dir() or not (task_dir / "issue.md").is_file():
            continue
        results.append(_run_task(task_dir, output, max_debug_rounds,
                                 engine, llm_config, max_model_calls))

    if not results:
        raise ValueError("No valid evaluation tasks found; each needs repo/ and issue.md.")

    summary = EvalSummary(
        total=len(results),
        passed=sum(1 for r in results if r.success),
        results=results,
    )
    (output / "results.json").write_text(
        json.dumps(asdict(summary), indent=2), encoding="utf-8"
    )
    (output / "summary.md").write_text(_render_summary(summary), encoding="utf-8")
    return summary


def _run_task(task_dir: Path, output: Path, max_debug_rounds: int,
              engine: str, llm_config: Optional[LLMConfig], max_model_calls: int) -> TaskResult:
    task_id = task_dir.name
    task_output = output / task_id
    task_output.mkdir(parents=True, exist_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix=f"patchpilot_eval_{task_id}_"))
    repo_copy = workdir / "repo"
    shutil.copytree(task_dir / "repo", repo_copy)

    start = time.monotonic()
    failure_reason = ""
    result: Optional[PatchPilotResult] = None
    try:
        result = run_pipeline(
            repo_path=str(repo_copy),
            issue_path=str(task_dir / "issue.md"),
            output_path=str(task_output / "report.md"),
            trace_path=str(task_output / "trace.json"),
            max_debug_rounds=max_debug_rounds,
            engine=engine, llm_config=llm_config, max_model_calls=max_model_calls,
        )
    except Exception as exc:  # a task crash must not abort the whole eval
        failure_reason = f"pipeline error: {exc}"
    runtime = round(time.monotonic() - start, 3)
    shutil.rmtree(workdir, ignore_errors=True)

    if result is None:
        return TaskResult(
            task_id=task_id, success=False, final_status="error",
            tests_passed=False, expected_tests_verified=None,
            runtime_seconds=runtime, commands_executed=0, files_changed=0,
            patch_size_lines=0, failure_reason=failure_reason,
        )

    tests_passed = _last_test_passed(result)
    expected_verified = _expected_tests_verified(task_dir, result)
    success = (
        result.final_status == "fixed"
        and tests_passed
        and expected_verified is not False
    )
    if not success and not failure_reason:
        failure_reason = _STATUS_FAILURE_REASONS.get(
            result.final_status, f"final status: {result.final_status}"
        )
        if result.error:
            failure_reason = result.error
        if expected_verified is False:
            failure_reason = "expected tests not found in passing output"

    kept = result.kept_changes
    patch_size = sum(
        1
        for change in kept
        for line in change.diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    )
    return TaskResult(
        task_id=task_id,
        success=success,
        final_status=result.final_status,
        tests_passed=tests_passed,
        expected_tests_verified=expected_verified,
        runtime_seconds=runtime,
        commands_executed=len(result.all_commands),
        files_changed=len({c.path for c in kept}) if result.final_status == "fixed" else 0,
        patch_size_lines=patch_size if result.final_status == "fixed" else 0,
        failure_reason=failure_reason,
        model_calls=result.model_usage.calls if result.model_usage else 0,
        input_tokens=result.model_usage.input_tokens if result.model_usage else 0,
        output_tokens=result.model_usage.output_tokens if result.model_usage else 0,
    )


_STATUS_FAILURE_REASONS = {
    "not_fixed": "no attempted patch made the tests pass",
    "no_safe_repair": "no safe repair candidate was found",
    "no_tests": "no test command detected, repair unverifiable",
    "already_passing": "task repo was not actually failing",
    "model_error": "model request failed or call budget exhausted",
}


def _last_test_passed(result: PatchPilotResult) -> bool:
    tests = [result.initial_test] + [r.test for r in result.debug_rounds]
    tests = [t for t in tests if t is not None]
    return bool(tests) and tests[-1].passed


def _expected_tests_verified(task_dir: Path, result: PatchPilotResult) -> Optional[bool]:
    expected_file = task_dir / "expected_tests.txt"
    if not expected_file.is_file():
        return None
    expected = [l.strip() for l in expected_file.read_text().splitlines() if l.strip()]
    if not expected:
        return None
    output_chunks = []
    final_tests = [result.initial_test] + [r.test for r in result.debug_rounds]
    final_tests = [t for t in final_tests if t is not None]
    if not final_tests or not final_tests[-1].passed:
        return False
    for execution in final_tests[-1].commands:
        output_chunks.append(execution.stdout)
        output_chunks.append(execution.stderr)
    combined = "\n".join(output_chunks)
    return all(name in combined for name in expected)


def _render_summary(summary: EvalSummary) -> str:
    lines = ["# PatchPilot Eval Summary", "", "## Overall Results", ""]
    rate = (100.0 * summary.passed / summary.total) if summary.total else 0.0
    lines += [
        f"- Tasks: {summary.total}",
        f"- Passed: {summary.passed}",
        f"- Success rate: {rate:.0f}%",
        "",
        "## Task Results",
        "",
        "| Task | Success | Status | Runtime (s) | Commands | Files changed | Patch size | Model calls | Tokens (in/out) | Failure reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary.results:
        lines.append(
            f"| {r.task_id} | {'✅' if r.success else '❌'} | {r.final_status} | "
            f"{r.runtime_seconds} | {r.commands_executed} | {r.files_changed} | "
            f"{r.patch_size_lines} | {r.model_calls} | {r.input_tokens}/{r.output_tokens} | {r.failure_reason or '-'} |"
        )

    lines += ["", "## Failure Taxonomy", ""]
    failures = Counter(r.failure_reason for r in summary.results if not r.success)
    if failures:
        for reason, count in failures.most_common():
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("No failures.")

    lines += [
        "",
        "## Observations",
        "",
        "- Every repair is accepted only after the task's test commands pass;",
        "  failed attempts are reverted, so task repos are never left broken.",
        "- Task fixtures are copied to a temporary workspace before each run.",
        "",
        "## Next Improvements",
        "",
        "- Compare LLM repairs against the explicit offline heuristic baseline.",
        "- Richer failure taxonomy (localization miss vs. repair miss).",
        "- Larger, more diverse task set with multi-file bugs.",
        "",
    ]
    return "\n".join(lines)
