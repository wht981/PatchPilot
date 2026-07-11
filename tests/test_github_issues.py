import unittest
from unittest import mock

from patchpilot.issue_loader import GITHUB_ISSUE_URL, IssueLoadError, load_issue


ISSUE_PAYLOAD = {
    "title": "calculator add returns the wrong result",
    "body": "The `add(a, b)` function subtracts instead of adding.",
}


class TestUrlDetection(unittest.TestCase):
    def test_matches_issue_urls(self):
        for url in (
            "https://github.com/wht981/PatchPilot/issues/1",
            "http://github.com/owner/repo/issues/42/",
        ):
            self.assertIsNotNone(GITHUB_ISSUE_URL.match(url), url)

    def test_rejects_non_issue_urls(self):
        for url in (
            "https://github.com/owner/repo/pull/7",
            "https://gitlab.com/owner/repo/issues/1",
            "https://github.com/owner/repo",
            "./examples/sample_issue.md",
        ):
            self.assertIsNone(GITHUB_ISSUE_URL.match(url), url)


class TestGithubIssueLoading(unittest.TestCase):
    @mock.patch("patchpilot.issue_loader._fetch_json", return_value=ISSUE_PAYLOAD)
    def test_loads_issue_from_url(self, fetch):
        url = "https://github.com/owner/repo/issues/12"
        issue = load_issue(url)
        fetch.assert_called_once_with(
            "https://api.github.com/repos/owner/repo/issues/12"
        )
        self.assertEqual(issue.title, ISSUE_PAYLOAD["title"])
        self.assertIn("subtracts", issue.body)
        self.assertEqual(issue.path, url)
        # raw_text feeds keyword extraction: code spans must survive.
        self.assertIn("`add(a, b)`", issue.raw_text)

    @mock.patch(
        "patchpilot.issue_loader._fetch_json",
        return_value={"title": "t", "body": None, "pull_request": {}},
    )
    def test_rejects_pull_request_urls(self, fetch):
        with self.assertRaises(IssueLoadError) as ctx:
            load_issue("https://github.com/owner/repo/issues/3")
        self.assertIn("pull request", str(ctx.exception))

    @mock.patch(
        "patchpilot.issue_loader._fetch_json",
        return_value={"title": "Just a title", "body": None},
    )
    def test_handles_empty_body(self, fetch):
        issue = load_issue("https://github.com/owner/repo/issues/9")
        self.assertEqual(issue.title, "Just a title")
        self.assertEqual(issue.body, "")

    def test_local_paths_still_work(self):
        with self.assertRaises(IssueLoadError):
            load_issue("/nonexistent/issue.md")


if __name__ == "__main__":
    unittest.main()
