import tempfile
import unittest
from pathlib import Path

from patchpilot.test_runner import detect_test_commands, run_tests


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_tr_"))

    def test_detects_python_test_files(self):
        (self.repo / "calculator.py").write_text("def add(a, b): return a + b\n")
        (self.repo / "test_calculator.py").write_text("import calculator\n")
        commands = detect_test_commands(str(self.repo))
        self.assertEqual(len(commands), 1)
        self.assertTrue(
            "pytest" in commands[0] or "unittest" in commands[0], commands[0]
        )

    def test_detects_npm_test_script(self):
        (self.repo / "package.json").write_text(
            '{"scripts": {"test": "jest"}}'
        )
        self.assertEqual(detect_test_commands(str(self.repo)), ["npm test"])

    def test_ignores_placeholder_npm_test(self):
        (self.repo / "package.json").write_text(
            '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
        )
        self.assertEqual(detect_test_commands(str(self.repo)), [])

    def test_falls_back_to_compileall_for_python(self):
        (self.repo / "main.py").write_text("x = 1\n")
        commands = detect_test_commands(str(self.repo))
        self.assertEqual(len(commands), 1)
        self.assertIn("compileall", commands[0])

    def test_no_command_for_unknown_repo(self):
        (self.repo / "notes.txt").write_text("hello\n")
        self.assertEqual(detect_test_commands(str(self.repo)), [])


class TestRunTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_tr_"))

    def test_passing_command(self):
        result = run_tests(str(self.repo), ["true"])
        self.assertTrue(result.passed)
        self.assertEqual(result.commands[0].exit_code, 0)

    def test_failing_command_captures_output(self):
        result = run_tests(str(self.repo), ["echo boom && exit 3"])
        self.assertFalse(result.passed)
        self.assertEqual(result.commands[0].exit_code, 3)
        self.assertIn("boom", result.commands[0].stdout)

    def test_no_commands_reports_clearly(self):
        result = run_tests(str(self.repo), [])
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "No test command detected.")

    def test_timeout_is_enforced(self):
        result = run_tests(str(self.repo), ["python3 -c 'import time; time.sleep(5)'"], timeout=1)
        self.assertFalse(result.passed)
        self.assertTrue(result.commands[0].timed_out)


if __name__ == "__main__":
    unittest.main()
