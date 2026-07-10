import tempfile
import unittest
from pathlib import Path

from patchpilot.models import (
    CommandExecution,
    FileChange,
    Issue,
    PatchPilotResult,
    PatchResult,
    RepairPlan,
    RepoContext,
    TestResult,
)
from patchpilot.report_writer import render_report, write_report


REQUIRED_SECTIONS = [
    "# PatchPilot Repair Report",
    "## Issue Summary",
    "## Repository Summary",
    "## Files Inspected",
    "## Modification Plan",
    "## Files Changed",
    "## Patch Diff",
    "## Commands Executed",
    "## Test Results",
    "## Failure Analysis",
    "## Final Status",
    "## PR Description Draft",
]


def _result(final_status: str, with_patch: bool = False) -> PatchPilotResult:
    issue = Issue(title="add is wrong", body="fix `add`", raw_text="x", path="i.md")
    context = RepoContext(
        repo_path="/repo",
        file_tree=["calculator.py"],
        readme_summary="demo",
        config_files=[],
        test_files=["test_calculator.py"],
        source_files=["calculator.py"],
        candidate_files=["calculator.py"],
        keywords=["add"],
    )
    plan = RepairPlan(
        issue_summary="add is wrong",
        suspected_root_cause="operator bug",
        candidate_files=["calculator.py"],
        proposed_changes="swap operator",
        test_strategy="run tests",
        risk_notes="bounded",
    )
    result = PatchPilotResult(issue=issue, repo_context=context, plan=plan)
    result.final_status = final_status
    if with_patch:
        change = FileChange(
            path="calculator.py",
            reason="attempt 1: swap `-` -> `+` in `add`",
            diff="--- a/calculator.py\n+++ b/calculator.py\n-    return a - b\n+    return a + b\n",
        )
        result.initial_patch = PatchResult(
            changed_files=[change], attempted=True, notes="ok"
        )
        execution = CommandExecution(
            command="python -m unittest", exit_code=0, stdout="", stderr="",
            duration_seconds=0.1, timed_out=False,
        )
        result.initial_test = TestResult(
            commands=[execution], passed=True, summary="All 1 test command(s) passed."
        )
    return result


class TestReportWriter(unittest.TestCase):
    def test_all_required_sections_present(self):
        report = render_report(_result("fixed", with_patch=True))
        for section in REQUIRED_SECTIONS:
            self.assertIn(section, report)

    def test_fixed_report_contains_diff_and_pr_draft(self):
        report = render_report(_result("fixed", with_patch=True))
        self.assertIn("```diff", report)
        self.assertIn("return a + b", report)
        self.assertIn("### Fix: add is wrong", report)

    def test_no_safe_repair_explains_why(self):
        report = render_report(_result("no_safe_repair"))
        self.assertIn("could not identify\na safe repair".replace("\n", " "), report.replace("\n", " "))
        self.assertIn("No patch was kept.", report)

    def test_no_tests_status_explains_why(self):
        report = render_report(_result("no_tests"))
        self.assertIn("No tests were detected.", report)

    def test_write_report_creates_file(self):
        path = Path(tempfile.mkdtemp()) / "report.md"
        write_report(_result("dry_run"), str(path))
        self.assertTrue(path.is_file())
        self.assertIn("Dry run", path.read_text())


if __name__ == "__main__":
    unittest.main()
