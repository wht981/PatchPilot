# PatchPilot

[![CI](https://github.com/wht981/PatchPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/wht981/PatchPilot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

PatchPilot is an **LLM-driven, test-verified coding agent**. Give it a local
repository and an issue: the model explores code, diagnoses the failure, plans
the repair and proposes changes across files. PatchPilot applies the proposed
transaction, runs the original test commands, and returns failures to the model
for a bounded retry. Only a passing repair is delivered.

The LLM is the default reasoning and repair engine. A separate offline heuristic
engine remains available as a regression baseline. Both use the same test,
delivery, report and trace infrastructure. The runtime uses **Python's standard
library only**; no model SDK or agent framework is required.

## Quick start

Python 3.9 or newer:

```bash
git clone https://github.com/wht981/PatchPilot.git
cd PatchPilot
python -m pip install -e .                 # optional; python -m works directly

export PATCHPILOT_MODEL="your-model-id"
export OPENAI_API_KEY="your-api-key"       # keep credentials out of CLI arguments
# Optional: an alternative OpenAI-compatible provider
# export PATCHPILOT_BASE_URL="https://your-provider.example/v1"

python -m patchpilot run \
  --repo ./examples/sample_repo \
  --issue ./examples/sample_issue.md \
  --no-apply --patch-file ./fix.patch
```

The example starts with a failing `add` implementation. `--no-apply` writes a
verified patch and restores the example; use `git apply fix.patch` to apply it.
A model ID is required explicitly so PatchPilot never silently selects a
different model or paid service. The API key is read from an environment variable.
`.env.example` documents the settings; PatchPilot does not automatically load
dotenv files.

For an existing local OpenAI-compatible server:

```bash
python -m patchpilot run \
  --repo ./examples/sample_repo --issue ./examples/sample_issue.md \
  --model your-local-model --base-url http://localhost:8000/v1 \
  --token-parameter max_tokens --plain-json --no-apply
```

Localhost endpoints can run without a key. Remote endpoints require HTTPS and
a key. Keep JSON mode enabled when your provider supports it; `--plain-json`
omits `response_format` but still requires and validates JSON actions.

## What the model controls

- **Repository exploration:** choose source and test files to read, search
  literal code fragments, and navigate beyond the initial keyword ranking.
- **Diagnosis and planning:** infer a root cause using issue text, code and
  baseline test failures; produce a structured repair plan.
- **Multi-file repair:** propose exact-match replacements or new source files,
  including changes beyond single-token mutations.
- **Debugging:** receive the actual failed test output and rejected patch;
  revise the hypothesis against the restored original source.

The host controls path validation, complete batch validation, writes, tests,
rollback and Git delivery. The model has no shell tool and cannot choose a
different verification command, edit tests/configuration or declare success.

## Architecture

```text
Issue + repository context
          |
    Baseline test suite
          |
    LLM agent <---- read_file / search observations
          |
     Repair plan
          |
    Exact-match patch proposal
          |
    Validate complete batch -> Apply -> Run original tests
          |                                |
          |                          failure: revert
          |                                |
          |                         feedback to LLM
          |
      passing repair -> Git branch / patch file / in-place
                     -> Report + event trace + token usage
```

| Module | Responsibility |
|---|---|
| `llm.py` | model interface, environment configuration, OpenAI-compatible HTTP adapter |
| `agent.py` | bounded model conversation, read tools, structured plans and proposals |
| `workspace.py` | validated paths, source edits, byte-preserving multi-file transactions |
| `pipeline.py` | baseline verification, apply/test/revert loop and delivery |
| `repo_context.py` | repository tree, test/config discovery and initial file ranking |
| `test_runner.py` | host-selected tests, timeouts and fresh Python bytecode caches |
| `git_workflow.py` | verified fix branches and portable patch files |
| `security.py`, `tracing.py` | credential redaction, command policy and event records |
| `report_writer.py`, `eval_runner.py` | auditable results and per-task evaluation metrics |
| `planner.py`, `patch_runner.py` | explicit offline heuristic baseline |

The model adapter uses the [Chat Completions protocol](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
JSON mode ensures parseable JSON where supported; PatchPilot separately
validates action fields and patch preimages, as described in the
[official JSON mode guidance](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat).
See [the architecture document](docs/architecture.md) for the action contract
and transaction invariants.

## Configuration

Flags override environment variables. LLM settings apply to both `run` and `eval`.

| Flag | Environment | Default |
|---|---|---|
| `--engine` | — | `llm`; use `heuristic` for offline repairs |
| `--model` | `PATCHPILOT_MODEL` | required in LLM mode |
| `--base-url` | `PATCHPILOT_BASE_URL` | `https://api.openai.com/v1` |
| `--api-key-env` | `PATCHPILOT_API_KEY_ENV` | `OPENAI_API_KEY` |
| `--llm-timeout` | `PATCHPILOT_LLM_TIMEOUT` | 60 seconds per HTTP request |
| `--max-tokens` | `PATCHPILOT_MAX_TOKENS` | 8192 output tokens per request |
| `--token-parameter` | `PATCHPILOT_TOKEN_PARAMETER` | `max_completion_tokens`; also supports `max_tokens` |
| `--plain-json` | `PATCHPILOT_JSON_MODE=0` | JSON mode enabled |
| `--max-model-calls` | — | 16 calls per task, including reads and invalid actions |
| `--max-debug-rounds` | — | 1 retry after the initial patch |

Transient HTTP/network failures get at most two retries per call. Authentication
errors, refusals and truncated responses fail explicitly. There is no silent
fallback to heuristic repair. Reports and JSON summaries include the engine,
model, logical model-call count and token usage when returned by the provider.
HTTP retries do not increment the logical model-call count.

## Run and delivery options

```bash
python -m patchpilot run \
  --repo /path/to/repo \
  --issue https://github.com/owner/repo/issues/123 \
  --output patchpilot_report.md --trace patchpilot_trace.json \
  --max-debug-rounds 2 --max-model-calls 24 --json
```

`--issue` accepts local Markdown or a GitHub issue URL; set `GITHUB_TOKEN` for
private issues. Repositories must already be local.

- Default delivery: on a clean Git repository, commit all verified files on a
  new `patchpilot/fix-<slug>` branch. The checkout moves to that branch; the
  original branch's commits remain unchanged.
- `--no-apply`: write all verified diffs to `--patch-file` (default
  `patchpilot_fix.patch`) and restore the workspace, including new files.
- `--in-place`: keep the verified edits in the working tree.
- `--dry-run`: ask the model to explore and plan; run no tests and apply no edits.
  This still makes model requests.

Dirty Git repositories and non-Git directories use in-place delivery by default;
the report explains why. A passing baseline returns `already_passing` and makes
no model calls. Missing behavior tests return `no_tests` in LLM mode: compilation
alone is insufficient to accept a repair.

`--json` prints only a JSON summary to stdout; logs go to stderr.
Exit codes: **0** = fixed / already passing / dry run; **1** = unsuccessful repair
(including `model_error`); **2** = configuration, usage or runtime error.

## Offline baseline and tests

```bash
python -m patchpilot run --engine heuristic \
  --repo ./examples/sample_repo --issue ./examples/sample_issue.md --no-apply

python -m unittest discover -s tests -v
python -m patchpilot eval --engine heuristic --tasks ./examples/eval_tasks
```

The heuristic engine uses bounded arithmetic/comparison, boolean and off-by-one
mutations. It requires no model or credentials. Existing historical showcase
outputs in [docs/showcase](docs/showcase/README.md) demonstrate this engine.

Tests include scripted model conversations and a real local HTTP test server.
They verify planning, multi-file repair, test feedback, rejected actions, budgets,
rollback on exceptions/interruption, credential handling and Git delivery without
paid API requests. These tests verify the runtime; they do not measure a real
model's repair quality.

## LLM evaluation

```bash
python -m patchpilot eval \
  --tasks ./examples/llm_tasks --output-dir ./eval_results/llm --json
```

`examples/llm_tasks` includes a multi-file normalization/greeting issue requiring
Unicode case folding and whitespace handling, beyond the offline mutation
engine. You can also evaluate the LLM on `examples/eval_tasks` for comparison.
Each task is copied into a temporary workspace; fixtures remain unchanged.

Results include success rate, test verification, runtime, command count, changed
files, patch size, model calls and input/output tokens. Model evaluations use your
configured provider and may incur charges; the model-call and token caps apply
independently to each task.

## GitHub maintainer workflow

[Auto Repair](.github/workflows/auto-repair.yml) runs when a maintainer labels an
issue `patchpilot`, or through manual dispatch. Configure these repository settings:

- Secret `PATCHPILOT_API_KEY`: your provider credential.
- Variable `PATCHPILOT_MODEL`: the model ID.
- Optional variable `PATCHPILOT_BASE_URL`: an OpenAI-compatible HTTPS base URL.
- Optional variables `PATCHPILOT_TOKEN_PARAMETER` and `PATCHPILOT_JSON_MODE` for
  provider compatibility (`max_tokens` and `0`, respectively).

The workflow creates a PR only for a verified repair and uploads reports/traces.
Label/manual triggers keep paid model runs under maintainer control. CI uses
the offline regression gate and the credential-free model runtime tests.

## Scope and safeguards

- Exact replacements must match once. The entire batch is validated before any
  write; failed attempts restore original bytes and remove newly created files.
- Paths outside the repository, symlink paths, VCS metadata, credential files,
  tests, configuration and hidden edit targets are rejected.
- Known provider credentials and recognizable secret formats are redacted from
  model context, reports and traces. The selected provider-key environment
  variable is removed from child test/Git commands.
- Repository reads, patch size, model calls, debug rounds and request/command
  timeouts are bounded. Test commands are fixed before model edits.

Verification currently detects Python unittest/pytest and `npm test`.
Repository retrieval uses a compact tree of up to 500 files and literal search;
very large repositories may need more advanced retrieval. JSON mode and token
parameter support vary by provider.

This runtime is **not an OS sandbox**. Tests execute repository code with the
current user's permissions, and generated source can have side effects when
imported or executed. Use trusted repositories or an isolated container for
untrusted code. A passing suite verifies covered behavior, not every requirement
in an issue. Regression tests should reproduce the bug before a repair run.
