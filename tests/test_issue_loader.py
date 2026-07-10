import tempfile
import unittest
from pathlib import Path

from patchpilot.issue_loader import IssueLoadError, load_issue


class TestIssueLoader(unittest.TestCase):
    def _write(self, content: str) -> str:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_loads_markdown_with_heading(self):
        path = self._write("# Bug: add is wrong\n\nThe `add` function subtracts.\n")
        issue = load_issue(path)
        self.assertEqual(issue.title, "Bug: add is wrong")
        self.assertIn("subtracts", issue.body)
        self.assertIn("# Bug", issue.raw_text)

    def test_first_line_used_as_title_without_heading(self):
        path = self._write("something broke\ndetails here\n")
        issue = load_issue(path)
        self.assertEqual(issue.title, "something broke")
        self.assertEqual(issue.body, "details here")

    def test_missing_file_raises_clear_error(self):
        with self.assertRaises(IssueLoadError) as ctx:
            load_issue("/nonexistent/issue.md")
        self.assertIn("not found", str(ctx.exception))

    def test_empty_file_raises_clear_error(self):
        path = self._write("   \n  \n")
        with self.assertRaises(IssueLoadError) as ctx:
            load_issue(path)
        self.assertIn("empty", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
