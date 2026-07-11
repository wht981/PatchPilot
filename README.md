# PatchPilot

[![CI](https://github.com/wht981/PatchPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/wht981/PatchPilot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

PatchPilot is a **standalone, test-verified autonomous coding agent**.
It reads an issue, navigates the repository, plans a repair, edits code,
runs the tests, debugs on failure, and produces an auditable patch report
— with **zero runtime dependencies**, so it runs on stock Python.

The design follows the kernel contracts of modern software agent
runtimes (the architecture is distilled from a deep study of the
[OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk),
without depending on it): typed action/observation records, an immutable
event trace, bounded command execution inside the workspace, and
risk-classified security policies.

## Features

- **Issue understanding** — parses local markdown issues or fetches
  GitHub issue URLs directly (`--issue https://github.com/o/r/issues/1`,
  zero extra dependencies; `GITHUB_TOKEN` supported for private repos).
- **Repository navigation** — scans the repo (skipping VCS metadata, caches,
  and binaries) and ranks candidate files against issue keywords.
- **Planning** — produces a repair plan: root cause hypothesis, candidate
  files, proposed changes, test strategy, and risk notes.
- **Test-verified repair** — every patch must make the detected test
  commands pass; failed attempts are automatically reverted.
- **Safe patch delivery** — on a clean git repo the verified fix is
  committed to a `patchpilot/fix-<slug>` branch and your branch stays
  untouched; `--no-apply` writes a `.patch` file and restores the repo
  instead, `--in-place` edits the working tree directly.
- **Bounded debug loop** — on failure, analyzes test output and retries with
  a new hypothesis, up to `--max-debug-rounds` (never unbounded).
- **Patch reports** — a full markdown report with diff, commands, test
  results, and a ready-to-use PR description draft.
- **Execution tracing** — every step is recorded to `patchpilot_trace.json`
  with secrets masked.
- **Safety layer** — command risk classification, dangerous-command
  blocking, secret masking, and workspace execution boundaries.
- **Evaluation harness** — measures success rate, runtime, commands
  executed, files changed, and patch size across repair tasks.

## Architecture

```
Issue Loader -> Repo Context Builder -> Planner -> Patch Runner
             -> Test Runner -> Debug Loop -> Report Writer
```

| Module | Responsibility |
|---|---|
| `issue_loader.py` | parse the markdown issue |
| `repo_context.py` | scan + rank repository files |
| `planner.py` | build the repair plan (pluggable) |
| `patch_runner.py` | conservative, bounded code edits with diffs |
| `test_runner.py` | detect and run test commands with timeouts |
| `pipeline.py` | orchestrate the verify/debug loop |
| `report_writer.py` | render `patchpilot_report.md` |
| `security.py` | command risk policy + secret masking |
| `tracing.py` | JSON event trace of the whole run |
| `eval_runner.py` | evaluation harness |

**Repair strategy.** The engine is deterministic and offline: it
localizes the fault from failing tests and issue keywords, then applies
bounded single-token mutations (operator swaps, `and`/`or` logic swaps,
off-by-one integer constants) that must pass the test suite. Failed
candidates are reverted; a fix is only ever delivered after the whole
suite passes. The planner is a small pluggable interface, so an
LLM-backed strategy can be added later without touching the pipeline.

## Installation

No dependencies — standard library only (Python >= 3.9):

```bash
cd PatchPilot
python -m patchpilot --help
```

## Quick Start

```bash
python -m patchpilot run \
  --repo ./examples/sample_repo \
  --issue ./examples/sample_issue.md \
  --output ./patchpilot_report.md \
  --max-debug-rounds 1
```

Flags: `--repo`, `--issue`, `--output`, `--trace`, `--max-debug-rounds`,
`--dry-run` (plan only, no edits, no tests),
`--no-apply` / `--in-place` / `--patch-file` (fix delivery, see below),
`--json`, `--verbose`.

**Fix delivery.** By default, if the target repo is a clean git
repository, the verified fix is committed on a new
`patchpilot/fix-<issue-slug>` branch and your original branch is left
untouched (merge with `git merge` or drop the branch to reject the fix).
If the repo is not a git repository or has uncommitted changes,
PatchPilot falls back to in-place editing and says so in the report.
With `--no-apply` the fix is written to a patch file
(`--patch-file`, default `patchpilot_fix.patch`) and the repository is
restored — apply it later with `git apply patchpilot_fix.patch`.

## Running the Sample

`examples/sample_repo` contains a calculator whose `add` intentionally
returns `a - b`. PatchPilot reads `examples/sample_issue.md`, localizes
`calculator.py`, swaps the operator, verifies with the tests, and writes
`patchpilot_report.md` plus `patchpilot_trace.json`. The run modifies the
sample repo in place; restore the bug with `git checkout -- examples/sample_repo`.

The unedited report and trace from a real run — including branch-based
fix delivery on a clean git repo — are in
[`docs/showcase/`](docs/showcase/README.md).

## Running Tests

```bash
python -m unittest discover -s tests -v      # or: python -m pytest tests
```

## Running Evaluation

```bash
python -m patchpilot eval --tasks ./examples/eval_tasks
```

Task repos are copied to a temporary workspace, so fixtures are never
modified. Results land in `eval_results/results.json` and
`eval_results/summary.md` (success rate, runtime, commands, patch size,
failure taxonomy).

## CI Usage

`--json` prints a machine-readable summary to stdout (logs go to
stderr), and exit codes are CI-friendly: **0** = fixed / already passing
/ dry run, **1** = repair failed, **2** = usage or runtime error.

```yaml
# .github/workflows/patchpilot.yml (excerpt)
- name: Attempt automatic repair
  run: |
    python -m patchpilot run \
      --repo . \
      --issue "$ISSUE_URL" \
      --no-apply --patch-file fix.patch \
      --json > patchpilot.json
- name: Upload proposed patch
  uses: actions/upload-artifact@v4
  with:
    name: patchpilot-fix
    path: |
      fix.patch
      patchpilot.json
      patchpilot_report.md
```

## Safety Controls

- **Command risk classification** (`safe` / `caution` / `dangerous`);
  dangerous commands (`rm -rf /`, `sudo`, `curl … | sh`, `cat .env`,
  `ssh`/`scp`/`rsync`, …) are refused before execution.
- **Secret masking** — API keys, tokens, and passwords are replaced with
  `[MASKED_SECRET]` in traces, logs, and reports.
- **Workspace boundary** — commands run only inside the target repo;
  patches to paths outside the repo root are rejected.
- **Bounded autonomy** — hard timeouts on every command, a capped number
  of repair attempts, and automatic revert of unverified patches.

## Limitations

- Repos must be local (GitHub issues can be fetched by URL, but the
  repository itself is not cloned automatically).
- The mutation engine repairs small single-token logic bugs (operators,
  boolean logic, off-by-one constants); complex or multi-file bugs are
  reported honestly as `not_fixed` / `no_safe_repair` instead of being
  guessed at.
- Test detection covers pytest/unittest, `npm test`, and compile checks.
- Large repos may need stronger retrieval than keyword ranking.

## Roadmap

- Automatic PR creation for delivered fix branches.
- An optional LLM-backed planner/repair strategy behind the existing
  pluggable planner interface.
- Embedding-based file retrieval for large repositories.
- Benchmark dataset and a richer failure taxonomy.
- Sandbox hardening (containerized command execution).
