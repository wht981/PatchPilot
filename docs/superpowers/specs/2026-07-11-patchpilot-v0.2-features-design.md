# PatchPilot v0.2 Feature Design

Date: 2026-07-11
Status: approved

Four independent features, one `feat/*` branch each, merged to `main`
with `--no-ff` in order A → B → C → D.

## A. Safe patch workflow (`feat/git-workflow`)

Problem: `run` edits the user's working tree in place with no undo.

New module `patchpilot/git_workflow.py` (git commands run through the
existing security-validated `execution.run_command`).

Behavior after a verified fix (`final_status == fixed`):

- **auto (default):** if the target repo is a git repository whose root
  equals the target path (guards against nested non-repo dirs inside an
  outer repo) and the working tree was clean *before* the repair, create
  `patchpilot/fix-<issue-slug>` (suffix `-2`, `-3`… on collision), commit
  the fix there with the user's own git identity, and stay on that
  branch. The original branch remains untouched. Otherwise fall back to
  in-place and explain why in the report.
- **`--no-apply`:** write the verified diff to `--patch-file`
  (default `patchpilot_fix.patch`, must pass `git apply --check`), then
  revert the repo to its original state.
- **`--in-place`:** force v0.1 behavior.

Git state is detected before the repair starts. Delivery info (mode,
branch, commit, patch path, fallback reason) is added to the result,
rendered in the report's Final Status section, and traced.

## B. GitHub issue URLs (`feat/github-issues`)

`load_issue()` accepts `https://github.com/<owner>/<repo>/issues/<n>` in
addition to local paths. Fetches via `urllib` from
`api.github.com/repos/<owner>/<repo>/issues/<n>` (zero new deps), sends
`Authorization: Bearer $GITHUB_TOKEN` when set, 15 s timeout. Clear
errors for 404 (mention token for private repos), network failures, and
URLs that point at pull requests. Tests mock the fetch; no live network.

## C. Stronger repair strategies (`feat/repair-strategies`)

New bounded mutation classes in `patch_runner.py`, ranked after the
existing operator swaps:

1. boolean logic: `and` ↔ `or` (word boundaries, code portion only)
2. off-by-one integer constants: `n` → `n+1`, `n-1`

Candidate cap raised to 16. The repair loop gains a per-round candidate
budget (`CANDIDATES_PER_ROUND = 6`): round 1 tries up to 6 verified
candidates, each debug round up to 6 more; every attempt is traced and
reverted on failure, so total attempts stay bounded by
`(1 + max_debug_rounds) * 6` and by the candidate cap.

New eval tasks: `task_003` (an `or` that must be `and`) and `task_004`
(an off-by-one constant). Eval expectation: 4/4 with default settings.

## D. CI-friendly output (`feat/ci-output`)

- `run --json` / `eval --json`: machine-readable JSON on stdout
  (status, success, changed files, diff, test summaries, delivery info,
  report/trace paths). Logs already go to stderr.
- Exit codes: 0 = fixed / already passing / dry run; 1 = not fixed /
  no safe repair / no tests; 2 = usage or runtime error (all exceptions
  caught in `main` with a clear stderr message).
- README gains a GitHub Actions usage example.

## Wrap-up

Bump version to 0.2.0, update README per feature (inside each feature
branch), push `main` and all feature branches.
