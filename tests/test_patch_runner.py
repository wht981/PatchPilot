import tempfile
import unittest
from pathlib import Path

from patchpilot.models import RepairPlan
from patchpilot.patch_runner import PatchBoundaryError, PatchRunner


def _plan(candidates):
    return RepairPlan(
        issue_summary="`add(a, b)` returns the wrong result",
        suspected_root_cause="",
        candidate_files=candidates,
        proposed_changes="",
        test_strategy="",
        risk_notes="",
    )


class TestPatchRunner(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_pr_"))
        (self.repo / "calculator.py").write_text(
            "def add(a, b):\n"
            "    return a - b\n"
            "\n"
            "def subtract(a, b):\n"
            "    return a - b\n"
        )
        self.runner = PatchRunner(str(self.repo))

    def test_proposes_mutation_in_failing_function_first(self):
        failure = "FAIL: test_add (test_calculator.TestCalculator.test_add)"
        candidates = self.runner.propose_candidates(_plan(["calculator.py"]), failure)
        self.assertTrue(candidates)
        first = candidates[0]
        self.assertIn("add", first.description)
        self.assertIn("return a + b", first.new_content)
        # subtract must not be touched: the failing test targets `add`.
        self.assertIn("def subtract(a, b):\n    return a - b", first.new_content)

    def test_apply_generates_diff_and_revert_restores(self):
        failure = "FAIL: test_add"
        candidate = self.runner.propose_candidates(_plan(["calculator.py"]), failure)[0]
        change = self.runner.apply(candidate, reason="fix add")
        self.assertIn("-    return a - b", change.diff)
        self.assertIn("+    return a + b", change.diff)
        self.assertIn("return a + b", (self.repo / "calculator.py").read_text())
        self.runner.revert(candidate)
        self.assertIn("return a - b", (self.repo / "calculator.py").read_text())

    def test_refuses_files_outside_repo(self):
        with self.assertRaises(PatchBoundaryError):
            self.runner.propose_candidates(_plan(["../outside.py"]), "FAIL: test_x")

    def test_bounded_number_of_candidates(self):
        failure = ""  # no target info: falls back to all functions, still bounded
        candidates = self.runner.propose_candidates(_plan(["calculator.py"]), failure)
        self.assertLessEqual(len(candidates), 12)


if __name__ == "__main__":
    unittest.main()
