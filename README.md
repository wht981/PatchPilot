# PatchPilot

PatchPilot is a **test-verified autonomous coding agent** built on the
[OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk).
It reads an issue, navigates the repository, plans a repair, edits code,
runs the tests, debugs on failure, and produces an auditable patch report.

The core pipeline is a *distilled* implementation of the OpenHands kernel
contracts — typed action/observation records, an immutable event trace,
bounded command execution inside the workspace, and risk-classified
security policies — with **zero runtime dependencies**, so the demo runs
on stock Python. A pluggable engine swaps the deterministic heuristics
for a real OpenHands LLM agent.

## Features

- **Issue understanding** — parses local markdown issues into typed records.
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
| `engines/openhands_engine.py` | optional LLM engine on the OpenHands SDK |

**Engines.** The default `heuristic` engine is deterministic and offline:
it localizes the fault from failing tests and issue keywords, then applies
bounded single-operator mutations that must pass the test suite. The
`openhands` engine delegates planning and editing to a real OpenHands
agent (file editor + terminal tools); PatchPilot still performs the final
test verification itself.

## Installation

No dependencies are required for the default engine (Python >= 3.9):

```bash
cd PatchPilot
python -m patchpilot --help
```

Optional, for the LLM-backed engine (Python >= 3.13):

```bash
pip install -e ".[openhands]"
export LLM_API_KEY=...   # never hardcoded or committed
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
`--dry-run` (plan only, no edits, no tests), `--engine {heuristic,openhands}`,
`--no-apply` / `--in-place` / `--patch-file` (fix delivery, see below),
`--verbose`.

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

- Only local repos and local markdown issues (no GitHub API yet).
- The heuristic engine repairs small, single-operator logic bugs; complex
  or multi-file bugs need the OpenHands engine.
- Test detection covers pytest/unittest, `npm test`, and compile checks.
- Large repos may need stronger retrieval than keyword ranking.

## Roadmap

- GitHub issue URL support and automatic PR creation.
- Deeper OpenHands SDK integration (condensers, sandboxed workspaces).
- Embedding-based file retrieval for large repositories.
- Benchmark dataset and a richer failure taxonomy.
- Sandbox hardening (containerized execution via openhands-workspace).
