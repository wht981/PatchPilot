import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patchpilot.models import Issue
from patchpilot.repo_context import build_repo_context
from patchpilot.security import SecurityPolicy
from patchpilot.workspace import Edit, Workspace, WorkspaceError


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "one.py").write_bytes(b"value = 1\r\n")
        (self.repo / "two.py").write_bytes(b"value = 2\n")
        (self.repo / "test_one.py").write_text("assert True\n")
        (self.repo / "setup.py").write_text("pass\n")
        self.workspace = Workspace(build_repo_context(str(self.repo), Issue("bug", "", "bug", "issue.md")), SecurityPolicy())
        self.workspace.read_file("one.py")
        self.workspace.read_file("two.py")

    def test_batch_edit_and_rollback_preserve_bytes(self):
        transaction = self.workspace.prepare([Edit("one.py", "1", "3"), Edit("two.py", "2", "4")], "fix")
        transaction.apply()
        self.assertEqual((self.repo / "one.py").read_bytes(), b"value = 3\r\n")
        self.assertEqual(len(transaction.changes), 2)
        transaction.revert()
        self.assertEqual((self.repo / "one.py").read_bytes(), b"value = 1\r\n")
        self.assertEqual((self.repo / "two.py").read_text(), "value = 2\n")

    def test_invalid_batch_makes_no_partial_edits(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.prepare([Edit("one.py", "1", "3"), Edit("two.py", "missing", "4")], "fix")
        self.assertEqual((self.repo / "one.py").read_bytes(), b"value = 1\r\n")

    def test_duplicate_and_unread_preimages_are_rejected(self):
        (self.repo / "other.py").write_text("x = 1\n")
        with self.assertRaises(WorkspaceError):
            self.workspace.prepare([Edit("other.py", "1", "2")], "fix")
        (self.repo / "one.py").write_text("value = 1 + 1\n")
        with self.assertRaises(WorkspaceError):
            self.workspace.prepare([Edit("one.py", "1", "2")], "fix")

    def test_creation_rollback_removes_new_files_and_directories(self):
        transaction = self.workspace.prepare([Edit("new/helper.py", "", "value = 3\n")], "fix")
        transaction.apply()
        self.assertTrue((self.repo / "new/helper.py").is_file())
        transaction.revert()
        self.assertFalse((self.repo / "new").exists())

    def test_stale_patch_preserves_concurrent_edits(self):
        transaction = self.workspace.prepare([Edit("one.py", "1", "3")], "fix")
        (self.repo / "one.py").write_text("user change\n")
        with self.assertRaises(WorkspaceError):
            transaction.apply()
        self.assertEqual((self.repo / "one.py").read_text(), "user change\n")

    def test_write_error_restores_already_written_files(self):
        transaction = self.workspace.prepare([Edit("one.py", "1", "3"), Edit("two.py", "2", "4")], "fix")
        original_write = Path.write_bytes

        def fail_on_second(path, content):
            if path.name == "two.py" and content == b"value = 4\n":
                raise OSError("write failed")
            return original_write(path, content)

        with patch.object(Path, "write_bytes", fail_on_second), self.assertRaises(OSError):
            transaction.apply()
        self.assertEqual((self.repo / "one.py").read_bytes(), b"value = 1\r\n")

    def test_paths_secrets_symlinks_and_test_edits_are_rejected(self):
        (self.root / "outside.py").write_text("secret\n")
        (self.repo / "alias.py").symlink_to(self.root / "outside.py")
        (self.repo / "loop").symlink_to(self.repo, target_is_directory=True)
        (self.repo / ".env").write_text("TOKEN=private\n")
        context = build_repo_context(str(self.repo), Issue("bug", "", "bug", "i.md"))
        self.assertNotIn("alias.py", context.file_tree)
        self.assertNotIn(".env", context.file_tree)
        for relative in ("../outside.py", "/tmp/file", ".git/config", ".env", "alias.py", "loop/one.py", "one//file.py", "x\n.py"):
            with self.subTest(relative=relative), self.assertRaises(WorkspaceError):
                self.workspace.read_file(relative)
        for relative in ("test_one.py", "tests/helper.py", "one.spec.ts", "setup.py", ".github/workflow.py",
                         "nested/setup.py", "pytest.py", "unittest/__init__.py", "sitecustomize.py"):
            with self.subTest(relative=relative), self.assertRaises(WorkspaceError):
                self.workspace.prepare([Edit(relative, "", "pass\n")], "fix")

    def test_search_returns_literal_matches_and_read_redacts_credentials(self):
        (self.repo / "one.py").write_text('api_key = "sk-test-secret012345"\nvalue = 1\n')
        (self.repo / ".env").write_text("value = private\n")
        result = self.workspace.search("value")
        self.assertEqual({m["path"] for m in result["matches"]}, {"one.py", "two.py"})
        self.assertNotIn("sk-test-secret012345", self.workspace.read_file("one.py")["content"])

    def test_read_bounds_and_large_files_are_enforced(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.read_file("one.py", 1, 201)
        (self.repo / "huge.py").write_bytes(b"x" * 250_001)
        with self.assertRaises(WorkspaceError):
            self.workspace.read_file("huge.py")

    def test_diff_applies_new_file_and_missing_trailing_newline(self):
        (self.repo / "one.py").write_bytes(b"value = 1")
        transaction = self.workspace.prepare([Edit("one.py", "1", "3"), Edit("new.py", "", "value = 2")], "fix")
        patch_file = self.root / "fix.patch"
        patch_file.write_text("".join(c.diff for c in transaction.changes))
        subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(patch_file)], cwd=self.repo, check=True, capture_output=True)
        self.assertEqual((self.repo / "one.py").read_bytes(), b"value = 3")
        self.assertEqual((self.repo / "new.py").read_bytes(), b"value = 2")
