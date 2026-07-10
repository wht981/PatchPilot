"""Command-line interface for PatchPilot.

Usage:
    python -m patchpilot run --repo <repo_path> --issue <issue_path>
    python -m patchpilot eval --tasks <tasks_dir>
"""

from __future__ import annotations

import argparse
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchpilot",
        description=(
            "PatchPilot: a test-verified autonomous coding agent. "
            "Reads an issue, navigates the repository, plans a repair, "
            "edits code, verifies with tests, and writes a patch report."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="repair a repo based on an issue file")
    run.add_argument("--repo", required=True, help="path to the local repository")
    run.add_argument("--issue", required=True, help="path to the issue markdown file")
    run.add_argument(
        "--output",
        default="patchpilot_report.md",
        help="path of the generated report (default: patchpilot_report.md)",
    )
    run.add_argument(
        "--trace",
        default="patchpilot_trace.json",
        help="path of the execution trace (default: patchpilot_trace.json)",
    )
    run.add_argument(
        "--max-debug-rounds",
        type=int,
        default=1,
        help="max debug rounds after the initial repair attempt (default: 1)",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="analyze and plan only; do not modify files or run tests",
    )
    run.add_argument(
        "--engine",
        choices=["heuristic", "openhands"],
        default="heuristic",
        help="repair engine (openhands requires the SDK and LLM_API_KEY)",
    )
    run.add_argument("--verbose", action="store_true", help="verbose logging")

    ev = subparsers.add_parser("eval", help="run the evaluation harness")
    ev.add_argument("--tasks", required=True, help="directory containing eval tasks")
    ev.add_argument(
        "--output-dir",
        default="eval_results",
        help="directory for results.json and summary.md (default: eval_results)",
    )
    ev.add_argument(
        "--max-debug-rounds",
        type=int,
        default=1,
        help="max debug rounds per task (default: 1)",
    )
    ev.add_argument("--verbose", action="store_true", help="verbose logging")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.command == "run":
        from patchpilot.pipeline import run_pipeline

        result = run_pipeline(
            repo_path=args.repo,
            issue_path=args.issue,
            output_path=args.output,
            trace_path=args.trace,
            max_debug_rounds=args.max_debug_rounds,
            dry_run=args.dry_run,
            engine=args.engine,
        )
        print(f"Final status: {result.final_status}")
        print(f"Report written to: {args.output}")
        return 0 if result.final_status in ("fixed", "dry_run") else 1
    if args.command == "eval":
        from patchpilot.eval_runner import run_eval

        summary = run_eval(
            tasks_dir=args.tasks,
            output_dir=args.output_dir,
            max_debug_rounds=args.max_debug_rounds,
        )
        print(f"Eval finished: {summary.passed}/{summary.total} tasks passed")
        print(f"Results written to: {args.output_dir}")
        return 0 if summary.passed == summary.total else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
