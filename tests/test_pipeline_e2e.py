"""End-to-end test: PatchPilot repairs the sample calculator repo."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from patchpilot.pipeline import run_pipeline


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestPipelineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="patchpilot_e2e_"))
        self.repo = self.workdir / "repo"
        shutil.copytree(EXAMPLES / "sample_repo", self.repo)
        self.report = self.workdir / "report.md"
        self.trace = self.workdir / "trace.json"
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def _run(self, **kwargs):
        return run_pipeline(
            repo_path=str(self.repo),
            issue_path=str(EXAMPLES / "sample_issue.md"),
            output_path=str(self.report),
            trace_path=str(self.trace),
            **kwargs,
        )

    def test_fixes_sample_calculator_bug(self):
        result = self._run(max_debug_rounds=1)
        self.assertEqual(result.final_status, "fixed")
        fixed = (self.repo / "calculator.py").read_text()
        self.assertIn("def add(a, b):\n    return a + b", fixed)
        # subtract must remain untouched.
        self.assertIn("def subtract(a, b):\n    return a - b", fixed)

    def test_writes_report_and_trace(self):
        self._run(max_debug_rounds=1)
        report = self.report.read_text()
        self.assertIn("## Patch Diff", report)
        self.assertIn("+    return a + b", report)
        trace = json.loads(self.trace.read_text())
        event_types = [e["type"] for e in trace["events"]]
        for expected in (
            "issue_loaded",
            "repo_scanned",
            "plan_created",
            "baseline_tests",
            "patch_applied",
            "tests_run",
            "final_status",
        ):
            self.assertIn(expected, event_types)

    def test_dry_run_modifies_nothing_and_still_reports(self):
        before = (self.repo / "calculator.py").read_text()
        result = self._run(dry_run=True)
        self.assertEqual(result.final_status, "dry_run")
        self.assertEqual((self.repo / "calculator.py").read_text(), before)
        self.assertIn("Dry run", self.report.read_text())


if __name__ == "__main__":
    unittest.main()
