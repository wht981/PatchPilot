# Showcase: a complete PatchPilot run

The files here are the **unedited output of a real PatchPilot run**
against a fresh git clone of the sample calculator repo:

- [`demo-report.md`](demo-report.md) — the generated repair report:
  issue summary, ranked candidate files, repair plan, verified patch
  diff, executed commands, test results, and a PR description draft.
- [`demo-trace.json`](demo-trace.json) — the full execution trace,
  one timestamped event per pipeline step (secrets masked).

The command that produced them:

```bash
python -m patchpilot run \
  --repo /path/to/calculator-demo \
  --issue examples/sample_issue.md \
  --output docs/showcase/demo-report.md \
  --trace docs/showcase/demo-trace.json
```

Because the target was a clean git repository, PatchPilot delivered the
verified fix on a dedicated branch and left `main` untouched:

```text
Final status: fixed
Delivery: fix committed on branch patchpilot/fix-bug-calculator-add-function-returns-the (0d99715);
          the original branch is unchanged
```

The same run works with a GitHub issue URL instead of a local file:

```bash
python -m patchpilot run \
  --repo /path/to/calculator-demo \
  --issue https://github.com/<owner>/<repo>/issues/<n>
```
