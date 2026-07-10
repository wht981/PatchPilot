import tempfile
import unittest
from pathlib import Path

from patchpilot.models import Issue
from patchpilot.repo_context import build_repo_context


def _make_issue(text: str) -> Issue:
    return Issue(title=text.splitlines()[0], body=text, raw_text=text, path="i.md")


class TestRepoContext(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="patchpilot_repo_"))
        (self.repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        (self.repo / "test_calculator.py").write_text(
            "from calculator import add\n"
        )
        (self.repo / "README.md").write_text("# demo repo\nA tiny calculator.\n")
        # Directories and files that must be skipped.
        for skip_dir in (".git", "node_modules", "__pycache__", ".venv"):
            d = self.repo / skip_dir
            d.mkdir()
            (d / "junk.py").write_text("x = 1\n")
        (self.repo / "logo.png").write_bytes(b"\x89PNG\r\n")

    def test_skips_vcs_caches_and_binaries(self):
        issue = _make_issue("Bug: `add` returns the wrong result")
        context = build_repo_context(str(self.repo), issue)
        joined = "\n".join(context.file_tree)
        self.assertNotIn(".git", joined)
        self.assertNotIn("node_modules", joined)
        self.assertNotIn("__pycache__", joined)
        self.assertNotIn(".venv", joined)
        self.assertNotIn("logo.png", joined)

    def test_ranks_candidate_files_by_issue_keywords(self):
        issue = _make_issue(
            "# Bug: calculator add function returns the wrong result\n"
            "The `add(a, b)` function should return the sum."
        )
        context = build_repo_context(str(self.repo), issue)
        self.assertIn("calculator.py", context.candidate_files)
        self.assertEqual(context.candidate_files[0], "calculator.py")
        self.assertIn("add", context.keywords)

    def test_detects_tests_readme_and_sources(self):
        issue = _make_issue("Bug: `add` is wrong")
        context = build_repo_context(str(self.repo), issue)
        self.assertIn("test_calculator.py", context.test_files)
        self.assertIn("calculator.py", context.source_files)
        self.assertIn("tiny calculator", context.readme_summary)

    def test_rejects_non_directory(self):
        issue = _make_issue("Bug")
        with self.assertRaises(NotADirectoryError):
            build_repo_context(str(self.repo / "missing"), issue)


if __name__ == "__main__":
    unittest.main()
