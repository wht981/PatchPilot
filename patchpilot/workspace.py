"""Repository read tools and transactional, exact-match multi-file patches."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set

from patchpilot.models import FileChange, RepoContext
from patchpilot.repo_context import CONFIG_FILE_NAMES, SKIP_DIRS, _is_test_file
from patchpilot.security import SecurityPolicy, is_sensitive_path


MAX_FILE_BYTES = 250_000
SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".kt", ".swift"}


class WorkspaceError(ValueError):
    """A model tool or patch violated a workspace constraint."""


@dataclass(frozen=True)
class Edit:
    path: str
    old_text: str
    new_text: str


class PatchTransaction:
    def __init__(self, workspace: "Workspace", originals: Dict[str, Optional[bytes]],
                 contents: Dict[str, bytes], reason: str) -> None:
        self.workspace, self.originals, self.contents = workspace, originals, contents
        self.created_dirs: List[Path] = []
        self._written: List[str] = []
        self.changes = []
        for relative, content in contents.items():
            original = originals[relative]
            diff_lines = difflib.unified_diff(
                (original or b"").decode("utf-8").splitlines(keepends=True),
                content.decode("utf-8").splitlines(keepends=True),
                fromfile=f"a/{relative}" if original is not None else "/dev/null",
                tofile=f"b/{relative}",
            )
            diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff_lines)
            self.changes.append(FileChange(relative, reason, diff))

    def apply(self) -> None:
        # A stale preimage must never overwrite somebody else's newer edit.
        for relative, original in self.originals.items():
            path = self.workspace.resolve(relative)
            current = path.read_bytes() if path.is_file() else None
            if current != original:
                raise WorkspaceError(f"File changed since patch validation: {relative}")
        try:
            for relative, content in self.contents.items():
                path = self.workspace.resolve(relative)
                missing = []
                parent = path.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    self.created_dirs.append(directory)
                self._written.append(relative)
                path.write_bytes(content)
        except BaseException:
            self.revert()
            raise

    def revert(self) -> None:
        for relative in reversed(self._written):
            original = self.originals[relative]
            path = self.workspace.resolve(relative)
            if original is None:
                if path.is_file():
                    path.unlink()
            else:
                path.write_bytes(original)
        for directory in reversed(self.created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        self.created_dirs.clear()
        self._written.clear()


class Workspace:
    def __init__(self, context: RepoContext, policy: SecurityPolicy) -> None:
        self.repo = Path(context.repo_path).resolve()
        self.context, self.policy = context, policy
        self.inspected: Set[str] = set()

    def resolve(self, relative: str) -> Path:
        if (not isinstance(relative, str) or not relative or "\\" in relative
                or any(c in relative for c in "\n\r\0")
                or PurePosixPath(relative).is_absolute()
                or str(PurePosixPath(relative)) != relative
                or any(p in ("..", ".") for p in relative.split("/"))
                or any(p in SKIP_DIRS for p in PurePosixPath(relative).parts)
                or is_sensitive_path(relative)):
            raise WorkspaceError("Refusing unsafe or sensitive repository path.")
        path = self.repo
        for part in PurePosixPath(relative).parts:
            path = path / part
            if path.is_symlink():
                raise WorkspaceError("Symlink paths are unavailable to the model.")
        try:
            path.resolve().relative_to(self.repo)
        except ValueError:
            raise WorkspaceError("Path must remain inside the repository.") from None
        return path

    def _content(self, relative: str) -> bytes:
        path = self.resolve(relative)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError("Read requires a text file no larger than 250 KB.")
        raw = path.read_bytes()
        if b"\0" in raw:
            raise WorkspaceError("Binary files are unavailable to the model.")
        try:
            raw.decode("utf-8")
        except UnicodeError:
            raise WorkspaceError("File must contain UTF-8 text.") from None
        return raw

    def read_file(self, path: str, start_line: int = 1, end_line: int = 200) -> dict:
        if (type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or end_line < start_line or end_line - start_line >= 200):
            raise WorkspaceError("read_file accepts a 1-based range of at most 200 lines.")
        lines = self._content(path).decode("utf-8").splitlines(keepends=True)
        content = "".join(lines[start_line - 1:end_line])
        if len(content) > 20_000:
            raise WorkspaceError("Read range exceeds 20,000 characters; request fewer lines.")
        self.inspected.add(path)
        return {"path": path, "start_line": start_line, "total_lines": len(lines),
                "content": self.policy.mask_secrets(content)}

    def search(self, query: str) -> dict:
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise WorkspaceError("search requires a literal query of 1-200 characters.")
        matches = []
        for relative in self.context.file_tree:
            try:
                content = self._content(relative).decode("utf-8")
            except (WorkspaceError, OSError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query.lower() in line.lower():
                    matches.append({"path": relative, "line": number,
                                    "text": self.policy.mask_secrets(line[:300])})
                    if len(matches) >= 30:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def prepare(self, edits: List[Edit], reason: str) -> PatchTransaction:
        if not 1 <= len(edits) <= 20:
            raise WorkspaceError("A patch must contain 1-20 edits.")
        originals: Dict[str, Optional[bytes]] = {}
        contents: Dict[str, bytes] = {}
        for edit in edits:
            path = self.resolve(edit.path)
            if (path.suffix.lower() not in SOURCE_EXTENSIONS
                    or _is_test_file(edit.path) or path.name == "conftest.py"
                    or edit.path in self.context.config_files
                    or path.name in CONFIG_FILE_NAMES
                    or path.name in ("pytest.py", "unittest.py", "sitecustomize.py", "usercustomize.py")
                    or any(p in ("pytest", "unittest") for p in PurePosixPath(edit.path).parts)
                    or any(p.startswith(".") for p in PurePosixPath(edit.path).parts)):
                raise WorkspaceError("Only source files may be edited; tests, configuration and hidden paths are read-only.")
            if edit.path not in originals:
                if path.exists():
                    if edit.path not in self.inspected:
                        raise WorkspaceError(f"Read the source file before editing it: {edit.path}")
                    originals[edit.path] = self._content(edit.path)
                else:
                    originals[edit.path] = None
                contents[edit.path] = originals[edit.path] or b""
            if not isinstance(edit.old_text, str) or not isinstance(edit.new_text, str):
                raise WorkspaceError("old_text and new_text must be strings.")
            content = contents[edit.path].decode("utf-8")
            if originals[edit.path] is None and not content and not edit.old_text:
                content = edit.new_text
            elif not edit.old_text or content.count(edit.old_text) != 1:
                raise WorkspaceError(f"old_text must match exactly once: {edit.path}")
            else:
                content = content.replace(edit.old_text, edit.new_text, 1)
            raw = content.encode("utf-8")
            if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
                raise WorkspaceError("Edited file exceeds the text-file size limit.")
            contents[edit.path] = raw
        if len(contents) > 8:
            raise WorkspaceError("A patch may change at most 8 files.")
        if any(content == (originals[path] or b"") for path, content in contents.items()):
            raise WorkspaceError("Every proposed file must have a non-empty change.")
        return PatchTransaction(self, originals, contents, self.policy.mask_secrets(reason))
