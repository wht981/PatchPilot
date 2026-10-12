"""Command-line interface for PatchPilot.

Usage:
    python -m patchpilot run --repo <repo_path> --issue <issue_path>
    python -m patchpilot eval --tasks <tasks_dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchpilot",
        description=(
            "PatchPilot: an LLM-driven, test-verified coding agent. "
            "Reads an issue, navigates the repository, plans a repair, "
            "edits code, verifies with tests, and writes a patch report."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="repair a repo based on an issue file")
    run.add_argument("--repo", required=True, help="path to the local repository")
    run.add_argument(
        "--issue",
        required=True,
        help=(
            "issue markdown file path, or a GitHub issue URL "
            "(https://github.com/<owner>/<repo>/issues/<n>; set GITHUB_TOKEN "
            "for private repos)"
        ),
    )
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
        help=(
            "max repair retries after the initial patch (default: 1; "
            "heuristic mode tries up to 6 mutations per round)"
        ),
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="analyze and plan only; do not modify files or run tests",
    )
    apply_group = run.add_mutually_exclusive_group()
    apply_group.add_argument(
        "--no-apply",
        action="store_true",
        help="write the verified fix to a patch file and restore the repo",
    )
    apply_group.add_argument(
        "--in-place",
        action="store_true",
        help="leave the fix in the working tree (skip the auto fix branch)",
    )
    run.add_argument(
        "--patch-file",
        default="patchpilot_fix.patch",
        help="patch file path used with --no-apply (default: patchpilot_fix.patch)",
    )
    run.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable JSON summary to stdout (for CI)",
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
    ev.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON results to stdout (for CI)",
    )
    ev.add_argument("--verbose", action="store_true", help="verbose logging")
    for command in (run, ev):
        command.add_argument("--engine", choices=("llm", "heuristic"), default="llm",
                             help="repair engine (default: llm; heuristic is offline)")
        command.add_argument("--model", help="model ID (or PATCHPILOT_MODEL)")
        command.add_argument("--base-url", help="OpenAI-compatible API base URL (or PATCHPILOT_BASE_URL)")
        command.add_argument("--api-key-env", help="name of the API key environment variable (default: OPENAI_API_KEY)")
        command.add_argument("--llm-timeout", type=float, help="timeout per model request in seconds (default: 60)")
        command.add_argument("--max-tokens", type=int, help="output token limit per model request (default: 8192)")
        command.add_argument("--token-parameter", choices=("max_completion_tokens", "max_tokens"),
                             help="token limit field expected by the provider")
        command.add_argument("--plain-json", action="store_true",
                             help="omit response_format for endpoints without JSON mode; validation still applies")
        command.add_argument("--max-model-calls", type=int, default=16,
                             help="total model call budget per task, including invalid actions (default: 16)")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """Exit codes: 0 = success (fixed / already passing / dry run, or all
    eval tasks passed); 1 = the repair or some eval task failed;
    2 = usage or runtime error."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        if args.command == "run":
            return _run_command(args)
        if args.command == "eval":
            return _eval_command(args)
        return 2
    except Exception as exc:
        print(f"patchpilot error: {exc}", file=sys.stderr)
        if args.verbose:
            raise
        return 2


def _run_command(args: argparse.Namespace) -> int:
    from patchpilot.pipeline import SUCCESS_STATUSES, run_pipeline
    from patchpilot.report_writer import result_summary_dict

    apply_mode = "auto"
    if args.no_apply:
        apply_mode = "no_apply"
    elif args.in_place:
        apply_mode = "in_place"
    config = _llm_config(args)
    result = run_pipeline(
        repo_path=args.repo,
        issue_path=args.issue,
        output_path=args.output,
        trace_path=args.trace,
        max_debug_rounds=args.max_debug_rounds,
        dry_run=args.dry_run,
        apply_mode=apply_mode,
        patch_file=args.patch_file,
        engine=args.engine, llm_config=config, max_model_calls=args.max_model_calls,
    )
    if args.json:
        from patchpilot.security import SecurityPolicy

        summary = result_summary_dict(result, policy=SecurityPolicy(known_secrets=[config.api_key] if config else []))
        summary["report_path"] = args.output
        summary["trace_path"] = args.trace
        print(json.dumps(summary, indent=2))
    else:
        print(f"Final status: {result.final_status}")
        if result.delivery is not None:
            print(f"Delivery: {result.delivery.note}")
        print(f"Report written to: {args.output}")
    return 0 if result.final_status in SUCCESS_STATUSES else 1


def _eval_command(args: argparse.Namespace) -> int:
    from patchpilot.eval_runner import run_eval

    summary = run_eval(
        tasks_dir=args.tasks,
        output_dir=args.output_dir,
        max_debug_rounds=args.max_debug_rounds,
        engine=args.engine, llm_config=_llm_config(args), max_model_calls=args.max_model_calls,
    )
    if args.json:
        from dataclasses import asdict

        print(json.dumps(asdict(summary), indent=2))
    else:
        print(f"Eval finished: {summary.passed}/{summary.total} tasks passed")
        print(f"Results written to: {args.output_dir}")
    return 0 if summary.passed == summary.total else 1


def _llm_config(args: argparse.Namespace):
    if args.engine == "heuristic":
        return None
    from patchpilot.llm import LLMConfig

    return LLMConfig.from_env(
        model=args.model, base_url=args.base_url, api_key_env=args.api_key_env,
        timeout=args.llm_timeout, max_tokens=args.max_tokens,
        token_parameter=args.token_parameter, json_mode=False if args.plain_json else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
