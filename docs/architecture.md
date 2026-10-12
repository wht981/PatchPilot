# LLM repair runtime

The default engine is a bounded model conversation. The model chooses what to
read, how to diagnose the issue and which source changes to propose. The pipeline
owns verification and delivery. The offline heuristic engine is selected
explicitly and shares this infrastructure.

## Model interface

`ModelClient.complete(messages) -> ModelReply` is the seam between the agent and
its provider. `OpenAICompatibleClient` implements it with standard-library HTTP;
tests implement it with scripted replies. A reply contains text and optional
input/output token counts. No provider key is stored in run results.

The HTTP adapter sends a non-streaming `/chat/completions` request with `model`,
`messages` and a configurable output-token parameter. JSON mode is on by
default. Credentials are environment-only, remote URLs must use HTTPS, redirects
are refused, retries are bounded, and truncated/refused responses are errors.

## Agent action contract

The adapter does not need provider-specific function calling. The model returns
one JSON action per turn; every action is parsed and validated by the host.
Unsupported or malformed actions receive an error observation and consume a call.

| Action | Required fields | Effect |
|---|---|---|
| `read_file` | `path`; optional inclusive `start_line`, `end_line` | read at most 200 lines / 20,000 characters of UTF-8 text |
| `search` | `query` | literal search over the compact repository tree; at most 30 matches |
| `plan` | all `RepairPlan` fields | finish planning without writes |
| `patch` | `analysis`, `edits` | return a validated source transaction during repair |
| `stop` | `reason` | end without an additional proposal |

Each edit has `path`, `old_text`, `new_text`. Existing source files must be read
before editing, and `old_text` must match exactly once in the current staged
content. Multiple replacements in a file are evaluated in order. A new source
file uses an empty `old_text`. A proposal may contain at most 20 edits across 8
files, each no larger than 250 KB. Tests and configuration are read-only.

The system prompt defines the protocol and treats issue text, code and tool
observations as untrusted evidence. Validation enforces tool capabilities even
when the model does not follow the prompt. This does not confine the effects
of generated source executed by a test; there is no OS sandbox.

## Verification transaction

1. Capture Git state and detect the test commands before any generated edit.
2. Run the baseline. Passing tests skip model calls; absent behavior tests skip
   repair. Dry runs use model planning but skip all tests/writes.
3. Let the model inspect evidence and finish its repair plan.
4. Validate all replacement preimages, paths and limits without modifying files.
5. Recheck original bytes immediately before writing. Apply the complete batch.
6. Run the same test commands with fresh Python bytecode caches.
7. On failure or an interrupted test invocation, restore the entire batch.
   Return failed output and the rejected diff to the model. Every retry uses
   original source; there is no accumulation of unverified edits.
8. On success, keep the complete winning transaction. Deliver all changed files
   on a fix branch, in place, or as a patch with original bytes restored.

`max_debug_rounds` caps patch attempts at one plus the configured number of
retries. `max_model_calls` separately caps the full conversation, including
exploration, planning and rejected actions. The request context has a character
budget; oversized context fails explicitly instead of silently dropping evidence.

## Results and observability

`PatchPilotResult` records baseline tests, every attempted patch and verification,
the complete winning patch, delivery, model usage and any model failure. A
transaction can contain multiple files, so kept changes are selected from the
passing attempt rather than from a suffix of the edit history.

Events record model calls and token usage, source reads/searches, proposal
validation, writes, test outcomes, rollback and delivery. Full raw prompts and
responses are not persisted in the trace. Reports and JSON summaries redact
credentials. Token usage is reported when supplied by the endpoint; missing
counts are represented as zero. Evaluation additionally records per-task call
counts and tokens, and runs on copies of task fixtures.

Model-network/protocol/budget errors produce `model_error` with a report and trace
and no unverified edits. Invalid configuration produces CLI exit 2 before repair.
No model failure silently selects the heuristic engine.
