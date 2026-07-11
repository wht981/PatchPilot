import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from patchpilot.cli import main


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class CliFixture(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="patchpilot_cli_"))
        self.repo = self.workdir / "repo"
        shutil.copytree(EXAMPLES / "sample_repo", self.repo)
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def _run_cli(self, *extra):
        argv = [
            "run",
            "--repo", str(self.repo),
            "--issue", str(EXAMPLES / "sample_issue.md"),
            "--output", str(self.workdir / "report.md"),
            "--trace", str(self.workdir / "trace.json"),
            *extra,
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()


class TestJsonOutput(CliFixture):
    def test_json_summary_on_success(self):
        code, stdout, _ = self._run_cli("--json")
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["final_status"], "fixed")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["changed_files"][0]["path"], "calculator.py")
        self.assertIn("return a + b", payload["diff"])
        self.assertIn("baseline", payload["tests"])
        self.assertIn("final", payload["tests"])
        self.assertEqual(payload["delivery"]["mode"], "in_place")
        self.assertEqual(payload["report_path"], str(self.workdir / "report.md"))

    def test_json_is_the_only_stdout(self):
        _, stdout, _ = self._run_cli("--json")
        json.loads(stdout)  # must parse as a single JSON document


class TestExitCodes(CliFixture):
    def test_exit_zero_on_fix(self):
        code, _, _ = self._run_cli()
        self.assertEqual(code, 0)

    def test_exit_one_when_not_fixable(self):
        # An unfixable failure: the test demands behavior no single-token
        # mutation of `add` can produce.
        (self.repo / "test_calculator.py").write_text(
            "import unittest\n"
            "from calculator import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 9999)\n"
        )
        code, _, _ = self._run_cli()
        self.assertEqual(code, 1)

    def test_exit_two_on_missing_issue(self):
        code, _, stderr = self._run_cli()
        self.assertEqual(code, 0)  # sanity: fixture works
        argv = ["run", "--repo", str(self.repo), "--issue", "/missing.md"]
        stdout, stderr_io = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr_io):
            code = main(argv)
        self.assertEqual(code, 2)
        self.assertIn("patchpilot error", stderr_io.getvalue())


class TestEvalJson(unittest.TestCase):
    def test_eval_json_output(self):
        outdir = Path(tempfile.mkdtemp(prefix="patchpilot_evalcli_"))
        self.addCleanup(shutil.rmtree, outdir, True)
        argv = [
            "eval",
            "--tasks", str(EXAMPLES / "eval_tasks"),
            "--output-dir", str(outdir),
            "--json",
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], payload["passed"])
        self.assertGreaterEqual(payload["total"], 4)


if __name__ == "__main__":
    unittest.main()
