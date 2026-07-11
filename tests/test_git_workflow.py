import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from patchpilot.git_workflow import detect_git_state, slugify
from patchpilot.pipeline import run_pipeline


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


class GitRepoFixture(unittest.TestCase):
    """A temp copy of the sample repo, initialized as a git repository."""

    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="patchpilot_git_"))
        self.repo = self.workdir / "repo"
        shutil.copytree(EXAMPLES / "sample_repo", self.repo)
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def _init_git(self):
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.name", "tester")
        _git(self.repo, "config", "user.email", "tester@example.com")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "initial")

    def _run(self, **kwargs):
        return run_pipeline(
            repo_path=str(self.repo),
            issue_path=str(EXAMPLES / "sample_issue.md"),
            output_path=str(self.workdir / "report.md"),
            trace_path=str(self.workdir / "trace.json"),
            **kwargs,
        )


class TestDetectGitState(GitRepoFixture):
    def test_clean_git_repo(self):
        self._init_git()
        state = detect_git_state(str(self.repo))
        self.assertTrue(state.is_repo)
        self.assertTrue(state.is_clean)
        self.assertEqual(state.current_branch, "main")

    def test_dirty_git_repo(self):
        self._init_git()
        (self.repo / "calculator.py").write_text("# dirty\n")
        state = detect_git_state(str(self.repo))
        self.assertTrue(state.is_repo)
        self.assertFalse(state.is_clean)

    def test_plain_directory_is_not_a_repo(self):
        state = detect_git_state(str(self.repo))
        self.assertFalse(state.is_repo)


class TestAutoDelivery(GitRepoFixture):
    def test_fix_lands_on_new_branch_and_main_stays_clean(self):
        self._init_git()
        result = self._run()
        self.assertEqual(result.final_status, "fixed")
        assert result.delivery is not None
        self.assertEqual(result.delivery.mode, "branch")
        self.assertTrue(result.delivery.branch.startswith("patchpilot/fix-"))
        # The fix branch carries the commit; main still has the bug.
        self.assertIn("return a + b", (self.repo / "calculator.py").read_text())
        main_version = _git(self.repo, "show", "main:calculator.py")
        self.assertIn("return a - b", main_version)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_dirty_tree_falls_back_to_in_place(self):
        self._init_git()
        (self.repo / "notes.txt").write_text("wip\n")
        result = self._run()
        assert result.delivery is not None
        self.assertEqual(result.delivery.mode, "in_place")
        self.assertIn("uncommitted changes", result.delivery.note)

    def test_non_git_dir_falls_back_to_in_place(self):
        result = self._run()
        assert result.delivery is not None
        self.assertEqual(result.delivery.mode, "in_place")
        self.assertIn("not the root of a git repository", result.delivery.note)


class TestNoApplyDelivery(GitRepoFixture):
    def test_patch_file_written_and_repo_restored(self):
        self._init_git()
        patch_path = self.workdir / "fix.patch"
        result = self._run(apply_mode="no_apply", patch_file=str(patch_path))
        self.assertEqual(result.final_status, "fixed")
        assert result.delivery is not None
        self.assertEqual(result.delivery.mode, "patch_file")
        # Repo restored to the buggy state, patch applies cleanly.
        self.assertIn("return a - b", (self.repo / "calculator.py").read_text())
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        _git(self.repo, "apply", "--check", str(patch_path))
        _git(self.repo, "apply", str(patch_path))
        self.assertIn("return a + b", (self.repo / "calculator.py").read_text())


class TestSlugify(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(
            slugify("Bug: calculator add function returns the wrong result!"),
            "bug-calculator-add-function-returns-the",
        )
        self.assertEqual(slugify("!!!"), "issue")


if __name__ == "__main__":
    unittest.main()
