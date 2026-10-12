import shutil
import tempfile
import unittest
from pathlib import Path

from patchpilot.eval_runner import _expected_tests_verified, run_eval
from patchpilot.models import CommandExecution, TestResult
from tests.test_report_writer import _result


class TestExpectedVerification(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "expected_tests.txt").write_text("test_add\ntest_subtract\n")

    def execution(self, output):
        return CommandExecution("tests", 0, output, "", 0, False)

    def test_baseline_test_names_cannot_verify_final_suite(self):
        result = _result("fixed", with_patch=True)
        result.baseline_test = TestResult([self.execution("test_add test_subtract")], False, "failed")
        result.initial_test = TestResult([self.execution("test_add")], True, "passed")
        self.assertFalse(_expected_tests_verified(self.root, result))

    def test_expected_names_must_appear_in_passing_final_output(self):
        result = _result("fixed", with_patch=True)
        result.initial_test = TestResult([self.execution("test_add PASSED\ntest_subtract PASSED")], True, "passed")
        self.assertTrue(_expected_tests_verified(self.root, result))

    def test_empty_evaluation_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "No valid evaluation tasks"):
            run_eval(str(self.root), str(self.root / "output"), engine="heuristic")
