import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchpilot.cli import build_parser, main
from patchpilot.llm import LLMConfig, LLMError
from patchpilot.models import TestResult
from patchpilot.pipeline import run_pipeline
from tests.llm_support import ScriptedClient, model_server, patch_action, plan_action, read_action


TASK = Path(__file__).resolve().parent.parent / "examples/llm_tasks/task_001"
EDITS = [
    {"path": "names.py", "old_text": "return value.strip()", "new_text": 'return " ".join(value.split()).casefold()'},
    {"path": "greeting.py", "old_text": 'return "hello" + normalize_name(name)', "new_text": 'return "Hello, " + normalize_name(name) + "!"'},
]


def actions():
    return [read_action("names.py"), read_action("greeting.py"), read_action("test_greeting.py"),
            plan_action(["names.py", "greeting.py"]), patch_action(EDITS)]


class TestLLMPipeline(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = self.root / "repo"
        shutil.copytree(TASK / "repo", self.repo)
        self.report = self.root / "report.md"
        self.trace = self.root / "trace.json"
        self.originals = {p.name: p.read_bytes() for p in self.repo.iterdir()}

    def run_agent(self, script=None, **kwargs):
        return run_pipeline(repo_path=str(self.repo), issue_path=str(TASK / "issue.md"),
                            output_path=str(self.report), trace_path=str(self.trace),
                            model_client=ScriptedClient(actions() if script is None else script), **kwargs)

    def assert_restored(self):
        for name, raw in self.originals.items():
            self.assertEqual((self.repo / name).read_bytes(), raw)

    def test_default_engine_repairs_two_files_with_real_tests(self):
        result = self.run_agent()
        self.assertEqual(result.engine, "llm")
        self.assertEqual(result.final_status, "fixed")
        self.assertFalse(result.baseline_test.passed)
        self.assertTrue(result.initial_test.passed)
        self.assertEqual({c.path for c in result.kept_changes}, {"names.py", "greeting.py"})
        self.assertEqual(result.model_usage.calls, 5)
        self.assertEqual(result.model_usage.input_tokens, 50)
        self.assertEqual((self.repo / "test_greeting.py").read_bytes(), self.originals["test_greeting.py"])
        report = self.report.read_text()
        self.assertIn("+++ b/names.py", report)
        self.assertIn("+++ b/greeting.py", report)
        self.assertIn("Model calls: 5", report)

    def test_failed_batch_is_reverted_before_feedback_retry(self):
        client = ScriptedClient(actions()[:-1] + [patch_action(EDITS[:1]), patch_action(EDITS)])
        complete = client.complete

        def check_retry(messages):
            if len(client.requests) == 5:
                self.assert_restored()
                self.assertIn("test_greet", messages[-1]["content"])
                self.assertIn("reverted", messages[-1]["content"])
            return complete(messages)

        client.complete = check_retry
        result = run_pipeline(str(self.repo), str(TASK / "issue.md"), str(self.report), str(self.trace), model_client=client)
        self.assertEqual(result.final_status, "fixed")
        self.assertFalse(result.initial_test.passed)
        self.assertTrue(result.debug_rounds[0].test.passed)
        self.assertEqual(len(result.kept_changes), 2)

    def test_exhausted_repair_rounds_revert_every_file(self):
        result = self.run_agent(actions()[:-1] + [patch_action(EDITS[:1])], max_debug_rounds=0)
        self.assertEqual(result.final_status, "not_fixed")
        self.assertFalse(result.kept_changes)
        self.assert_restored()

    def test_budget_and_invalid_model_actions_are_bounded(self):
        result = self.run_agent(["not JSON", {"action": "run_command", "command": "touch escaped"}], max_model_calls=2)
        self.assertEqual(result.final_status, "model_error")
        self.assertEqual(result.model_usage.calls, 2)
        self.assertIn("budget", result.error)
        self.assert_restored()
        self.assertFalse((self.repo / "escaped").exists())

    def test_provider_error_after_failed_attempt_leaves_originals(self):
        result = self.run_agent(actions()[:-1] + [patch_action(EDITS[:1]), LLMError("network down")])
        self.assertEqual(result.final_status, "model_error")
        self.assertEqual(result.error, "network down")
        self.assert_restored()
        self.assertIn("Model error", self.report.read_text())

    def test_test_edits_rejected_then_corrected(self):
        invalid = patch_action([{"path": "test_greeting.py", "old_text": "assertEqual", "new_text": "assertNotEqual"}])
        result = self.run_agent(actions()[:-1] + [invalid, patch_action(EDITS)])
        self.assertEqual(result.final_status, "fixed")
        self.assertEqual(result.model_usage.calls, 6)
        self.assertIn("agent_action_rejected", self.trace.read_text())

    def test_dry_run_uses_model_plan_without_tests_or_edits(self):
        result = self.run_agent(actions()[:-1], dry_run=True)
        self.assertEqual(result.final_status, "dry_run")
        self.assertEqual(result.plan.candidate_files, ["names.py", "greeting.py"])
        self.assertFalse(result.all_commands)
        self.assert_restored()

    def test_no_apply_delivers_both_files_and_restores_workspace(self):
        patch_file = self.root / "fix.patch"
        result = self.run_agent(apply_mode="no_apply", patch_file=str(patch_file))
        self.assertEqual(result.final_status, "fixed")
        self.assertEqual(result.delivery.mode, "patch_file")
        self.assert_restored()
        subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=self.repo, check=True, capture_output=True)
        self.assertIn("+++ b/names.py", patch_file.read_text())
        self.assertIn("+++ b/greeting.py", patch_file.read_text())

    def test_no_apply_write_failure_still_restores_workspace(self):
        with self.assertRaises(OSError):
            self.run_agent(apply_mode="no_apply", patch_file=str(self.root / "missing/fix.patch"))
        self.assert_restored()

    def test_verification_exception_and_interrupt_revert_batch(self):
        for error in (RuntimeError("test failed to start"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch("patchpilot.pipeline.run_tests", side_effect=[
                    TestResult([], False, "baseline failing"), error]), self.assertRaises(type(error)):
                self.run_agent()
            self.assert_restored()

    def test_already_passing_skips_model_calls(self):
        with patch("patchpilot.pipeline.run_tests", return_value=TestResult([], True, "passed")):
            result = self.run_agent([])
        self.assertEqual(result.final_status, "already_passing")
        self.assertEqual(result.model_usage.calls, 0)

    def test_compile_check_is_insufficient_for_llm_repair(self):
        (self.repo / "test_greeting.py").unlink()
        result = self.run_agent([])
        self.assertEqual(result.final_status, "no_tests")
        self.assertEqual(result.model_usage.calls, 0)

    def test_git_delivery_commits_whole_batch_and_quotes_issue_title(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

        git("init", "-q", "-b", "main")
        git("config", "user.name", "tester")
        git("config", "user.email", "tester@example.com")
        git("add", ".")
        git("commit", "-q", "-m", "initial")
        issue = self.root / "issue.md"
        issue.write_text('# Fix $(touch injected) and `touch injected2`\nRepair names and greeting.\n')
        result = run_pipeline(str(self.repo), str(issue), str(self.report), str(self.trace), model_client=ScriptedClient(actions()))
        self.assertEqual(result.delivery.mode, "branch")
        self.assertEqual(git("status", "--porcelain"), "")
        self.assertEqual(git("show", "main:names.py").encode(), self.originals["names.py"])
        self.assertIn("casefold", git("show", "HEAD:names.py"))
        self.assertIn("Hello, ", git("show", "HEAD:greeting.py"))
        self.assertFalse((self.repo / "injected").exists())
        self.assertFalse((self.repo / "injected2").exists())

    def test_real_http_adapter_and_cli_run_complete_repair(self):
        with model_server(actions()) as (url, state):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["run", "--repo", str(self.repo), "--issue", str(TASK / "issue.md"),
                             "--model", "local-test-model", "--base-url", url,
                             "--output", str(self.report), "--trace", str(self.trace), "--json"])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["final_status"], "fixed")
        self.assertEqual(len(summary["changed_files"]), 2)
        self.assertEqual(len(state["requests"]), 5)
        self.assertIn("baseline_failure", state["requests"][0]["messages"][1]["content"])

    def test_missing_model_is_clear_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = main(["run", "--repo", str(self.repo), "--issue", str(TASK / "issue.md")])
        self.assertEqual(code, 2)
        self.assertIn("PATCHPILOT_MODEL", stderr.getvalue())
        self.assert_restored()

    def test_credentials_are_removed_from_prompts_artifacts_and_test_environment(self):
        key = "opaque-secret-credential"
        issue = self.root / "issue.md"
        issue.write_text(f"# Name normalization {key}\nFix both modules.\n")
        test_path = self.repo / "test_greeting.py"
        test_path.write_text(test_path.read_text() + '\nclass CredentialTests(unittest.TestCase):\n'
                             '    def test_credential_not_inherited(self):\n'
                             '        import os\n'
                             '        self.assertNotIn("CUSTOM_LLM_KEY", os.environ)\n')
        with model_server(actions()) as (url, state), patch.dict(os.environ, {"CUSTOM_LLM_KEY": key}):
            result = run_pipeline(str(self.repo), str(issue), str(self.report), str(self.trace),
                                  llm_config=LLMConfig("test", url, key, api_key_env="CUSTOM_LLM_KEY"))
        self.assertEqual(result.final_status, "fixed")
        for text in (json.dumps(state["requests"]), self.report.read_text(), self.trace.read_text()):
            self.assertNotIn(key, text)

    def test_eval_uses_llm_engine_and_records_usage(self):
        with model_server(actions()) as (url, state), contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["eval", "--tasks", str(TASK.parent), "--output-dir", str(self.root / "eval"),
                         "--model", "test", "--base-url", url, "--json"])
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())["results"][0]
        self.assertEqual(result["files_changed"], 2)
        self.assertEqual(result["model_calls"], 5)
        self.assertEqual(result["input_tokens"], 50)
        self.assertTrue(result["expected_tests_verified"])
        self.assert_restored()

    def test_cli_default_is_llm_for_run_and_eval(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["run", "--repo", ".", "--issue", "i.md"]).engine, "llm")
        self.assertEqual(parser.parse_args(["eval", "--tasks", "."]).engine, "llm")
